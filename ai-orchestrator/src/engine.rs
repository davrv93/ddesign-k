//! Motor: router (niveles 0-1-2), fallback manager y llamadas a los proveedores.
//!
//! ```text
//! model="auto"        → reglas → (si dudan) juez → agente → cadena filtrada por capacidades
//! model="coding-deep" → ese agente                        → cadena reordenada por el scorer
//! model="laguna"      → ese modelo, sin cadena
//!
//! cadena:  modelo 1 ──(429/timeout/5xx/cuota/caído)──► modelo 2 ──► …
//! modelo:  deployment 1 (OpenRouter) ──(falla/sin cuota)──► deployment 2 (OpenCode Zen)
//! ```

use crate::classifier::{classify, extract_features, Features, RouteDecision};
use crate::config::{AgentSpec, Config, Deployment, ErrorKind, Priority};
use crate::quotas::{deployment_key, Quotas};
use crate::reliability::{backoff_delay, classify_reqwest, error_from_response, now, Breaker, Cooldowns, UpstreamError};
use crate::scorer::{rank_with, Ranked};
use crate::telemetry::{Row, Telemetry};
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, VecDeque};
use std::future::Future;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct Attempt {
    pub model: String,
    pub ok: bool,
    pub latency: f64,
    pub kind: Option<String>,
    pub status: Option<u16>,
    pub skipped: Option<String>,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub message: String,
}

pub struct Outcome<T> {
    pub result: T,
    pub model: String,
    pub attempts: Vec<Attempt>,
}

#[derive(Debug)]
pub enum Failure {
    /// El error no admite fallback (la petición está mal): va tal cual al cliente.
    NoFallback(UpstreamError, Vec<Attempt>),
    AllFailed(Vec<Attempt>, Option<UpstreamError>),
}

#[derive(Debug)]
pub enum PlanError {
    UnknownProfile(String),
    NoCapableModel(String),
}

/// Respuesta de un proveedor.
pub enum Reply {
    Json(Value),
    Stream(reqwest::Response),
}

pub struct Served {
    pub reply: Reply,
    pub provider: String,
    pub deployment: Deployment,
}

/// Datos de la petición que acompañan a cada fila de telemetría.
#[derive(Clone, Default)]
pub struct CallCtx {
    pub request_id: String,
    pub profile: String,
    pub agent: String,
    pub bucket: Option<String>,
    pub source: String,
    pub stream: bool,
    /// Estilo de respuesta aplicado (off/lite/full/ultra), para medir su ahorro.
    pub style: Option<String>,
    /// Caracteres quitados de las salidas de herramientas.
    pub saved_chars: Option<u64>,
    /// Quién llamó: openai · anthropic · jmd · ui.
    pub client: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RoutePlan {
    pub profile: String,
    pub decision: RouteDecision,
    pub features: Features,
    pub chain: Vec<String>,
    pub priority: Priority,
    pub agent: Option<String>,
    pub ranked: Vec<Ranked>,
    pub dropped: HashMap<String, String>,
    pub requirements: Vec<&'static str>,
}

impl RoutePlan {
    /// Clave de telemetría: el agente, o «model:<x>» si se pidió un modelo concreto.
    pub fn task(&self) -> String {
        self.agent.clone().unwrap_or_else(|| format!("model:{}", self.chain[0]))
    }
}

// ---------------------------------------------------------------------------
// Motor
// ---------------------------------------------------------------------------

pub struct Engine {
    cfg: RwLock<Arc<Config>>,
    pub config_path: PathBuf,
    pub client: reqwest::Client,
    pub breaker: Breaker,
    pub cooldowns: Cooldowns,
    pub quotas: Quotas,
    pub telemetry: Telemetry,
    judge_cache: Mutex<(HashMap<String, JudgeOut>, VecDeque<String>)>,
    pub gateway_keys: Vec<String>,
    pub admin_token: String,
}

impl Engine {
    pub fn new(cfg: Config, config_path: PathBuf, telemetry: Telemetry, gateway_keys: Vec<String>, admin_token: String) -> Self {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .pool_idle_timeout(Duration::from_secs(90))
            .build()
            .expect("cliente HTTP");
        let e = Self {
            cfg: RwLock::new(Arc::new(cfg)),
            config_path,
            client,
            breaker: Breaker::default(),
            cooldowns: Cooldowns::default(),
            quotas: Quotas::default(),
            telemetry,
            judge_cache: Mutex::new((HashMap::new(), VecDeque::new())),
            gateway_keys,
            admin_token,
        };
        e.seed_quotas();
        e
    }

