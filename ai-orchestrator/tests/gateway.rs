//! Pruebas de punta a punta: el gateway real contra un proveedor falso con formato OpenAI.
//!
//! El proveedor falso decide por el nombre del modelo:
//!   ok-*      200 con cabeceras x-ratelimit-*
//!   rl-*      429 siempre (Retry-After: 0)
//!   bad-*     400 «petición mal formada»
//!   down-*    503
//! y expone /models y /key (saldo al estilo OpenRouter).

use ai_orchestrator::{api, config::Config, engine::Engine, telemetry::Telemetry};
use axum::body::Body;
use axum::extract::State;
use axum::http::{Request, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use tower::ServiceExt;

const ADMIN: &str = "admin-secreto";

#[derive(Default)]
struct Fake {
    calls: Mutex<HashMap<String, usize>>,
    bodies: Mutex<Vec<Value>>,
}

async fn fake_chat(State(f): State<Arc<Fake>>, Json(body): Json<Value>) -> Response {
    let model = body["model"].as_str().unwrap_or("").to_string();
    *f.calls.lock().unwrap().entry(model.clone()).or_default() += 1;
    f.bodies.lock().unwrap().push(body.clone());
    if model.starts_with("rl-") {
        return (StatusCode::TOO_MANY_REQUESTS, [("retry-after", "0")],
            Json(json!({"error": {"message": "Rate limit exceeded", "code": 429}}))).into_response();
    }
    if model.starts_with("bad-") {
        return (StatusCode::BAD_REQUEST, Json(json!({"error": {"message": "messages[0]: campo inválido"}}))).into_response();
    }
    if model.starts_with("tool-") {
        if body["stream"].as_bool() == Some(true) {
            let sse = format!(
                "data: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
                json!({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                    "function": {"name": "Bash", "arguments": "{\"command\":"}}]}}]}),
                json!({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"ls\"}"}}]},
                    "finish_reason": "tool_calls"}]}),
            );
            return ([("content-type", "text/event-stream")], sse).into_response();
        }
        return Json(json!({"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant",
            "content": null, "tool_calls": [{"id": "call_1", "type": "function",
            "function": {"name": "Bash", "arguments": "{\"command\":\"ls\"}"}}]}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}})).into_response();
    }
    if model.starts_with("down-") {
        return (StatusCode::SERVICE_UNAVAILABLE, "overloaded").into_response();
    }
    if body["stream"].as_bool() == Some(true) {
        let sse = format!(
            "data: {}\n\ndata: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
            json!({"choices": [{"delta": {"content": "Ho"}, "index": 0}]}),
            json!({"choices": [{"delta": {"content": "la"}, "index": 0}]}),
            json!({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
                   "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}),
        );
        return ([("content-type", "text/event-stream")], sse).into_response();
    }
    (
        [("x-ratelimit-limit-requests", "100"), ("x-ratelimit-remaining-requests", "42"),
         ("x-ratelimit-reset-requests", "30s")],
        Json(json!({
            "id": "x", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": format!("respuesta de {model}")},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })),
    ).into_response()
}

async fn fake_models() -> Json<Value> {
    Json(json!({"data": [{"id": "ok-a"}, {"id": "ok-b"}, {"id": "rl-a"}]}))
}

async fn fake_key() -> Json<Value> {
    Json(json!({"data": {"limit": 2.0, "usage": 1.5, "limit_remaining": 0.5, "is_free_tier": false}}))
}

async fn start_fake() -> (String, Arc<Fake>) {
    let fake = Arc::new(Fake::default());
    let app = Router::new()
        .route("/v1/chat/completions", post(fake_chat))
        .route("/v1/models", get(fake_models))
        .route("/v1/key", get(fake_key))
        .with_state(fake.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (format!("http://{addr}/v1"), fake)
}

fn config(base: &str) -> Config {
    let yaml = format!(r#"
providers:
  p1:
    base_url: {base}
    api_key: k1
    quota: {{balance: openrouter}}
  p2:
    base_url: {base}
    api_key: k2
  limited:
    base_url: {base}
    api_key: k3
    quota: {{requests_per_day: 2}}
models:
  flaky:   {{capabilities: [text, code], deployments: [{{provider: p1, model: rl-flaky}}]}}
  good:    {{capabilities: [text, code], deployments: [{{provider: p1, model: ok-good}}]}}
  twoprov: {{capabilities: [text], deployments: [{{provider: p1, model: rl-two}}, {{provider: p2, model: ok-two}}]}}
  broken:  {{capabilities: [text], deployments: [{{provider: p1, model: bad-x}}]}}
  down:    {{capabilities: [text], deployments: [{{provider: p1, model: down-x}}]}}
  seer:    {{capabilities: [text, image], deployments: [{{provider: p2, model: ok-vision}}]}}
  capmodel: {{capabilities: [text], deployments: [{{provider: limited, model: ok-capped}}]}}
  toolmodel: {{capabilities: [text, tools], deployments: [{{provider: p2, model: tool-x}}]}}
agents:
  general: {{chain: [good], priority: speed}}
  coding:  {{chain: [flaky, good], params: {{temperature: 0.2}}}}
  twohop:  {{chain: [twoprov]}}
  strict:  {{chain: [broken, good]}}
  outage:  {{chain: [down, good]}}
  vision:  {{chain: [good, seer]}}
  capped:  {{chain: [capmodel, good]}}
  tools:   {{chain: [toolmodel]}}
compat:
  model_aliases:
    "claude-*sonnet*": general
    "claude-*tool*": tools
router:
  judge: {{enabled: false}}
reliability:
  backoff: {{base: 0.01, max: 0.02}}
  circuit_breaker: {{failure_threshold: 2, window: 60, open_seconds: 30}}
learning:
  exploration: 0
"#);
    Config::from_yaml(&yaml).unwrap()
}

async fn setup() -> (Router, Arc<Engine>, Arc<Fake>, tempdir::Dir) {
    let (base, fake) = start_fake().await;
    let dir = tempdir::Dir::new();
    let path = dir.0.join("config.yaml");
    std::fs::write(&path, config(&base).to_yaml()).unwrap();
    let engine = Arc::new(Engine::new(config(&base), path, Telemetry::open(":memory:").unwrap(), vec![], ADMIN.into()));
    (api::router(engine.clone()), engine, fake, dir)
}

mod tempdir {
    pub struct Dir(pub std::path::PathBuf);
    impl Dir {
        pub fn new() -> Self {
            let p = std::env::temp_dir().join(format!("aio-test-{}", uuid::Uuid::new_v4().simple()));
            std::fs::create_dir_all(&p).unwrap();
            Dir(p)
        }
    }
    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
}

async fn call(app: &Router, method: &str, uri: &str, body: Option<Value>, token: Option<&str>) -> (StatusCode, Value, axum::http::HeaderMap) {
    let mut req = Request::builder().method(method).uri(uri).header("content-type", "application/json");
    if let Some(t) = token {
        req = req.header("authorization", format!("Bearer {t}"));
    }
    let req = req.body(body.map(|b| Body::from(b.to_string())).unwrap_or_else(Body::empty)).unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let headers = resp.headers().clone();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let v = serde_json::from_slice(&bytes).unwrap_or_else(|_| Value::String(String::from_utf8_lossy(&bytes).into()));
    (status, v, headers)
}

fn chat(model: &str, text: &str) -> Value {
    json!({"model": model, "messages": [{"role": "user", "content": text}]})
}

#[tokio::test]
async fn falls_back_on_429_and_then_cools_the_model_down() {
    let (app, _e, fake, _d) = setup().await;
    let (st, v, h) = call(&app, "POST", "/v1/chat/completions", Some(chat("coding", "hola")), None).await;
    assert_eq!(st, StatusCode::OK, "{v}");
    assert_eq!(v["choices"][0]["message"]["content"], "respuesta de ok-good");
    assert_eq!(h["x-orchestrator-model"], "good");
    // 1 intento + 2 reintentos en el modelo con 429
    assert_eq!(fake.calls.lock().unwrap()["rl-flaky"], 3);
    let kinds: Vec<&str> = v["orchestrator"]["attempts"].as_array().unwrap().iter()
        .map(|a| a["kind"].as_str().unwrap_or("ok")).collect();
    assert_eq!(kinds, ["rate_limit", "rate_limit", "rate_limit", "ok"]);
    // Parámetros del agente aplicados sin pisar los de la petición
    assert_eq!(fake.bodies.lock().unwrap().last().unwrap()["temperature"], 0.2);

    // Segunda petición: el modelo con 429 está en cooldown y ni se llama.
    let (st, v, _) = call(&app, "POST", "/v1/chat/completions", Some(chat("coding", "hola")), None).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(fake.calls.lock().unwrap()["rl-flaky"], 3);
    assert_eq!(v["orchestrator"]["attempts"][0]["skipped"], "cooldown");
}

#[tokio::test]
async fn second_provider_of_the_same_model() {
    let (app, _e, _f, _d) = setup().await;
    let (st, v, _) = call(&app, "POST", "/v1/chat/completions", Some(chat("twohop", "hola")), None).await;
    assert_eq!(st, StatusCode::OK, "{v}");
    assert_eq!(v["orchestrator"]["provider"], "p2");
    assert_eq!(v["orchestrator"]["upstream_model"], "ok-two");
}

#[tokio::test]
async fn bad_request_goes_to_the_client_without_fallback() {
    let (app, _e, fake, _d) = setup().await;
    let (st, v, _) = call(&app, "POST", "/v1/chat/completions", Some(chat("strict", "hola")), None).await;
    assert_eq!(st, StatusCode::BAD_REQUEST, "{v}");
    assert_eq!(v["error"]["type"], "bad_request");
    assert!(!fake.calls.lock().unwrap().contains_key("ok-good"));
}

#[tokio::test]
async fn circuit_opens_after_repeated_5xx() {
    let (app, e, _f, _d) = setup().await;
    let (st, _, _) = call(&app, "POST", "/v1/chat/completions", Some(chat("outage", "hola")), None).await;
    assert_eq!(st, StatusCode::OK);
    let (state, _) = e.breaker.state("down", ai_orchestrator::reliability::now());
    assert_eq!(state, ai_orchestrator::reliability::CircuitState::Open);
    let (_, v, _) = call(&app, "POST", "/v1/chat/completions", Some(chat("outage", "hola")), None).await;
    assert_eq!(v["orchestrator"]["attempts"][0]["skipped"], "circuit_open");
}

#[tokio::test]
async fn local_daily_quota_skips_before_the_429() {
    let (app, _e, fake, _d) = setup().await;
    for _ in 0..2 {
        let (_, v, _) = call(&app, "POST", "/v1/chat/completions", Some(chat("capped", "hola")), None).await;
        assert_eq!(v["orchestrator"]["model"], "capmodel");
    }
    let (_, v, _) = call(&app, "POST", "/v1/chat/completions", Some(chat("capped", "hola")), None).await;
    assert_eq!(v["orchestrator"]["model"], "good");
    assert_eq!(v["orchestrator"]["attempts"][0]["skipped"], "no_quota");
    assert_eq!(fake.calls.lock().unwrap()["ok-capped"], 2);

    let (st, q, _) = call(&app, "GET", "/admin/api/quotas", None, Some(ADMIN)).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(q["limited"]["requests_today"], 2);
    assert_eq!(q["limited"]["remaining_today"], 0);
    // Las cabeceras x-ratelimit-* del proveedor quedan a la vista
    let deps = q["p1"]["deployments"].as_array().unwrap();
    let good = deps.iter().find(|d| d["model"] == "ok-good").unwrap();
    assert_eq!(good["headers"]["remaining_requests"], 42.0);
}

#[tokio::test]
async fn image_requests_only_go_to_capable_models() {
    let (app, _e, _f, _d) = setup().await;
    let body = json!({"model": "auto", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "¿qué hay en la foto?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]});
    let (st, plan, _) = call(&app, "POST", "/v1/route", Some(body.clone()), None).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(plan["decision"]["agent"], "vision");
    assert_eq!(plan["chain"], json!(["seer"]));
    assert_eq!(plan["dropped"]["good"], "no admite image");
    let (st, v, _) = call(&app, "POST", "/v1/chat/completions", Some(body), None).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(v["orchestrator"]["upstream_model"], "ok-vision");
}

#[tokio::test]
async fn streaming_passthrough_and_telemetry() {
    let (app, e, _f, _d) = setup().await;
    let mut body = chat("general", "hola");
    body["stream"] = json!(true);
    let (st, v, h) = call(&app, "POST", "/v1/chat/completions", Some(body), None).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(h["content-type"], "text/event-stream");
    let text = v.as_str().unwrap();
    assert!(text.contains("\"Ho\"") && text.contains("[DONE]"));
    // La fila final se escribe al terminar el stream (en otra tarea).
    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    let rows = e.telemetry.recent(5);
    let last = rows.iter().find(|r| r["final"] == true).unwrap();
    assert_eq!(last["ok"], true);
    assert_eq!(last["stream"], true);
    assert_eq!(last["quality"], 0.8);
}

#[tokio::test]
async fn unknown_model_is_404_and_feedback_updates_quality() {
    let (app, _e, _f, _d) = setup().await;
    let (st, _, _) = call(&app, "POST", "/v1/chat/completions", Some(chat("no-existe", "hola")), None).await;
    assert_eq!(st, StatusCode::NOT_FOUND);
    let (_, v, _) = call(&app, "POST", "/v1/chat/completions", Some(chat("general", "hola")), None).await;
    let rid = v["orchestrator"]["request_id"].as_str().unwrap().to_string();
    let (st, _, _) = call(&app, "POST", "/v1/feedback", Some(json!({"request_id": rid, "quality": 0.3})), None).await;
    assert_eq!(st, StatusCode::OK);
    let (st, _, _) = call(&app, "POST", "/v1/feedback", Some(json!({"request_id": "nope", "quality": 0.3})), None).await;
    assert_eq!(st, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn admin_requires_token_masks_keys_and_persists() {
    let (app, e, _f, dir) = setup().await;
    let (st, _, _) = call(&app, "GET", "/admin/api/config", None, None).await;
    assert_eq!(st, StatusCode::UNAUTHORIZED);
    let (st, _, _) = call(&app, "GET", "/admin/api/config", None, Some("otro")).await;
    assert_eq!(st, StatusCode::UNAUTHORIZED);

    let (st, cfg, _) = call(&app, "GET", "/admin/api/config", None, Some(ADMIN)).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(cfg["providers"]["p1"]["api_key"], ai_orchestrator::config::MASK);

    // Editar un proveedor devolviendo la clave tapada no la pierde.
    let mut p1 = cfg["providers"]["p1"].clone();
    p1["quota"]["requests_per_day"] = json!(500);
    let (st, v, _) = call(&app, "PUT", "/admin/api/providers/p1", Some(p1), Some(ADMIN)).await;
    assert_eq!(st, StatusCode::OK, "{v}");
    assert_eq!(e.cfg().providers["p1"].api_key.as_deref(), Some("k1"));
    assert_eq!(e.cfg().providers["p1"].quota.requests_per_day, 500);
    let saved = std::fs::read_to_string(dir.0.join("config.yaml")).unwrap();
    assert!(saved.contains("requests_per_day: 500"));

    // Borrar un proveedor en uso: la validación lo impide.
    let (st, v, _) = call(&app, "DELETE", "/admin/api/providers/p2", None, Some(ADMIN)).await;
    assert_eq!(st, StatusCode::UNPROCESSABLE_ENTITY, "{v}");

    // Un agente nuevo se puede pedir como modelo al instante.
    let agent = json!({"description": "pruebas", "chain": ["good"], "priority": "cost"});
    let (st, _, _) = call(&app, "PUT", "/admin/api/agents/nuevo", Some(agent), Some(ADMIN)).await;
    assert_eq!(st, StatusCode::OK);
    let (st, v, _) = call(&app, "POST", "/v1/chat/completions", Some(chat("nuevo", "hola")), None).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(v["orchestrator"]["agent"], "nuevo");
}

#[tokio::test]
async fn provider_models_and_balance() {
    let (app, _e, _f, _d) = setup().await;
    let (st, v, _) = call(&app, "POST", "/admin/api/providers/p1/models", None, Some(ADMIN)).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(v["ok"], true);
    let configured = v["configured"].as_array().unwrap();
    let exists = |m: &str| configured.iter().find(|c| c["model"] == m).unwrap()["exists"].clone();
    assert_eq!(exists("rl-flaky"), json!(false));
    assert_eq!(exists("ok-good"), json!(false));
    assert_eq!(v["models"].as_array().unwrap().len(), 3);

    let (st, b, _) = call(&app, "POST", "/admin/api/providers/p1/balance", None, Some(ADMIN)).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(b["remaining"], 0.5);
    assert_eq!(b["limit"], 2.0);
}

#[tokio::test]
async fn ui_is_served() {
    let (app, _e, _f, _d) = setup().await;
    let (st, v, _) = call(&app, "GET", "/ui/", None, None).await;
    assert_eq!(st, StatusCode::OK);
    assert!(v.as_str().unwrap().contains("<html"));
}

// ---------------------------------------------------------------------------
// API de Anthropic (Claude Code)
// ---------------------------------------------------------------------------

async fn raw_call(app: &Router, uri: &str, body: Value, headers: &[(&str, &str)]) -> (StatusCode, String) {
    let mut req = Request::builder().method("POST").uri(uri).header("content-type", "application/json");
    for (k, v) in headers {
        req = req.header(*k, *v);
    }
    let resp = app.clone().oneshot(req.body(Body::from(body.to_string())).unwrap()).await.unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    (status, String::from_utf8_lossy(&bytes).into())
}

fn events(sse: &str) -> Vec<Value> {
    sse.lines().filter_map(|l| l.strip_prefix("data: ")).map(|d| serde_json::from_str(d).unwrap()).collect()
}

#[tokio::test]
async fn anthropic_messages_with_alias_and_text() {
    let (app, _e, fake, _d) = setup().await;
    let req = json!({"model": "claude-sonnet-4-5", "max_tokens": 100, "system": "Eres útil.",
        "messages": [{"role": "user", "content": "hola"}]});
    let (st, body) = raw_call(&app, "/v1/messages", req, &[("x-api-key", "cualquiera"), ("user-agent", "claude-cli/2.0")]).await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let v: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["type"], "message");
    assert_eq!(v["model"], "claude-sonnet-4-5");
    assert_eq!(v["content"][0]["text"], "respuesta de ok-good");
    assert_eq!(v["stop_reason"], "end_turn");
    assert_eq!(v["usage"]["input_tokens"], 10);
    let sent = fake.bodies.lock().unwrap().last().unwrap().clone();
    assert_eq!(sent["messages"][0], json!({"role": "system", "content": "Eres útil."}));
}

#[tokio::test]
async fn anthropic_tool_use_non_stream_and_stream() {
    let (app, _e, _f, _d) = setup().await;
    let mut req = json!({"model": "claude-tool-1", "max_tokens": 100,
        "tools": [{"name": "Bash", "description": "shell", "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": "lista"}]});
    let (st, body) = raw_call(&app, "/v1/messages", req.clone(), &[]).await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let v: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["stop_reason"], "tool_use");
    assert_eq!(v["content"][0], json!({"type": "tool_use", "id": "call_1", "name": "Bash", "input": {"command": "ls"}}));

    req["stream"] = json!(true);
    let (st, body) = raw_call(&app, "/v1/messages", req, &[]).await;
    assert_eq!(st, StatusCode::OK);
    let ev = events(&body);
    let types: Vec<&str> = ev.iter().map(|e| e["type"].as_str().unwrap()).collect();
    assert_eq!(types, ["message_start", "ping", "content_block_start", "content_block_delta", "content_block_stop",
        "message_delta", "message_stop"]);
    assert_eq!(ev[2]["content_block"]["name"], "Bash");
    assert_eq!(ev[3]["delta"]["partial_json"], "{\"command\":\"ls\"}");
    assert_eq!(ev[5]["delta"]["stop_reason"], "tool_use");
}

#[tokio::test]
async fn anthropic_text_stream() {
    let (app, _e, _f, _d) = setup().await;
    let req = json!({"model": "claude-sonnet-4-5", "max_tokens": 50, "stream": true,
        "messages": [{"role": "user", "content": "hola"}]});
    let (st, body) = raw_call(&app, "/v1/messages", req, &[]).await;
    assert_eq!(st, StatusCode::OK);
    let ev = events(&body);
    let text: String = ev.iter().filter(|e| e["type"] == "content_block_delta")
        .map(|e| e["delta"]["text"].as_str().unwrap()).collect();
    assert_eq!(text, "Hola");
    assert_eq!(ev.last().unwrap()["type"], "message_stop");
    assert_eq!(ev[ev.len() - 2]["usage"]["output_tokens"], 2);
}

#[tokio::test]
async fn anthropic_errors_and_count_tokens() {
    let (app, _e, _f, _d) = setup().await;
    let (st, body) = raw_call(&app, "/v1/messages", json!({"model": "no-existe", "max_tokens": 5,
        "messages": [{"role": "user", "content": "x"}]}), &[]).await;
    assert_eq!(st, StatusCode::NOT_FOUND);
    let v: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["type"], "error");
    assert_eq!(v["error"]["type"], "not_found_error");
    let (st, body) = raw_call(&app, "/v1/messages/count_tokens", json!({"model": "claude-sonnet-4-5",
        "messages": [{"role": "user", "content": "a".repeat(400)}]}), &[]).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["input_tokens"], 100);
}

#[tokio::test]
async fn style_and_tool_compression_reach_the_provider_and_the_stats() {
    let (app, _e, fake, _d) = setup().await;
    // Estilo por cabecera
    let (st, _, _) = {
        let req = Request::builder().method("POST").uri("/v1/chat/completions")
            .header("content-type", "application/json").header("x-jmd-style", "full").header("x-jmd-client", "jmd")
            .body(Body::from(chat("general", "hola").to_string())).unwrap();
        let resp = app.clone().oneshot(req).await.unwrap();
        (resp.status(), (), ())
    };
    assert_eq!(st, StatusCode::OK);
    let sent = fake.bodies.lock().unwrap().last().unwrap().clone();
    assert!(sent["messages"][0]["content"].as_str().unwrap().contains("Terse mode"));

    // Compresión de salidas de herramientas, activada desde la API de administración
    let (st, _, _) = call(&app, "PUT", "/admin/api/savings",
        Some(json!({"style": "lite", "compress_tool_output": true, "tool_output_max_chars": 300})), Some(ADMIN)).await;
    assert_eq!(st, StatusCode::OK);
    let long = "compilando…\n".repeat(200) + "error: falta ;";
    let body = json!({"model": "general", "messages": [
        {"role": "user", "content": "compila"},
        {"role": "assistant", "content": null, "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "sh", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": long}]});
    let (st, _, _) = call(&app, "POST", "/v1/chat/completions", Some(body), None).await;
    assert_eq!(st, StatusCode::OK);
    let sent = fake.bodies.lock().unwrap().last().unwrap().clone();
    let tool = sent["messages"].as_array().unwrap().iter().find(|m| m["role"] == "tool").unwrap();
    let t = tool["content"].as_str().unwrap();
    assert!(t.contains("[×200]") && t.ends_with("error: falta ;"), "{t}");
    assert!(sent["messages"][0]["content"].as_str().unwrap().contains("Answer concisely"));

    let (_, s, _) = call(&app, "GET", "/admin/api/savings", None, Some(ADMIN)).await;
    let rows = s["by_style"].as_array().unwrap();
    assert!(rows.iter().any(|r| r["style"] == "full" && r["client"] == "jmd"), "{s}");
    assert!(rows.iter().any(|r| r["style"] == "lite" && r["tool_chars_saved"].as_i64().unwrap() > 2000), "{s}");
}
