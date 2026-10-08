//! Cuotas: cuánto le queda a cada proveedor y a cada modelo.
//!
//! El formato OpenAI no estandariza un endpoint de cuota, así que se combinan tres fuentes:
//!
//! 1. **Cabeceras** `x-ratelimit-{limit,remaining,reset}-{requests,tokens}` (OpenAI, Groq,
//!    Together, OpenRouter…), que se leen en cada respuesta.
//! 2. **Saldo** del proveedor, si tiene endpoint: OpenRouter (`GET /key`), DeepSeek
//!    (`/user/balance`) o cualquier JSON con punteros configurables.
//! 3. **Presupuestos locales** (`requests_per_day`, `requests_per_minute`, `tokens_per_day`)
//!    por proveedor y por deployment, contados por el gateway: sirven para las capas gratuitas
//!    que no avisan hasta el 429.
//!
//! El router consulta `blocked()` antes de llamar: un deployment sin cuota se salta sin
//! gastar una petición en recibir el 429.

use crate::config::{BalanceKind, Config, Deployment, Provider};
use crate::reliability::{now, parse_reset};
use reqwest::header::HeaderMap;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;

pub fn deployment_key(d: &Deployment) -> String {
    format!("{}/{}", d.provider, d.model)
}

pub fn day_index(at: f64, offset_hours: i32) -> i64 {
    ((at + offset_hours as f64 * 3600.0) / 86400.0).floor() as i64
}

pub fn day_start(at: f64, offset_hours: i32) -> f64 {
    day_index(at, offset_hours) as f64 * 86400.0 - offset_hours as f64 * 3600.0
}

#[derive(Default, Clone, Copy, Debug)]
struct DayCount {
    day: i64,
    requests: u64,
    tokens: u64,
}

#[derive(Default, Clone, Debug, Serialize)]
pub struct HeaderSnap {
    pub at: f64,
    pub limit_requests: Option<f64>,
    pub remaining_requests: Option<f64>,
    pub reset_requests_at: Option<f64>,
    pub limit_tokens: Option<f64>,
    pub remaining_tokens: Option<f64>,
    pub reset_tokens_at: Option<f64>,
}

#[derive(Default, Clone, Debug, Serialize)]
pub struct Balance {
    pub at: f64,
    pub remaining: Option<f64>,
    pub limit: Option<f64>,
    pub usage: Option<f64>,
    pub unit: String,
    pub is_free_tier: Option<bool>,
    pub error: Option<String>,
}

#[derive(Default)]
struct State {
    day: HashMap<String, DayCount>,
    minute: HashMap<String, VecDeque<f64>>,
    headers: HashMap<String, HeaderSnap>,
    balances: HashMap<String, Balance>,
}

#[derive(Default)]
pub struct Quotas {
    inner: Mutex<State>,
}

fn pkey(provider: &str) -> String {
    format!("p:{provider}")
}

fn dkey(d: &Deployment) -> String {
    format!("d:{}", deployment_key(d))
}

fn header_f(h: &HeaderMap, k: &str) -> Option<f64> {
    h.get(k)?.to_str().ok()?.trim().parse().ok()
}

fn header_reset(h: &HeaderMap, k: &str, at: f64) -> Option<f64> {
    parse_reset(h.get(k)?.to_str().ok()?).map(|s| at + s)
}

impl State {
    fn day_count(&mut self, key: &str, day: i64) -> &mut DayCount {
        let c = self.day.entry(key.to_string()).or_default();
        if c.day != day {
            *c = DayCount { day, requests: 0, tokens: 0 };
        }
        c
    }

    fn minute_count(&mut self, key: &str, at: f64) -> usize {
        let q = self.minute.entry(key.to_string()).or_default();
        while q.front().is_some_and(|t| *t <= at - 60.0) {
            q.pop_front();
        }
        q.len()
    }
}