    pub fn cfg(&self) -> Arc<Config> {
        self.cfg.read().unwrap().clone()
    }

    /// Valida, guarda en disco y aplica en caliente.
    pub fn replace_config(&self, mut new: Config) -> Result<(), String> {
        let old = self.cfg();
        new.unmask_from(&old);
        new.validate()?;
        if !self.config_path.as_os_str().is_empty() {
            let tmp = self.config_path.with_extension("yaml.tmp");
            std::fs::write(&tmp, new.to_yaml()).map_err(|e| format!("no se pudo guardar: {e}"))?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
            }
            std::fs::rename(&tmp, &self.config_path).map_err(|e| format!("no se pudo guardar: {e}"))?;
        }
        *self.cfg.write().unwrap() = Arc::new(new);
        self.judge_cache.lock().unwrap().0.clear();
        Ok(())
    }

    /// Al arrancar, lo gastado hoy sale de la telemetría (los presupuestos sobreviven a reinicios).
    fn seed_quotas(&self) {
        let cfg = self.cfg();
        let t = now();
        for (name, p) in &cfg.providers {
            let off = p.quota.reset_utc_offset_hours;
            let day = crate::quotas::day_index(t, off);
            for (model, req, tok) in self.telemetry.usage_since(name, crate::quotas::day_start(t, off)) {
                self.quotas.seed_today(name, None, req, tok, day);
                self.quotas.seed_today(name, Some(&model), req, tok, day);
            }
        }
    }

    // -- disponibilidad -----------------------------------------------------

    fn usable_deployments(&self, cfg: &Config, group: &str, at: f64) -> (Vec<Deployment>, Vec<String>) {
        let mut ok = vec![];
        let mut why = vec![];
        let Some(spec) = cfg.models.get(group) else { return (ok, vec!["modelo desconocido".into()]) };
        for d in &spec.deployments {
            let Some(p) = cfg.providers.get(&d.provider) else { continue };
            if !p.enabled {
                why.push(format!("{} desactivado", d.provider));
            } else if p.missing_key() {
                why.push(format!("{} sin clave", d.provider));
            } else if let Some(c) = self.cooldowns.get(&format!("dep:{}", deployment_key(d)), at) {
                why.push(format!("{} en cooldown ({})", deployment_key(d), c.reason));
            } else if let Some(r) = self.quotas.blocked(p, d, at) {
                why.push(r);
            } else {
                ok.push(d.clone());
            }
        }
        (ok, why)
    }

    // -- llamada a un grupo (sus deployments en orden) ------------------------

    pub async fn call_group(&self, cfg: &Config, group: &str, payload: &Value, timeout: Duration,
                            stream: bool, ctx: &CallCtx) -> Result<Served, UpstreamError> {
        let (deps, why) = self.usable_deployments(cfg, group, now());
        if deps.is_empty() {
            return Err(UpstreamError::new(ErrorKind::Unavailable, None, format!("{group}: {}", why.join("; "))));
        }
        let n_deps = cfg.models[group].deployments.len();
        let mut last = None;
        for d in deps {
            let p = &cfg.providers[&d.provider];
            let t0 = Instant::now();
            match self.send(p, &d, payload, timeout, stream).await {
                Ok(reply) => return Ok(Served { reply, provider: d.provider.clone(), deployment: d }),
                Err(err) => {
                    let pol = cfg.reliability.policy(err.kind);
                    self.telemetry.record(&Row {
                        request_id: ctx.request_id.clone(), profile: ctx.profile.clone(), agent: ctx.agent.clone(),
                        bucket: ctx.bucket.clone(), route_source: ctx.source.clone(), model: group.into(),
                        provider: Some(d.provider.clone()), upstream_model: Some(d.model.clone()), ok: false,
                        latency: Some(t0.elapsed().as_secs_f64()), error_kind: Some(err.kind.as_str().into()),
                        status: err.status, stream: ctx.stream, ..Default::default()
                    });
                    tracing::warn!(group, provider = %d.provider, model = %d.model, "{err}");
                    if !pol.fallback {
                        return Err(err);
                    }
                    if n_deps > 1 && pol.cooldown > 0.0 {
                        let secs = pol.cooldown.max(err.retry_after.unwrap_or(0.0));
                        self.cooldowns.start(&format!("dep:{}", deployment_key(&d)), secs, err.kind.as_str(), now());
                    }
                    last = Some(err);
                }
            }
        }
        Err(last.unwrap_or_else(|| UpstreamError::new(ErrorKind::Unavailable, None, "sin deployments")))
    }

