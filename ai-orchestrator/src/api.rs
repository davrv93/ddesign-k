//! API OpenAI-compatible y API de administración (la que usa la UI).
//!
//! ```text
//! POST /v1/chat/completions   model = auto | <agente> | <modelo>   (stream incluido)
//! GET  /v1/models             perfiles, agentes y modelos
//! POST /v1/route              dry-run: qué agente y qué cadena tocarían, sin llamar a nadie
//! POST /v1/feedback           {request_id, quality: 0..1} → alimenta el aprendizaje
//! GET  /health
//!
//! /admin/api/*                configuración, cuotas, estado, peticiones, playground (ADMIN_TOKEN)
//! /ui/                        la UI de gestión
//! ```
//! Pistas opcionales: en el cuerpo (`"orchestrator": {"agent": "...", "priority": "speed",
//! "judge": false}`) o en cabeceras (`X-Orchestrator-Agent`, `X-Orchestrator-Priority`).

#![allow(clippy::result_large_err)] // las respuestas de error son el Err de los guardas

use crate::config::{AgentSpec, Config, ModelSpec, Priority, Provider};
use crate::engine::{prepare_body, trunc, Attempt, CallCtx, Engine, Failure, Hints, PlanError, Reply, RoutePlan};
use crate::quotas::probe_balance;
use crate::reliability::now;
use crate::scorer::{auto_quality, quality_of_body};
use crate::telemetry::Row;
use axum::body::{Body, Bytes};
use axum::extract::{Path, Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use futures::StreamExt;
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::{Duration, Instant};

type AppState = Arc<Engine>;

pub fn router(engine: AppState) -> Router {
    Router::new()
        .route("/", get(|| async { Redirect::temporary("/ui/") }))
        .route("/ui", get(|| async { Redirect::temporary("/ui/") }))
        .route("/ui/", get(ui_index))
        .route("/ui/app.js", get(ui_js))
        .route("/ui/style.css", get(ui_css))
        .route("/health", get(health))
        .route("/v1/models", get(models))
        .route("/v1/chat/completions", post(chat))
        .route("/v1/route", post(route))
        .route("/v1/feedback", post(feedback))
        .route("/admin/api/config", get(get_config).put(put_config))
        .route("/admin/api/config.yaml", get(get_config_yaml).put(put_config_yaml))
        .route("/admin/api/providers/:name", put(put_provider).delete(delete_provider))
        .route("/admin/api/providers/:name/models", post(provider_models))
        .route("/admin/api/providers/:name/balance", post(provider_balance))
        .route("/admin/api/models/:name", put(put_model).delete(delete_model))
        .route("/admin/api/models/:name/reset", post(reset_model))
        .route("/admin/api/agents/:name", put(put_agent).delete(delete_agent))
        .route("/admin/api/quotas", get(quotas))
        .route("/admin/api/status", get(status))
        .route("/admin/api/requests", get(requests))
        .route("/admin/api/playground", post(playground))
        .route("/admin/api/route", post(admin_route))
        .with_state(engine)
}

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------

fn error(status: StatusCode, message: impl Into<String>, kind: &str, extra: Value) -> Response {
    let mut err = json!({"message": message.into(), "type": kind});
    if let (Some(o), Value::Object(x)) = (err.as_object_mut(), extra) {
        o.extend(x);
    }
    (status, Json(json!({ "error": err }))).into_response()
}

fn bearer(headers: &HeaderMap) -> String {
    let h = headers.get(header::AUTHORIZATION).and_then(|v| v.to_str().ok()).unwrap_or("");
    if h.len() > 7 && h[..7].eq_ignore_ascii_case("bearer ") {
        return h[7..].trim().to_string();
    }
    headers.get("x-api-key").and_then(|v| v.to_str().ok()).unwrap_or("").to_string()
}

fn ct_eq(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

fn gateway_auth(e: &Engine, headers: &HeaderMap) -> Result<(), Response> {
    if e.gateway_keys.is_empty() {
        return Ok(());
    }
    let t = bearer(headers);
    if e.gateway_keys.iter().any(|k| ct_eq(k, &t)) {
        Ok(())
    } else {
        Err(error(StatusCode::UNAUTHORIZED, "clave inválida", "auth", json!({})))
    }
}

fn admin_auth(e: &Engine, headers: &HeaderMap) -> Result<(), Response> {
    if ct_eq(&e.admin_token, &bearer(headers)) {
        Ok(())
    } else {
        Err(error(StatusCode::UNAUTHORIZED, "token de administración inválido", "auth", json!({})))
    }
}

macro_rules! guard {
    ($r:expr) => {
        if let Err(resp) = $r {
            return resp;
        }
    };
}

fn parse_body(raw: &Bytes) -> Result<Value, Response> {
    let body: Value = serde_json::from_slice(raw)
        .map_err(|_| error(StatusCode::BAD_REQUEST, "JSON inválido", "invalid_request_error", json!({})))?;
    if !body["messages"].as_array().is_some_and(|m| !m.is_empty()) {
        return Err(error(StatusCode::BAD_REQUEST, "falta messages", "invalid_request_error", json!({})));
    }
    Ok(body)
}

fn hints(headers: &HeaderMap, body: &Value) -> Result<Hints, Response> {
    let o = &body["orchestrator"];
    let hv = |k: &str| headers.get(k).and_then(|v| v.to_str().ok()).map(String::from);
    let agent = hv("x-orchestrator-agent").or_else(|| o["agent"].as_str().map(String::from));
    let priority = match hv("x-orchestrator-priority").or_else(|| o["priority"].as_str().map(String::from)) {
        None => None,
        Some(p) => Some(Priority::parse(&p).ok_or_else(|| {
            error(StatusCode::BAD_REQUEST, "priority: quality | speed | cost", "invalid_request_error", json!({}))
        })?),
    };
    Ok(Hints { agent, priority, judge: o["judge"].as_bool().unwrap_or(true) })
}

fn plan_error(e: PlanError) -> Response {
    match e {
        PlanError::UnknownProfile(p) => {
            error(StatusCode::NOT_FOUND, format!("modelo o perfil desconocido: {p}"), "model_not_found", json!({}))
        }
        PlanError::NoCapableModel(m) => error(StatusCode::UNPROCESSABLE_ENTITY, m, "no_capable_model", json!({})),
    }
}

fn failure_response(e: &Engine, f: Failure, chain: &[String]) -> Response {
    match f {
        Failure::NoFallback(err, _) => {
            let status = err.status.and_then(|s| StatusCode::from_u16(s).ok())
                .filter(|s| s.is_client_error() || s.is_server_error())
                .unwrap_or(StatusCode::BAD_REQUEST);
            error(status, trunc(&err.message, 2000), err.kind.as_str(), json!({}))
        }
        Failure::AllFailed(attempts, last) => {
            let kinds: Vec<String> = attempts.iter().map(|a| a.skipped.clone().or(a.kind.clone()).unwrap_or_default()).collect();
            let limited = !kinds.is_empty() && kinds.iter().all(|k| {
                matches!(k.as_str(), "rate_limit" | "quota" | "cooldown" | "circuit_open" | "no_quota")
            });
            let msg = format!("todos los modelos fallaron ({})",
                attempts.iter().map(|a| format!("{}:{}", a.model, a.skipped.clone().or(a.kind.clone()).unwrap_or_default()))
                    .collect::<Vec<_>>().join(", "));
            let mut resp = error(if limited { StatusCode::TOO_MANY_REQUESTS } else { StatusCode::SERVICE_UNAVAILABLE },
                msg, "all_models_failed", json!({"attempts": attempts, "last_error": last.map(|l| l.to_string())}));
            if limited {
                let t = now();
                let wait = chain.iter().filter_map(|m| {
                    let cd = e.cooldowns.get(m, t).map(|c| c.until);
                    let cb = e.breaker.state(m, t).1;
                    cd.into_iter().chain(cb).reduce(f64::max)
                }).fold(f64::INFINITY, f64::min);
                if wait.is_finite() {
                    let secs = ((wait - t).ceil() as i64).max(1);
                    resp.headers_mut().insert(header::RETRY_AFTER, HeaderValue::from(secs));
                }
            }
            resp
        }
    }
}

fn meta(rid: &str, plan: &RoutePlan, model: &str, provider: &str, upstream_model: &str, attempts: &[Attempt]) -> Value {
    json!({
        "request_id": rid, "agent": plan.task(), "model": model, "provider": provider,
        "upstream_model": upstream_model, "route": plan.decision, "priority": plan.priority,
        "chain": plan.chain, "attempts": attempts,
    })
}

fn meta_headers(h: &mut HeaderMap, rid: &str, plan: &RoutePlan, model: &str, attempts: &[Attempt]) {
    let mut put = |k: &'static str, v: String| {
        if let Ok(v) = HeaderValue::from_str(&v) {
            h.insert(k, v);
        }
    };
    put("x-orchestrator-request-id", rid.into());
    put("x-orchestrator-agent", plan.task());
    put("x-orchestrator-model", model.into());
    put("x-orchestrator-route-source", plan.decision.source.clone());
    put("x-orchestrator-attempts", attempts.iter().filter(|a| a.skipped.is_none()).count().to_string());
}

// ---------------------------------------------------------------------------
// /v1
// ---------------------------------------------------------------------------

async fn health() -> Json<Value> {
    Json(json!({"ok": true}))
}

async fn models(State(e): State<AppState>, headers: HeaderMap) -> Response {
    guard!(gateway_auth(&e, &headers));
    let cfg = e.cfg();
    let created = now() as i64;
    let mut data = vec![json!({"id": "auto", "object": "model", "created": created, "owned_by": "ai-orchestrator",
        "description": "El router elige agente y modelo"})];
    for (n, a) in &cfg.agents {
        data.push(json!({"id": n, "object": "model", "created": created, "owned_by": "ai-orchestrator",
            "description": a.description, "chain": a.chain}));
    }
    for (n, m) in &cfg.models {
        data.push(json!({"id": n, "object": "model", "created": created, "owned_by": "upstream",
            "capabilities": m.capabilities, "context": m.context}));
    }
    Json(json!({"object": "list", "data": data})).into_response()
}

async fn route(State(e): State<AppState>, headers: HeaderMap, raw: Bytes) -> Response {
    guard!(gateway_auth(&e, &headers));
    do_route(&e, &headers, &raw).await
}

async fn do_route(e: &Engine, headers: &HeaderMap, raw: &Bytes) -> Response {
    let body = match parse_body(raw) { Ok(b) => b, Err(r) => return r };
    let h = match hints(headers, &body) { Ok(h) => h, Err(r) => return r };
    let cfg = e.cfg();
    match e.plan(&cfg, &body, body["model"].as_str(), &h).await {
        Ok(plan) => Json(json!(plan)).into_response(),
        Err(err) => plan_error(err),
    }
}

async fn chat(State(e): State<AppState>, headers: HeaderMap, raw: Bytes) -> Response {
    guard!(gateway_auth(&e, &headers));
    let body = match parse_body(&raw) { Ok(b) => b, Err(r) => return r };
    handle_chat(e, &headers, body).await
}

async fn handle_chat(e: AppState, headers: &HeaderMap, body: Value) -> Response {
    let h = match hints(headers, &body) { Ok(h) => h, Err(r) => return r };
    let cfg = e.cfg();
    let plan = match e.plan(&cfg, &body, body["model"].as_str(), &h).await {
        Ok(p) => p,
        Err(err) => return plan_error(err),
    };
    let stream = body["stream"].as_bool().unwrap_or(false);
    let agent_spec = plan.agent.as_ref().and_then(|a| cfg.agents.get(a));
    let payload = prepare_body(&body, agent_spec);
    let per_try = Duration::from_secs_f64(agent_spec.and_then(|a| a.timeout).unwrap_or(cfg.reliability.default_timeout));
    let deadline = Duration::from_secs_f64(cfg.reliability.deadline);
    let rid = uuid::Uuid::new_v4().simple().to_string();
    let ctx = CallCtx { request_id: rid.clone(), profile: plan.profile.clone(), agent: plan.task(),
        bucket: Some(plan.decision.bucket()), source: plan.decision.source.clone(), stream };

    let res = e.run(&cfg, &plan.chain, per_try, deadline, |m, t| {
        let (cfg, payload, ctx, e) = (&cfg, &payload, &ctx, &e);
        async move { e.call_group(cfg, &m, payload, t, stream, ctx).await }
    }).await;
    let out = match res {
        Ok(o) => o,
        Err(f) => return failure_response(&e, f, &plan.chain),
    };
    let latency = out.attempts.last().map(|a| a.latency);
    let served = out.result;
    let (provider, upstream_model) = (served.provider.clone(), served.deployment.model.clone());
    match served.reply {
        Reply::Json(mut v) => {
            let q = quality_of_body(&v, plan.features.wants_json);
            let usage = v.get("usage").cloned();
            e.record_success(&ctx, &out.model, &served.deployment, latency, Some(q), usage.as_ref());
            v["orchestrator"] = meta(&rid, &plan, &out.model, &provider, &upstream_model, &out.attempts);
            let mut resp = Json(v).into_response();
            meta_headers(resp.headers_mut(), &rid, &plan, &out.model, &out.attempts);
            resp
        }
        Reply::Stream(upstream) => {
            let (tx, rx) = tokio::sync::mpsc::channel::<Result<Bytes, std::io::Error>>(32);
            let engine = e.clone();
            let deployment = served.deployment.clone();
            let model = out.model.clone();
            let wants_json = plan.features.wants_json;
            tokio::spawn(async move {
                let started = Instant::now();
                let mut buf: Vec<u8> = Vec::new();
                let mut completed = true;
                let mut client_gone = false;
                let mut s = upstream.bytes_stream();
                while let Some(chunk) = s.next().await {
                    match chunk {
                        Ok(b) => {
                            if buf.len() < 1_000_000 {
                                buf.extend_from_slice(&b);
                            }
                            if tx.send(Ok(b)).await.is_err() {
                                client_gone = true;
                                break;
                            }
                        }
                        Err(err) => {
                            completed = false;
                            let _ = tx.send(Err(std::io::Error::other(err.to_string()))).await;
                            break;
                        }
                    }
                }
                let (text, finish, tools, usage) = parse_sse(&buf);
                let cfg = engine.cfg();
                if let (Some(u), Some(p)) = (&usage, cfg.providers.get(&deployment.provider)) {
                    let t = u["total_tokens"].as_u64().unwrap_or(0);
                    engine.quotas.record_tokens(p, &deployment, t, now());
                }
                if completed || client_gone {
                    let q = (!client_gone).then(|| auto_quality(&text, finish.as_deref(), tools, wants_json));
                    engine.record_success(&ctx, &model, &deployment,
                        latency.or(Some(started.elapsed().as_secs_f64())), q, usage.as_ref());
                } else {
                    engine.breaker.failure(&model, &cfg.reliability.circuit_breaker, now());
                    engine.telemetry.record(&Row {
                        request_id: ctx.request_id.clone(), profile: ctx.profile.clone(), agent: ctx.agent.clone(),
                        bucket: ctx.bucket.clone(), route_source: ctx.source.clone(), model: model.clone(),
                        provider: Some(deployment.provider.clone()), upstream_model: Some(deployment.model.clone()),
                        ok: false, is_final: true, latency, error_kind: Some("stream_interrupted".into()), stream: true,
                        ..Default::default()
                    });
                }
            });
            let body = Body::from_stream(futures::stream::unfold(rx, |mut rx| async move {
                rx.recv().await.map(|item| (item, rx))
            }));
            let mut resp = Response::new(body);
            let h = resp.headers_mut();
            h.insert(header::CONTENT_TYPE, HeaderValue::from_static("text/event-stream"));
            h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
            meta_headers(h, &rid, &plan, &out.model, &out.attempts);
            resp
        }
    }
}

/// Reconstruye texto, finish_reason, si hubo tool_calls y usage de un stream SSE.
pub fn parse_sse(raw: &[u8]) -> (String, Option<String>, bool, Option<Value>) {
    let mut text = String::new();
    let mut finish = None;
    let mut tools = false;
    let mut usage = None;
    for line in String::from_utf8_lossy(raw).lines() {
        let Some(data) = line.strip_prefix("data:").map(str::trim) else { continue };
        if data.is_empty() || data == "[DONE]" {
            continue;
        }
        let Ok(obj) = serde_json::from_str::<Value>(data) else { continue };
        if obj.get("usage").is_some_and(|u| !u.is_null()) {
            usage = obj.get("usage").cloned();
        }
        for ch in obj["choices"].as_array().into_iter().flatten() {
            if let Some(c) = ch["delta"]["content"].as_str() {
                text.push_str(c);
            }
            if ch["delta"].get("tool_calls").is_some_and(|t| !t.is_null()) {
                tools = true;
            }
            if let Some(f) = ch["finish_reason"].as_str() {
                finish = Some(f.to_string());
            }
        }
    }
    (text, finish, tools, usage)
}

#[derive(Deserialize)]
struct FeedbackIn {
    request_id: String,
    quality: f64,
}

async fn feedback(State(e): State<AppState>, headers: HeaderMap, Json(fb): Json<FeedbackIn>) -> Response {
    // La UI valora respuestas con el token de administración.
    if admin_auth(&e, &headers).is_err() {
        guard!(gateway_auth(&e, &headers));
    }
    if !(0.0..=1.0).contains(&fb.quality) {
        return error(StatusCode::BAD_REQUEST, "quality debe estar entre 0 y 1", "invalid_request_error", json!({}));
    }
    if e.telemetry.set_quality(&fb.request_id, fb.quality) == 0 {
        return error(StatusCode::NOT_FOUND, "request_id desconocido", "not_found", json!({}));
    }
    Json(json!({"ok": true})).into_response()
}

// ---------------------------------------------------------------------------
// /admin/api
// ---------------------------------------------------------------------------

async fn get_config(State(e): State<AppState>, headers: HeaderMap) -> Response {
    guard!(admin_auth(&e, &headers));
    Json(e.cfg().masked()).into_response()
}

fn apply(e: &Engine, cfg: Config) -> Response {
    match e.replace_config(cfg) {
        Ok(()) => Json(json!({"ok": true, "config": e.cfg().masked()})).into_response(),
        Err(msg) => error(StatusCode::UNPROCESSABLE_ENTITY, msg, "invalid_config", json!({})),
    }
}

async fn put_config(State(e): State<AppState>, headers: HeaderMap, Json(cfg): Json<Config>) -> Response {
    guard!(admin_auth(&e, &headers));
    apply(&e, cfg)
}

async fn get_config_yaml(State(e): State<AppState>, headers: HeaderMap) -> Response {
    guard!(admin_auth(&e, &headers));
    ([(header::CONTENT_TYPE, "text/yaml; charset=utf-8")], e.cfg().masked().to_yaml()).into_response()
}

async fn put_config_yaml(State(e): State<AppState>, headers: HeaderMap, text: String) -> Response {
    guard!(admin_auth(&e, &headers));
    match serde_yaml::from_str::<Config>(&text) {
        Ok(cfg) => apply(&e, cfg),
        Err(err) => error(StatusCode::UNPROCESSABLE_ENTITY, format!("YAML inválido: {err}"), "invalid_config", json!({})),
    }
}

fn edit(e: &Engine, f: impl FnOnce(&mut Config)) -> Response {
    let mut cfg = (*e.cfg()).clone();
    f(&mut cfg);
    apply(e, cfg)
}

async fn put_provider(State(e): State<AppState>, headers: HeaderMap, Path(name): Path<String>,
                      Json(p): Json<Provider>) -> Response {
    guard!(admin_auth(&e, &headers));
    edit(&e, |c| { c.providers.insert(name, p); })
}

async fn delete_provider(State(e): State<AppState>, headers: HeaderMap, Path(name): Path<String>) -> Response {
    guard!(admin_auth(&e, &headers));
    edit(&e, |c| { c.providers.remove(&name); })
}

async fn put_model(State(e): State<AppState>, headers: HeaderMap, Path(name): Path<String>,
                   Json(m): Json<ModelSpec>) -> Response {
    guard!(admin_auth(&e, &headers));
    edit(&e, |c| { c.models.insert(name, m); })
}

async fn delete_model(State(e): State<AppState>, headers: HeaderMap, Path(name): Path<String>) -> Response {
    guard!(admin_auth(&e, &headers));
    edit(&e, |c| { c.models.remove(&name); })
}

async fn put_agent(State(e): State<AppState>, headers: HeaderMap, Path(name): Path<String>,
                   Json(a): Json<AgentSpec>) -> Response {
    guard!(admin_auth(&e, &headers));
    edit(&e, |c| { c.agents.insert(name, a); })
}

async fn delete_agent(State(e): State<AppState>, headers: HeaderMap, Path(name): Path<String>) -> Response {
    guard!(admin_auth(&e, &headers));
    edit(&e, |c| { c.agents.remove(&name); })
}

async fn reset_model(State(e): State<AppState>, headers: HeaderMap, Path(name): Path<String>) -> Response {
    guard!(admin_auth(&e, &headers));
    let cfg = e.cfg();
    e.breaker.reset(&name);
    e.cooldowns.clear(&name);
    if let Some(m) = cfg.models.get(&name) {
        for d in &m.deployments {
            e.cooldowns.clear(&format!("dep:{}", crate::quotas::deployment_key(d)));
        }
    }
    Json(json!({"ok": true})).into_response()
}

/// Lista los modelos del proveedor (GET /models) y marca qué deployments configurados existen.
async fn provider_models(State(e): State<AppState>, headers: HeaderMap, Path(name): Path<String>) -> Response {
    guard!(admin_auth(&e, &headers));
    let cfg = e.cfg();
    let Some(p) = cfg.providers.get(&name) else {
        return error(StatusCode::NOT_FOUND, "proveedor desconocido", "not_found", json!({}));
    };
    let mut req = e.client.get(format!("{}/models", p.base_url.trim_end_matches('/'))).timeout(Duration::from_secs(20));
    for (k, v) in &p.headers {
        req = req.header(k, v);
    }
    if let Some(k) = p.key() {
        req = req.bearer_auth(k);
    }
    let t0 = Instant::now();
    let (ok, status, ids, err) = match req.send().await {
        Err(err) => (false, None, vec![], Some(err.to_string())),
        Ok(r) => {
            let st = r.status().as_u16();
            let v: Value = r.json().await.unwrap_or(Value::Null);
            let list = v["data"].as_array().or(v["models"].as_array()).cloned().unwrap_or_default();
            let ids: Vec<Value> = list.iter().map(|m| {
                let id = m["id"].as_str().or(m["name"].as_str()).unwrap_or("").trim_start_matches("models/").to_string();
                json!({"id": id, "context": m["context_length"], "input_modalities": m["architecture"]["input_modalities"],
                       "free": id.ends_with(":free") || m["pricing"]["prompt"].as_str() == Some("0")})
            }).collect();
            let err = (st != 200).then(|| trunc(&v.to_string(), 300));
            (st == 200, Some(st), ids, err)
        }
    };
    let known: std::collections::HashSet<&str> = ids.iter().filter_map(|m| m["id"].as_str()).collect();
    let configured: Vec<Value> = cfg.models.iter().flat_map(|(g, m)| {
        m.deployments.iter().filter(|d| d.provider == name)
            .map(|d| json!({"group": g, "model": d.model, "exists": ok.then(|| known.contains(d.model.as_str()))}))
            .collect::<Vec<_>>()
    }).collect();
    Json(json!({"ok": ok, "status": status, "latency": t0.elapsed().as_secs_f64(), "error": err,
                "models": ids, "configured": configured})).into_response()
}

async fn provider_balance(State(e): State<AppState>, headers: HeaderMap, Path(name): Path<String>) -> Response {
    guard!(admin_auth(&e, &headers));
    let cfg = e.cfg();
    let Some(p) = cfg.providers.get(&name) else {
        return error(StatusCode::NOT_FOUND, "proveedor desconocido", "not_found", json!({}));
    };
    match probe_balance(&e.client, p).await {
        Some(b) => {
            e.quotas.set_balance(&name, b.clone());
            Json(json!(b)).into_response()
        }
        None => Json(json!({"error": "este proveedor no tiene endpoint de saldo configurado"})).into_response(),
    }
}

async fn quotas(State(e): State<AppState>, headers: HeaderMap) -> Response {
    guard!(admin_auth(&e, &headers));
    Json(e.quotas.report(&e.cfg(), now())).into_response()
}

async fn status(State(e): State<AppState>, headers: HeaderMap) -> Response {
    guard!(admin_auth(&e, &headers));
    let cfg = e.cfg();
    let t = now();
    let mut models = serde_json::Map::new();
    for (name, m) in &cfg.models {
        let (st, open_until) = e.breaker.state(name, t);
        let deployments: Vec<Value> = m.deployments.iter().map(|d| {
            let p = cfg.providers.get(&d.provider);
            let reason = match p {
                None => Some("proveedor desconocido".to_string()),
                Some(p) if !p.enabled => Some("proveedor desactivado".into()),
                Some(p) if p.missing_key() => Some("sin clave".into()),
                Some(p) => e.cooldowns.get(&format!("dep:{}", crate::quotas::deployment_key(d)), t)
                    .map(|c| format!("cooldown ({}) {:.0} s", c.reason, c.until - t))
                    .or_else(|| e.quotas.blocked(p, d, t)),
            };
            json!({"provider": d.provider, "model": d.model, "usable": reason.is_none(), "reason": reason})
        }).collect();
        models.insert(name.clone(), json!({
            "circuit": st, "open_until": open_until, "cooldown": e.cooldowns.get(name, t),
            "capabilities": m.capabilities, "cost": m.cost, "deployments": deployments,
        }));
    }
    Json(json!({"now": t, "models": models, "stats": e.telemetry.summary(cfg.learning.window_days)})).into_response()
}

#[derive(Deserialize)]
struct Limit {
    limit: Option<u32>,
}

async fn requests(State(e): State<AppState>, headers: HeaderMap, Query(q): Query<Limit>) -> Response {
    guard!(admin_auth(&e, &headers));
    Json(e.telemetry.recent(q.limit.unwrap_or(100).min(1000))).into_response()
}

/// El chat de la UI: igual que /v1/chat/completions, sin stream y con el token de administración.
async fn playground(State(e): State<AppState>, headers: HeaderMap, raw: Bytes) -> Response {
    guard!(admin_auth(&e, &headers));
    let mut body = match parse_body(&raw) { Ok(b) => b, Err(r) => return r };
    body["stream"] = json!(false);
    handle_chat(e, &headers, body).await
}

async fn admin_route(State(e): State<AppState>, headers: HeaderMap, raw: Bytes) -> Response {
    guard!(admin_auth(&e, &headers));
    do_route(&e, &headers, &raw).await
}

// ---------------------------------------------------------------------------
// UI (embebida en el binario)
// ---------------------------------------------------------------------------

async fn ui_index() -> Response {
    ([(header::CONTENT_TYPE, "text/html; charset=utf-8")], include_str!("../ui/index.html")).into_response()
}

async fn ui_js() -> Response {
    ([(header::CONTENT_TYPE, "application/javascript; charset=utf-8")], include_str!("../ui/app.js")).into_response()
}

async fn ui_css() -> Response {
    ([(header::CONTENT_TYPE, "text/css; charset=utf-8")], include_str!("../ui/style.css")).into_response()
}