impl Quotas {
    /// Motivo por el que este deployment no debe usarse ahora, o None si tiene cuota.
    pub fn blocked(&self, p: &Provider, d: &Deployment, at: f64) -> Option<String> {
        let q = &p.quota;
        let day = day_index(at, q.reset_utc_offset_hours);
        let mut s = self.inner.lock().unwrap();
        let pk = pkey(&d.provider);
        let dk = dkey(d);
        let pc = *s.day_count(&pk, day);
        if q.requests_per_day > 0 && pc.requests >= q.requests_per_day {
            return Some(format!("{}: {} peticiones hoy (límite {})", d.provider, pc.requests, q.requests_per_day));
        }
        if q.tokens_per_day > 0 && pc.tokens >= q.tokens_per_day {
            return Some(format!("{}: {} tokens hoy (límite {})", d.provider, pc.tokens, q.tokens_per_day));
        }
        if q.requests_per_minute > 0 && s.minute_count(&pk, at) as u64 >= q.requests_per_minute {
            return Some(format!("{}: {} peticiones/min", d.provider, q.requests_per_minute));
        }
        let dc = *s.day_count(&dk, day);
        if d.requests_per_day > 0 && dc.requests >= d.requests_per_day {
            return Some(format!("{}: {} peticiones hoy (límite {})", d.model, dc.requests, d.requests_per_day));
        }
        if d.requests_per_minute > 0 && s.minute_count(&dk, at) as u64 >= d.requests_per_minute {
            return Some(format!("{}: {} peticiones/min", d.model, d.requests_per_minute));
        }
        if let Some(h) = s.headers.get(&deployment_key(d)) {
            if h.remaining_requests.is_some_and(|r| r < 1.0) && h.reset_requests_at.is_some_and(|r| r > at) {
                return Some(format!("{}: el proveedor informa 0 peticiones restantes", d.model));
            }
            if h.remaining_tokens.is_some_and(|r| r < 1.0) && h.reset_tokens_at.is_some_and(|r| r > at) {
                return Some(format!("{}: el proveedor informa 0 tokens restantes", d.model));
            }
        }
        if let Some(b) = s.balances.get(&d.provider) {
            // Solo si el saldo es reciente (<15 min) y hay límite: sin límite no hay saldo que agotar.
            if at - b.at < 900.0 && b.error.is_none() && b.remaining.is_some_and(|r| r <= 0.0)
                && (b.limit.is_some() || q.balance == BalanceKind::Deepseek)
            {
                return Some(format!("{}: saldo agotado", d.provider));
            }
        }
        None
    }

    /// Cuenta una petición enviada (antes de saber cómo acaba).
    pub fn record_request(&self, p: &Provider, d: &Deployment, at: f64) {
        let day = day_index(at, p.quota.reset_utc_offset_hours);
        let mut s = self.inner.lock().unwrap();
        for k in [pkey(&d.provider), dkey(d)] {
            s.day_count(&k, day).requests += 1;
            s.minute.entry(k).or_default().push_back(at);
        }
    }

    /// Un 429 no consume cuota: se descuenta la petición.
    pub fn refund_request(&self, p: &Provider, d: &Deployment, at: f64) {
        let day = day_index(at, p.quota.reset_utc_offset_hours);
        let mut s = self.inner.lock().unwrap();
        for k in [pkey(&d.provider), dkey(d)] {
            let c = s.day_count(&k, day);
            c.requests = c.requests.saturating_sub(1);
            if let Some(q) = s.minute.get_mut(&k) {
                q.pop_back();
            }
        }
    }

    pub fn record_tokens(&self, p: &Provider, d: &Deployment, tokens: u64, at: f64) {
        let day = day_index(at, p.quota.reset_utc_offset_hours);
        let mut s = self.inner.lock().unwrap();
        for k in [pkey(&d.provider), dkey(d)] {
            s.day_count(&k, day).tokens += tokens;
        }
    }

    /// Carga lo gastado hoy (desde la telemetría) al arrancar.
    pub fn seed_today(&self, provider: &str, model: Option<&str>, requests: u64, tokens: u64, day: i64) {
        let key = match model {
            Some(m) => format!("d:{provider}/{m}"),
            None => pkey(provider),
        };
        let mut s = self.inner.lock().unwrap();
        let c = s.day_count(&key, day);
        c.requests += requests;
        c.tokens += tokens;
    }

    pub fn observe_headers(&self, d: &Deployment, h: &HeaderMap, at: f64) {
        let snap = HeaderSnap {
            at,
            limit_requests: header_f(h, "x-ratelimit-limit-requests").or_else(|| header_f(h, "x-ratelimit-limit")),
            remaining_requests: header_f(h, "x-ratelimit-remaining-requests")
                .or_else(|| header_f(h, "x-ratelimit-remaining")),
            reset_requests_at: header_reset(h, "x-ratelimit-reset-requests", at)
                .or_else(|| header_reset(h, "x-ratelimit-reset", at)),
            limit_tokens: header_f(h, "x-ratelimit-limit-tokens"),
            remaining_tokens: header_f(h, "x-ratelimit-remaining-tokens"),
            reset_tokens_at: header_reset(h, "x-ratelimit-reset-tokens", at),
        };
        if snap.limit_requests.is_some() || snap.remaining_requests.is_some() || snap.remaining_tokens.is_some() {
            self.inner.lock().unwrap().headers.insert(deployment_key(d), snap);
        }
    }