    async fn send(&self, p: &crate::config::Provider, d: &Deployment, payload: &Value, timeout: Duration,
                  stream: bool) -> Result<Reply, UpstreamError> {
        let mut body = payload.clone();
        body["model"] = json!(d.model);
        body["stream"] = json!(stream);
        let url = format!("{}/chat/completions", p.base_url.trim_end_matches('/'));
        let mut req = self.client.post(&url).json(&body);
        if !stream {
            req = req.timeout(timeout);
        }
        for (k, v) in &p.headers {
            req = req.header(k, v);
        }
        if let Some(k) = p.key() {
            req = req.bearer_auth(k);
        }
        let at = now();
        self.quotas.record_request(p, d, at);
        let fut = req.send();
        // En stream, el timeout cubre hasta las cabeceras; después manda el cliente.
        let resp = match tokio::time::timeout(timeout, fut).await {
            Err(_) => return Err(UpstreamError::new(ErrorKind::Timeout, None, "timeout esperando respuesta")),
            Ok(Err(e)) => return Err(UpstreamError::new(classify_reqwest(&e), None, e.to_string())),
            Ok(Ok(r)) => r,
        };
        self.quotas.observe_headers(d, resp.headers(), now());
        let status = resp.status().as_u16();
        if status != 200 {
            if status == 429 {
                self.quotas.refund_request(p, d, at);
            }
            let headers = resp.headers().clone();
            let text = resp.text().await.unwrap_or_default();
            return Err(error_from_response(status, &headers, &text));
        }
        if stream {
            return Ok(Reply::Stream(resp));
        }
        let headers = resp.headers().clone();
        let text = resp.text().await.map_err(|e| UpstreamError::new(classify_reqwest(&e), Some(200), e.to_string()))?;
        let data: Value = serde_json::from_str(&text)
            .map_err(|_| UpstreamError::new(ErrorKind::ServerError, Some(200), format!("respuesta no JSON: {}", trunc(&text, 200))))?;
        // Algunos proveedores devuelven 200 con un error dentro.
        if let Some(err) = data.get("error").filter(|_| data.get("choices").is_none()) {
            let code = err.get("code").and_then(Value::as_u64).map(|c| c as u16).unwrap_or(500);
            return Err(error_from_response(code, &headers, &err.to_string()));
        }
        if !data["choices"].as_array().is_some_and(|c| !c.is_empty()) {
            return Err(UpstreamError::new(ErrorKind::ServerError, Some(200), "respuesta sin choices"));
        }
        if let Some(u) = data.get("usage") {
            let tokens = u["total_tokens"].as_u64()
                .unwrap_or(u["prompt_tokens"].as_u64().unwrap_or(0) + u["completion_tokens"].as_u64().unwrap_or(0));
            self.quotas.record_tokens(p, d, tokens, now());
        }
        Ok(Reply::Json(data))
    }

    // -- fallback manager -----------------------------------------------------

    /// Recorre la cadena hasta que un modelo responda.
    ///
    /// Por modelo: en cooldown, con el circuito abierto o sin cuota → se salta. Si falla, la
    /// política del error decide: reintentar el mismo (backoff), pasar al siguiente o devolver
    /// el error al cliente. Al abandonarlo se le pone su cooldown. Si todos estaban saltados
    /// por cooldown o circuito, se prueba igual el que vuelve antes.
    pub async fn run<T, F, Fut>(&self, cfg: &Config, chain: &[String], per_try: Duration, deadline: Duration,
                                mut call: F) -> Result<Outcome<T>, Failure>
    where
        F: FnMut(String, Duration) -> Fut,
        Fut: Future<Output = Result<T, UpstreamError>>,
    {
        let rel = &cfg.reliability;
        let end = Instant::now() + deadline;
        let mut attempts: Vec<Attempt> = vec![];
        let mut skipped: Vec<(f64, String)> = vec![];
        let mut last: Option<UpstreamError> = None;
        let mut tried_any = false;

        for model in chain {
            let model = model.clone();
            let at = now();
            if let Some(c) = self.cooldowns.get(&model, at) {
                skipped.push((c.until, model.clone()));
                attempts.push(skip(&model, "cooldown", &c.reason));
                continue;
            }
            // La cuota se mira antes que el circuito: `allow` en semiabierto reserva la prueba.
            let (deps, why) = self.usable_deployments(cfg, &model, at);
            if deps.is_empty() {
                attempts.push(skip(&model, "no_quota", &why.join("; ")));
                continue;
            }
            if !self.breaker.allow(&model, at) {
                let until = self.breaker.state(&model, at).1.unwrap_or(at);
                skipped.push((until, model.clone()));
                attempts.push(skip(&model, "circuit_open", ""));
                continue;
            }
            tried_any = true;
            let mut n = 0u32;
            loop {
                let remaining = end.saturating_duration_since(Instant::now());
                if remaining < Duration::from_millis(500) {
                    break;
                }
                let t0 = Instant::now();
                match call(model.clone(), per_try.min(remaining)).await {
                    Ok(result) => {
                        self.breaker.success(&model);
                        attempts.push(Attempt { model: model.clone(), ok: true, latency: t0.elapsed().as_secs_f64(),
                            kind: None, status: None, skipped: None, message: String::new() });
                        return Ok(Outcome { result, model, attempts });
                    }
                    Err(err) => {
                        let pol = rel.policy(err.kind);
                        attempts.push(Attempt { model: model.clone(), ok: false, latency: t0.elapsed().as_secs_f64(),
                            kind: Some(err.kind.as_str().into()), status: err.status, skipped: None,
                            message: trunc(&err.message, 300) });
                        if pol.breaker {
                            self.breaker.failure(&model, &rel.circuit_breaker, now());
                        } else {
                            self.breaker.release_probe(&model);
                        }
                        if !pol.fallback {
                            return Err(Failure::NoFallback(err, attempts));
                        }
                        let retry = n < pol.retries;
                        let ra = err.retry_after;
                        let kind = err.kind;
                        last = Some(err);
                        if retry {
                            if let Some(delay) = backoff_delay(&rel.backoff, n, ra, rand::random()) {
                                if Instant::now() + Duration::from_secs_f64(delay) + Duration::from_secs(1) < end {
                                    tokio::time::sleep(Duration::from_secs_f64(delay)).await;
                                    n += 1;
                                    continue;
                                }
                            }
                        }
                        if pol.cooldown > 0.0 {
                            self.cooldowns.start(&model, pol.cooldown.max(ra.unwrap_or(0.0)), kind.as_str(), now());
                        }
                        break;
                    }
                }
            }
        }
        if !tried_any && !skipped.is_empty() {
            skipped.sort_by(|a, b| a.0.total_cmp(&b.0));
            let m = skipped[0].1.clone();
            tracing::warn!("todos los modelos fuera de servicio; se prueba {m} igualmente");
            let t0 = Instant::now();
            match call(m.clone(), per_try).await {
                Ok(result) => {
                    self.breaker.success(&m);
                    attempts.push(Attempt { model: m.clone(), ok: true, latency: t0.elapsed().as_secs_f64(),
                        kind: None, status: None, skipped: None, message: String::new() });
                    return Ok(Outcome { result, model: m, attempts });
                }
                Err(err) => {
                    attempts.push(Attempt { model: m, ok: false, latency: t0.elapsed().as_secs_f64(),
                        kind: Some(err.kind.as_str().into()), status: err.status, skipped: None,
                        message: trunc(&err.message, 300) });
                    last = Some(err);
                }
            }
        }
        Err(Failure::AllFailed(attempts, last))
    }