    pub fn set_balance(&self, provider: &str, b: Balance) {
        self.inner.lock().unwrap().balances.insert(provider.to_string(), b);
    }

    pub fn balance(&self, provider: &str) -> Option<Balance> {
        self.inner.lock().unwrap().balances.get(provider).cloned()
    }

    /// Informe para la UI: por proveedor y por deployment, lo usado, el límite y lo que queda.
    pub fn report(&self, cfg: &Config, at: f64) -> Value {
        let mut s = self.inner.lock().unwrap();
        let mut providers = serde_json::Map::new();
        for (name, p) in &cfg.providers {
            let q = &p.quota;
            let day = day_index(at, q.reset_utc_offset_hours);
            let pk = pkey(name);
            let c = *s.day_count(&pk, day);
            let rpm = s.minute_count(&pk, at) as u64;
            let mut deployments = vec![];
            for (group, m) in &cfg.models {
                for d in m.deployments.iter().filter(|d| &d.provider == name) {
                    let dk = dkey(d);
                    let dc = *s.day_count(&dk, day);
                    let drpm = s.minute_count(&dk, at) as u64;
                    deployments.push(json!({
                        "group": group, "model": d.model,
                        "requests_today": dc.requests, "tokens_today": dc.tokens,
                        "requests_per_day": nz(d.requests_per_day),
                        "remaining_today": rem(d.requests_per_day, dc.requests),
                        "requests_last_minute": drpm, "requests_per_minute": nz(d.requests_per_minute),
                        "headers": s.headers.get(&deployment_key(d)),
                    }));
                }
            }
            providers.insert(name.clone(), json!({
                "enabled": p.enabled,
                "has_key": !p.missing_key(),
                "balance_kind": q.balance,
                "balance": s.balances.get(name),
                "requests_today": c.requests, "tokens_today": c.tokens,
                "requests_per_day": nz(q.requests_per_day),
                "remaining_today": rem(q.requests_per_day, c.requests),
                "tokens_per_day": nz(q.tokens_per_day),
                "remaining_tokens_today": rem(q.tokens_per_day, c.tokens),
                "requests_last_minute": rpm, "requests_per_minute": nz(q.requests_per_minute),
                "resets_at": day_start(at, q.reset_utc_offset_hours) + 86400.0,
                "deployments": deployments,
            }));
        }
        Value::Object(providers)
    }
}

fn nz(v: u64) -> Option<u64> {
    (v > 0).then_some(v)
}

fn rem(limit: u64, used: u64) -> Option<u64> {
    (limit > 0).then(|| limit.saturating_sub(used))
}

// ---------------------------------------------------------------------------
// Sondeo de saldo
// ---------------------------------------------------------------------------