    // -- juez (nivel 1) ---------------------------------------------------------

    async fn judge(&self, cfg: &Config, body: &Value, f: &Features, mut fallback: RouteDecision) -> RouteDecision {
        let jc = &cfg.router.judge;
        let digest = conversation_digest(body, jc.max_chars);
        let key = hex(&Sha256::digest(digest.as_bytes()));
        let cached = self.judge_cache.lock().unwrap().0.get(&key).cloned();
        let out = match cached {
            Some(o) => Some(o),
            None => {
                let agents: Vec<(String, String)> =
                    cfg.agents.iter().map(|(n, a)| (n.clone(), a.description.clone())).collect();
                let payload = judge_body(&agents, &digest);
                let ctx = CallCtx { request_id: format!("judge-{}", uuid::Uuid::new_v4().simple()),
                    profile: "_judge".into(), agent: "_judge".into(), source: "judge".into(), ..Default::default() };
                let to = Duration::from_secs_f64(jc.timeout);
                let res = self.run(cfg, &jc.chain, to, to * 2, |m, t| {
                    let payload = &payload;
                    let ctx = &ctx;
                    async move { self.call_group(cfg, &m, payload, t, false, ctx).await }
                }).await;
                match res {
                    Ok(o) => {
                        self.record_success(&ctx, &o.model, &o.result.deployment, o.attempts.last().map(|a| a.latency), None, None);
                        let text = match &o.result.reply {
                            Reply::Json(v) => v["choices"][0]["message"]["content"].as_str().unwrap_or("").to_string(),
                            Reply::Stream(_) => String::new(),
                        };
                        let names: Vec<&str> = agents.iter().map(|(n, _)| n.as_str()).collect();
                        let parsed = parse_judge(&text, &names);
                        if let Some(p) = &parsed {
                            let mut c = self.judge_cache.lock().unwrap();
                            c.0.insert(key.clone(), p.clone());
                            c.1.push_back(key);
                            while c.1.len() > jc.cache_size.max(1) {
                                if let Some(old) = c.1.pop_front() {
                                    c.0.remove(&old);
                                }
                            }
                        }
                        parsed
                    }
                    Err(e) => {
                        tracing::warn!("juez no disponible: {e:?}");
                        None
                    }
                }
            }
        };
        let Some(out) = out else {
            fallback.reasons.push("juez sin respuesta válida: se usan las reglas".into());
            return fallback;
        };
        // Lo que se sabe con certeza (hay imagen, hay tools, contexto grande) manda sobre el juez.
        let modality = if f.modality() != "text" { f.modality().to_string() } else { out.modality.clone() };
        let mut reasons = fallback.reasons.clone();
        reasons.push(format!("juez: {}", out.agent));
        RouteDecision {
            agent: out.agent.clone(),
            modality,
            reasoning: out.reasoning.clone(),
            tools: out.tools || f.has_tools,
            context: if fallback.context == "large" { "large".into() } else { out.context.clone() },
            priority: Some(out.priority.clone()),
            confidence: 0.9,
            source: "judge".into(),
            reasons,
        }
    }

    // -- router (nivel 2) ----------------------------------------------------------

    pub async fn plan(&self, cfg: &Config, body: &Value, profile: Option<&str>, hints: &Hints) -> Result<RoutePlan, PlanError> {
        let mut profile = profile.filter(|p| !p.is_empty()).unwrap_or(&cfg.default_profile).to_string();
        if profile != "auto" && !cfg.agents.contains_key(&profile) && !cfg.models.contains_key(&profile) {
            // Claude Code pide «claude-sonnet-…», OpenCode lo que se le configure: alias → perfil.
            if let Some(target) = cfg.resolve_alias(&profile) {
                profile = target.to_string();
            }
        }
        let f = extract_features(body);
        let rules = classify(&f, cfg.router.long_context_tokens);
        let requirements: Vec<&'static str> = f.requirements().into_iter().collect();

        if cfg.models.contains_key(&profile) {
            let mut d = rules.clone();
            d.agent = format!("model:{profile}");
            d.source = "explicit".into();
            d.confidence = 1.0;
            d.reasons = vec!["modelo pedido".into()];
            return Ok(RoutePlan { profile: profile.clone(), decision: d, features: f, chain: vec![profile],
                priority: hints.priority.unwrap_or_default(), agent: None, ranked: vec![], dropped: HashMap::new(),
                requirements });
        }

        let mut decision = if profile == "auto" {
            if let Some(a) = &hints.agent {
                if !cfg.agents.contains_key(a) {
                    return Err(PlanError::UnknownProfile(a.clone()));
                }
                let mut d = rules;
                d.agent = a.clone();
                d.source = "hint".into();
                d.confidence = 1.0;
                d
            } else if rules.confidence < cfg.router.rules_min_confidence && cfg.router.judge.enabled
                && !cfg.router.judge.chain.is_empty() && hints.judge
            {
                self.judge(cfg, body, &f, rules).await
            } else {
                rules
            }
        } else if cfg.agents.contains_key(&profile) {
            let mut d = rules;
            d.reasons.push(format!("perfil {profile} (las reglas sugerían {})", d.agent));
            d.agent = profile.clone();
            d.source = "profile".into();
            d.confidence = 1.0;
            d
        } else {
            return Err(PlanError::UnknownProfile(profile));
        };
        if !cfg.agents.contains_key(&decision.agent) {
            // Un agente de las reglas que el usuario borró de la config.
            decision.reasons.push(format!("agente {} no existe: general", decision.agent));
            decision.agent = if cfg.agents.contains_key("general") { "general".into() }
                else { cfg.agents.keys().next().cloned().unwrap_or_default() };
        }
        let agent_key = decision.agent.clone();
        let agent: &AgentSpec = &cfg.agents[&agent_key];
        let priority = hints.priority
            .or_else(|| (decision.source == "judge").then(|| decision.priority.as_deref().and_then(Priority::parse)).flatten())
            .unwrap_or(agent.priority);

        let max_out = body["max_tokens"].as_u64().or(body["max_completion_tokens"].as_u64()).unwrap_or(1024);
        let mut dropped = HashMap::new();
        let filter = |chain: &[String], dropped: &mut HashMap<String, String>| -> Vec<String> {
            chain.iter().filter(|m| {
                let spec = &cfg.models[*m];
                let missing: Vec<&str> = requirements.iter().copied().filter(|r| !spec.capabilities.contains(*r)).collect();
                if !missing.is_empty() {
                    dropped.insert((*m).clone(), format!("no admite {}", missing.join(", ")));
                    false
                } else if f.est_tokens + max_out > spec.context {
                    dropped.insert((*m).clone(), format!("contexto {} < {}", spec.context, f.est_tokens + max_out));
                    false
                } else {
                    true
                }
            }).cloned().collect()
        };
        let mut chain = filter(&agent.chain, &mut dropped);
        if chain.is_empty() {
            // Nadie de la cadena puede: cualquier modelo del catálogo que sí, del más barato al más caro.
            let mut catalog: Vec<String> = cfg.models.keys().filter(|m| !agent.chain.contains(m)).cloned().collect();
            catalog.sort_by_key(|m| (cfg.models[m].cost, std::cmp::Reverse(cfg.models[m].context)));
            chain = filter(&catalog, &mut dropped);
            decision.reasons.push("la cadena del agente no tiene modelos capaces: se usa el catálogo".into());
        }
        if chain.is_empty() {
            return Err(PlanError::NoCapableModel(format!(
                "ningún modelo cumple {requirements:?} con ~{} tokens", f.est_tokens)));
        }
        let ranked = rank_with(&cfg.learning, &cfg.models, &self.telemetry, &chain, &decision.agent,
            &decision.bucket(), priority);
        Ok(RoutePlan { profile, chain: ranked.iter().map(|r| r.model.clone()).collect(), decision, features: f,
            priority, agent: Some(agent_key), ranked, dropped, requirements })
    }