fn num(v: Option<&Value>) -> Option<f64> {
    match v? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

pub fn parse_balance(kind: BalanceKind, body: &Value, remaining_ptr: Option<&str>, limit_ptr: Option<&str>) -> Balance {
    let mut b = Balance { at: now(), ..Default::default() };
    match kind {
        BalanceKind::Openrouter => {
            let d = &body["data"];
            b.limit = num(d.get("limit"));
            b.usage = num(d.get("usage"));
            b.remaining = num(d.get("limit_remaining")).or_else(|| b.limit.zip(b.usage).map(|(l, u)| l - u));
            b.is_free_tier = d.get("is_free_tier").and_then(Value::as_bool);
            b.unit = "USD".into();
        }
        BalanceKind::Deepseek => {
            let info = &body["balance_infos"][0];
            b.remaining = num(info.get("total_balance"));
            b.unit = info.get("currency").and_then(Value::as_str).unwrap_or("").to_string();
            if body.get("is_available").and_then(Value::as_bool) == Some(false) {
                b.remaining = Some(0.0);
            }
        }
        BalanceKind::Json => {
            b.remaining = remaining_ptr.and_then(|p| num(body.pointer(p)));
            b.limit = limit_ptr.and_then(|p| num(body.pointer(p)));
        }
        BalanceKind::None => {}
    }
    if b.remaining.is_none() && kind != BalanceKind::None {
        b.error = Some("la respuesta no trae el saldo donde se esperaba".into());
    }
    b
}

pub async fn probe_balance(client: &reqwest::Client, p: &Provider) -> Option<Balance> {
    let q = &p.quota;
    let url = match q.balance {
        BalanceKind::None => return None,
        BalanceKind::Openrouter => q.balance_url.clone().unwrap_or_else(|| format!("{}/key", p.base_url.trim_end_matches('/'))),
        _ => q.balance_url.clone()?,
    };
    let mut req = client.get(&url).timeout(std::time::Duration::from_secs(15));
    if let Some(k) = p.key() {
        req = req.bearer_auth(k);
    }
    let fail = |e: String| Some(Balance { at: now(), error: Some(e), ..Default::default() });
    let resp = match req.send().await {
        Ok(r) => r,
        Err(e) => return fail(format!("sin conexión: {e}")),
    };
    let status = resp.status();
    let body: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => return fail(format!("HTTP {status}: respuesta no JSON ({e})")),
    };
    if !status.is_success() {
        return fail(format!("HTTP {status}: {}", body.to_string().chars().take(200).collect::<String>()));
    }
    Some(parse_balance(q.balance, &body, q.remaining_pointer.as_deref(), q.limit_pointer.as_deref()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::QuotaCfg;
    use reqwest::header::HeaderValue;

    fn provider(rpd: u64, rpm: u64) -> Provider {
        Provider {
            base_url: "http://x".into(),
            quota: QuotaCfg { requests_per_day: rpd, requests_per_minute: rpm, ..Default::default() },
            enabled: true,
            ..Default::default()
        }
    }

    fn dep() -> Deployment {
        Deployment { provider: "or".into(), model: "m".into(), requests_per_day: 0, requests_per_minute: 0 }
    }

    #[test]
    fn daily_budget_blocks_and_resets_next_day() {
        let q = Quotas::default();
        let p = provider(2, 0);
        let d = dep();
        let t = 1_000_000.0 * 86400.0 / 86400.0;
        q.record_request(&p, &d, t);
        assert!(q.blocked(&p, &d, t).is_none());
        q.record_request(&p, &d, t);
        assert!(q.blocked(&p, &d, t).unwrap().contains("límite 2"));
        assert!(q.blocked(&p, &d, t + 86400.0).is_none());
    }

    #[test]
    fn refund_on_429() {
        let q = Quotas::default();
        let p = provider(1, 0);
        let d = dep();
        q.record_request(&p, &d, 10.0);
        q.refund_request(&p, &d, 10.0);
        assert!(q.blocked(&p, &d, 10.0).is_none());
    }

    #[test]
    fn per_minute_window() {
        let q = Quotas::default();
        let p = provider(0, 2);
        let d = dep();
        q.record_request(&p, &d, 0.0);
        q.record_request(&p, &d, 1.0);
        assert!(q.blocked(&p, &d, 2.0).is_some());
        assert!(q.blocked(&p, &d, 61.5).is_none());
    }

    #[test]
    fn headers_with_zero_remaining_block_until_reset() {
        let q = Quotas::default();
        let p = provider(0, 0);
        let d = dep();
        let mut h = HeaderMap::new();
        h.insert("x-ratelimit-limit-requests", HeaderValue::from_static("100"));
        h.insert("x-ratelimit-remaining-requests", HeaderValue::from_static("0"));
        h.insert("x-ratelimit-reset-requests", HeaderValue::from_static("30s"));
        q.observe_headers(&d, &h, 1000.0);
        assert!(q.blocked(&p, &d, 1010.0).is_some());
        assert!(q.blocked(&p, &d, 1031.0).is_none());
    }

    #[test]
    fn parses_balances() {
        let or = json!({"data": {"limit": 2.0, "usage": 1.74, "limit_remaining": 0.26, "is_free_tier": false}});
        let b = parse_balance(BalanceKind::Openrouter, &or, None, None);
        assert_eq!(b.remaining, Some(0.26));
        let ds = json!({"is_available": true, "balance_infos": [{"currency": "USD", "total_balance": "4.20"}]});
        assert_eq!(parse_balance(BalanceKind::Deepseek, &ds, None, None).remaining, Some(4.2));
        let js = json!({"credits": {"left": 7, "total": 10}});
        let b = parse_balance(BalanceKind::Json, &js, Some("/credits/left"), Some("/credits/total"));
        assert_eq!((b.remaining, b.limit), (Some(7.0), Some(10.0)));
    }

    #[test]
    fn day_start_respects_offset() {
        // 2026-10-08 10:00 UTC; con -8 h el día empezó el 2026-10-08 08:00 UTC
        let t = 1_791_453_600.0;
        let s = day_start(t, -8);
        assert!(s <= t && t - s < 86400.0);
        assert_eq!(((s + -8.0 * 3600.0) % 86400.0 + 86400.0) % 86400.0, 0.0);
    }
}