    pub fn record_success(&self, ctx: &CallCtx, group: &str, d: &Deployment, latency: Option<f64>,
                          quality: Option<f64>, usage: Option<&Value>) {
        let usage = usage.filter(|u| !u.is_null());
        self.telemetry.record(&Row {
            request_id: ctx.request_id.clone(), profile: ctx.profile.clone(), agent: ctx.agent.clone(),
            bucket: ctx.bucket.clone(), route_source: ctx.source.clone(), model: group.into(),
            provider: Some(d.provider.clone()), upstream_model: Some(d.model.clone()),
            ok: true, is_final: true, latency, stream: ctx.stream,
            prompt_tokens: usage.and_then(|u| u["prompt_tokens"].as_u64()),
            completion_tokens: usage.and_then(|u| u["completion_tokens"].as_u64()),
            quality_auto: quality, style: ctx.style.clone(), saved_chars: ctx.saved_chars,
            client: ctx.client.clone(), ..Default::default()
        });
    }
}

fn skip(model: &str, why: &str, message: &str) -> Attempt {
    Attempt { model: model.into(), ok: false, latency: 0.0, kind: None, status: None, skipped: Some(why.into()),
        message: message.into() }
}

pub fn trunc(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[derive(Debug, Clone, Default)]
pub struct Hints {
    pub agent: Option<String>,
    pub priority: Option<Priority>,
    pub judge: bool,
    pub style: Option<crate::savings::Style>,
}

/// Aplica los parámetros por defecto del agente sin pisar los de la petición.
pub fn prepare_body(body: &Value, agent: Option<&AgentSpec>) -> Value {
    let mut out = serde_json::Map::new();
    if let Some(a) = agent {
        for (k, v) in &a.params {
            out.insert(k.clone(), v.clone());
        }
    }
    if let Some(obj) = body.as_object() {
        for (k, v) in obj {
            if !matches!(k.as_str(), "model" | "orchestrator" | "stream") {
                out.insert(k.clone(), v.clone());
            }
        }
    }
    if let Some(sp) = agent.and_then(|a| a.system_prompt.as_ref()).filter(|s| !s.is_empty()) {
        if let Some(Value::Array(msgs)) = out.get_mut("messages") {
            if msgs.first().and_then(|m| m["role"].as_str()) != Some("system") {
                msgs.insert(0, json!({"role": "system", "content": sp}));
            }
        }
    }
    Value::Object(out)
}

// ---------------------------------------------------------------------------
// Juez: prompt, esquema y parseo
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, serde::Deserialize)]
pub struct JudgeOut {
    pub agent: String,
    #[serde(default = "d_text")]
    pub modality: String,
    #[serde(default = "d_low")]
    pub reasoning: String,
    #[serde(default)]
    pub tools: bool,
    #[serde(default = "d_small")]
    pub context: String,
    #[serde(default = "d_quality")]
    pub priority: String,
}

fn d_text() -> String { "text".into() }
fn d_low() -> String { "low".into() }
fn d_small() -> String { "small".into() }
fn d_quality() -> String { "quality".into() }

const JUDGE_SYSTEM: &str = "Eres el router de un gateway de modelos de IA. No respondes al usuario: clasificas su petición.
Elige el agente que mejor la resuelve:
{agents}
Campos:
- reasoning: cuánto hay que pensar (low: respuesta directa; high: varios pasos, depurar, demostrar).
- tools: si la tarea necesita ejecutar herramientas o acciones.
- context: small (<4k tokens), medium, large (mucho código o documentos).
- priority: quality si el resultado debe ser bueno; speed si es algo rápido o interactivo; cost si es trivial.
Responde SOLO el JSON, sin texto alrededor.";

pub fn judge_body(agents: &[(String, String)], digest: &str) -> Value {
    let list: Vec<String> = agents.iter().map(|(n, d)| format!("- {n}: {d}")).collect();
    let names: Vec<&str> = agents.iter().map(|(n, _)| n.as_str()).collect();
    json!({
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM.replace("{agents}", &list.join("\n"))},
            {"role": "user", "content": digest},
        ],
        "temperature": 0,
        "max_tokens": 200,
        "response_format": {"type": "json_schema", "json_schema": {"name": "route", "strict": true, "schema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": names},
                "modality": {"type": "string", "enum": ["text", "image", "audio", "video", "document", "mixed"]},
                "reasoning": {"type": "string", "enum": ["low", "medium", "high"]},
                "tools": {"type": "boolean"},
                "context": {"type": "string", "enum": ["small", "medium", "large"]},
                "priority": {"type": "string", "enum": ["quality", "speed", "cost"]},
            },
            "required": ["agent", "modality", "reasoning", "tools", "context", "priority"],
            "additionalProperties": false,
        }}},
    })
}

pub fn conversation_digest(body: &Value, max_chars: usize) -> String {
    let mut lines = vec![];
    let empty = vec![];
    for m in body["messages"].as_array().unwrap_or(&empty) {
        let content = match &m["content"] {
            Value::String(s) => s.clone(),
            Value::Array(parts) => parts.iter().map(|p| match p["type"].as_str() {
                Some("text") | Some("input_text") => p["text"].as_str().unwrap_or("").to_string(),
                Some(t) => format!("[{t}]"),
                None => String::new(),
            }).collect::<Vec<_>>().join(" "),
            _ => String::new(),
        };
        lines.push(format!("{}: {}", m["role"].as_str().unwrap_or("?"), content));
    }
    let mut text = lines.join("\n");
    if let Some(tools) = body["tools"].as_array() {
        let names: Vec<&str> = tools.iter().filter_map(|t| t["function"]["name"].as_str()).collect();
        text.push_str(&format!("\n[tools disponibles: {}]", names.join(", ")));
    }
    let n = text.chars().count();
    if n > max_chars { text.chars().skip(n - max_chars).collect() } else { text }
}

pub fn parse_judge(text: &str, agents: &[&str]) -> Option<JudgeOut> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    let out: JudgeOut = serde_json::from_str(text.get(start..=end)?).ok()?;
    let valid = agents.contains(&out.agent.as_str())
        && ["low", "medium", "high"].contains(&out.reasoning.as_str())
        && ["small", "medium", "large"].contains(&out.context.as_str())
        && Priority::parse(&out.priority).is_some();
    valid.then_some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_judge_output_inside_text() {
        let t = "Claro:\n{\"agent\":\"coding\",\"modality\":\"text\",\"reasoning\":\"high\",\"tools\":true,\"context\":\"large\",\"priority\":\"quality\"}";
        let o = parse_judge(t, &["coding", "general"]).unwrap();
        assert_eq!(o.agent, "coding");
        assert!(o.tools);
        assert!(parse_judge(t, &["general"]).is_none());
        assert!(parse_judge("nada", &["coding"]).is_none());
    }

    #[test]
    fn prepare_body_keeps_request_params() {
        let agent = AgentSpec { description: String::new(), chain: vec![], priority: Priority::Quality, timeout: None,
            params: serde_json::from_value(json!({"temperature": 0.2, "top_p": 0.9})).unwrap(),
            system_prompt: Some("Eres conciso.".into()) };
        let body = json!({"model": "auto", "temperature": 0.7, "orchestrator": {"priority": "speed"},
            "messages": [{"role": "user", "content": "hola"}]});
        let out = prepare_body(&body, Some(&agent));
        assert_eq!(out["temperature"], 0.7);
        assert_eq!(out["top_p"], 0.9);
        assert!(out.get("model").is_none() && out.get("orchestrator").is_none());
        assert_eq!(out["messages"][0]["role"], "system");
    }

    #[test]
    fn digest_keeps_the_tail() {
        let body = json!({"messages": [{"role": "user", "content": [
            {"type": "text", "text": "mira"}, {"type": "image_url", "image_url": {"url": "x"}}]}]});
        let d = conversation_digest(&body, 1000);
        assert!(d.contains("[image_url]"));
        assert_eq!(conversation_digest(&json!({"messages": [{"role": "user", "content": "abcdef"}]}), 3), "def");
    }
}
