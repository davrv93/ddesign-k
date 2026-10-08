//! Fiabilidad: clasificación de errores, circuit breaker, cooldowns y backoff.
//!
//! ```text
//!   cerrado ──(N fallos en la ventana)──► abierto ──(open_seconds)──► semiabierto
//!      ▲                                     ▲                            │
//!      └──────────── éxito ◄─────────────────┴──── falla la prueba ◄──────┘
//! ```
//! En semiabierto pasa UNA petición de prueba; las demás siguen saltándose el modelo.
//! Un cooldown, en cambio, lo pone un único error que ya dice cuánto esperar (429 con
//! Retry-After, cuota diaria agotada, modelo dado de baja).

use crate::config::{Backoff, CircuitBreakerCfg, ErrorKind};
use reqwest::header::HeaderMap;
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;

pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

// ---------------------------------------------------------------------------
// Errores del upstream
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct UpstreamError {
    pub kind: ErrorKind,
    pub status: Option<u16>,
    pub message: String,
    pub retry_after: Option<f64>,
}

impl UpstreamError {
    pub fn new(kind: ErrorKind, status: Option<u16>, message: impl Into<String>) -> Self {
        Self { kind, status, message: message.into(), retry_after: None }
    }
}

impl std::fmt::Display for UpstreamError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let msg: String = self.message.chars().take(300).collect();
        write!(f, "{} ({:?}): {}", self.kind.as_str(), self.status, msg)
    }
}

const QUOTA: &[&str] = &["quota", "credits", "insufficient", "billing", "per-day", "per day", "daily limit",
    "free-models-per-day", "exceeded your current", "balance"];
const CONTEXT: &[&str] = &["context length", "context_length", "maximum context", "context window",
    "contextwindow", "too many tokens", "prompt is too long", "maximum number of tokens", "input is too long"];
const NOT_FOUND: &[&str] = &["model not found", "does not exist", "not a valid model", "unknown model",
    "no such model", "invalid model"];
const UNAVAILABLE: &[&str] = &["no endpoints found", "currently unavailable", "deprecated", "no longer available",
    "decommissioned", "temporarily unavailable"];
const UNSUPPORTED: &[&str] = &["does not support", "not supported", "unsupported", "no support for"];

fn has(text: &str, needles: &[&str]) -> bool {
    needles.iter().any(|n| text.contains(n))
}

pub fn classify_status(status: u16, body: &str) -> ErrorKind {
    use ErrorKind::*;
    let t = body.to_lowercase();
    match status {
        429 => if has(&t, QUOTA) { Quota } else { RateLimit },
        402 => Quota,
        408 | 504 => Timeout,
        401 | 403 => if has(&t, QUOTA) { Quota } else { Auth },
        404 => if has(&t, UNAVAILABLE) { Unavailable } else { NotFound },
        413 => Context,
        400..=499 => {
            if has(&t, CONTEXT) { Context }
            else if has(&t, NOT_FOUND) { NotFound }
            else if has(&t, UNAVAILABLE) { Unavailable }
            else if has(&t, UNSUPPORTED) { Unsupported }
            else if t.contains("rate limit") || t.contains("ratelimit") { RateLimit }
            else { BadRequest }
        }
        503 if has(&t, UNAVAILABLE) => Unavailable,
        _ => ServerError,
    }
}

pub fn classify_reqwest(e: &reqwest::Error) -> ErrorKind {
    if e.is_timeout() {
        if e.is_connect() { ErrorKind::Connection } else { ErrorKind::Timeout }
    } else {
        ErrorKind::Connection
    }
}

pub fn parse_retry_after(headers: &HeaderMap) -> Option<f64> {
    let get = |k: &str| headers.get(k).and_then(|v| v.to_str().ok()).map(str::trim);
    if let Some(raw) = get("retry-after") {
        if let Ok(v) = raw.parse::<f64>() {
            return Some(v.max(0.0));
        }
    }
    let reset = get("x-ratelimit-reset-requests").or_else(|| get("x-ratelimit-reset"))?;
    parse_reset(reset)
}

/// `x-ratelimit-reset*`: segundos relativos («20s», «1m30s», «0.5»), epoch en s o en ms.
pub fn parse_reset(raw: &str) -> Option<f64> {
    let raw = raw.trim();
    if let Ok(v) = raw.parse::<f64>() {
        let t = now();
        return Some(if v > 1e12 { (v / 1000.0 - t).max(0.0) } else if v > 1e9 { (v - t).max(0.0) } else { v });
    }
    // Formato duración de OpenAI: 1h2m3.5s, 250ms
    let mut total = 0.0;
    let mut num = String::new();
    let mut chars = raw.chars().peekable();
    let mut any = false;
    while let Some(c) = chars.next() {
        if c.is_ascii_digit() || c == '.' {
            num.push(c);
            continue;
        }
        let v: f64 = num.parse().ok()?;
        num.clear();
        any = true;
        total += match c {
            'h' => v * 3600.0,
            'm' if chars.peek() == Some(&'s') => {
                chars.next();
                v / 1000.0
            }
            'm' => v * 60.0,
            's' => v,
            _ => return None,
        };
    }
    if !num.is_empty() {
        return None;
    }
    any.then_some(total)
}

pub fn error_from_response(status: u16, headers: &HeaderMap, body: &str) -> UpstreamError {
    let mut e = UpstreamError::new(classify_status(status, body), Some(status), body);
    e.retry_after = parse_retry_after(headers);
    e
}

// ---------------------------------------------------------------------------
// Backoff
// ---------------------------------------------------------------------------

/// Segundos antes del reintento `attempt` (0 = primero). None si el proveedor pide esperar
/// más de `max_retry_after`: entonces conviene pasar al siguiente modelo.
pub fn backoff_delay(cfg: &Backoff, attempt: u32, retry_after: Option<f64>, rnd: f64) -> Option<f64> {
    if let (Some(ra), true) = (retry_after, cfg.respect_retry_after) {
        return (ra <= cfg.max_retry_after).then_some(ra);
    }
    let raw = (cfg.base * 2f64.powi(attempt as i32)).min(cfg.max);
    Some(raw * (0.5 + rnd / 2.0))
}

// ---------------------------------------------------------------------------
// Circuit breaker
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CircuitState {
    Closed,
    Open,
    HalfOpen,
}

#[derive(Default)]
struct Circuit {
    failures: VecDeque<f64>,
    open_until: Option<f64>,
    probing: bool,
}

#[derive(Default)]
pub struct Breaker {
    inner: Mutex<HashMap<String, Circuit>>,
}

impl Breaker {
    pub fn state(&self, name: &str, at: f64) -> (CircuitState, Option<f64>) {
        let map = self.inner.lock().unwrap();
        match map.get(name).and_then(|c| c.open_until) {
            None => (CircuitState::Closed, None),
            Some(u) if at < u => (CircuitState::Open, Some(u)),
            Some(_) => (CircuitState::HalfOpen, None),
        }
    }

    /// ¿Se puede llamar? En semiabierto, solo el primero que pregunta (la prueba).
    pub fn allow(&self, name: &str, at: f64) -> bool {
        let mut map = self.inner.lock().unwrap();
        let Some(c) = map.get_mut(name) else { return true };
        match c.open_until {
            None => true,
            Some(u) if at < u => false,
            Some(_) if c.probing => false,
            Some(_) => {
                c.probing = true;
                true
            }
        }
    }

    pub fn success(&self, name: &str) {
        self.inner.lock().unwrap().remove(name);
    }

    pub fn failure(&self, name: &str, cfg: &CircuitBreakerCfg, at: f64) -> CircuitState {
        let mut map = self.inner.lock().unwrap();
        let c = map.entry(name.to_string()).or_default();
        if c.open_until.is_some_and(|u| at >= u) {
            // Falló la prueba en semiabierto: se vuelve a abrir.
            c.open_until = Some(at + cfg.open_seconds);
            c.probing = false;
            return CircuitState::Open;
        }
        if c.open_until.is_some() {
            return CircuitState::Open;
        }
        c.failures.push_back(at);
        while c.failures.front().is_some_and(|t| *t <= at - cfg.window) {
            c.failures.pop_front();
        }
        if c.failures.len() as u32 >= cfg.failure_threshold {
            c.failures.clear();
            c.open_until = Some(at + cfg.open_seconds);
            c.probing = false;
            return CircuitState::Open;
        }
        CircuitState::Closed
    }

    /// Un error que no cuenta para el circuito no cierra ni reabre: libera la prueba.
    pub fn release_probe(&self, name: &str) {
        if let Some(c) = self.inner.lock().unwrap().get_mut(name) {
            c.probing = false;
        }
    }

    pub fn reset(&self, name: &str) {
        self.inner.lock().unwrap().remove(name);
    }
}

// ---------------------------------------------------------------------------
// Cooldowns
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct Cooldown {
    pub until: f64,
    pub reason: String,
}

#[derive(Default)]
pub struct Cooldowns {
    inner: Mutex<HashMap<String, Cooldown>>,
}

impl Cooldowns {
    pub fn get(&self, name: &str, at: f64) -> Option<Cooldown> {
        let mut map = self.inner.lock().unwrap();
        match map.get(name) {
            Some(c) if c.until > at => Some(c.clone()),
            Some(_) => {
                map.remove(name);
                None
            }
            None => None,
        }
    }

    pub fn start(&self, name: &str, seconds: f64, reason: &str, at: f64) {
        let mut map = self.inner.lock().unwrap();
        let until = at + seconds;
        if map.get(name).is_some_and(|c| c.until >= until) {
            return;
        }
        map.insert(name.to_string(), Cooldown { until, reason: reason.to_string() });
    }

    pub fn clear(&self, name: &str) {
        self.inner.lock().unwrap().remove(name);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ErrorKind::*;

    #[test]
    fn classifies_statuses() {
        assert_eq!(classify_status(429, "Rate limit exceeded"), RateLimit);
        assert_eq!(classify_status(429, "free-models-per-day limit reached"), Quota);
        assert_eq!(classify_status(429, "Rate limit exceeded: free-models-per-min"), RateLimit);
        assert_eq!(classify_status(402, ""), Quota);
        assert_eq!(classify_status(401, "invalid key"), Auth);
        assert_eq!(classify_status(404, "No endpoints found for x"), Unavailable);
        assert_eq!(classify_status(404, ""), NotFound);
        assert_eq!(classify_status(400, "This model's maximum context length is 8192"), Context);
        assert_eq!(classify_status(400, "model does not support image input"), Unsupported);
        assert_eq!(classify_status(400, "messages: field required"), BadRequest);
        assert_eq!(classify_status(503, "overloaded"), ServerError);
        assert_eq!(classify_status(504, ""), Timeout);
    }

    #[test]
    fn parses_reset_durations() {
        assert_eq!(parse_reset("20s"), Some(20.0));
        assert_eq!(parse_reset("1m30s"), Some(90.0));
        assert_eq!(parse_reset("250ms"), Some(0.25));
        assert_eq!(parse_reset("6"), Some(6.0));
        assert_eq!(parse_reset("abc"), None);
    }

    #[test]
    fn backoff_respects_retry_after() {
        let cfg = Backoff::default();
        assert_eq!(backoff_delay(&cfg, 0, Some(3.0), 0.5), Some(3.0));
        assert_eq!(backoff_delay(&cfg, 0, Some(60.0), 0.5), None);
        let d = backoff_delay(&cfg, 3, None, 1.0).unwrap();
        assert!((d - 4.0).abs() < 1e-9);
    }

    #[test]
    fn breaker_opens_half_opens_and_closes() {
        let cfg = CircuitBreakerCfg { failure_threshold: 3, window: 60.0, open_seconds: 30.0 };
        let b = Breaker::default();
        assert_eq!(b.failure("m", &cfg, 0.0), CircuitState::Closed);
        assert_eq!(b.failure("m", &cfg, 1.0), CircuitState::Closed);
        assert_eq!(b.failure("m", &cfg, 2.0), CircuitState::Open);
        assert!(!b.allow("m", 10.0));
        // pasada la apertura: una sola prueba
        assert!(b.allow("m", 40.0));
        assert!(!b.allow("m", 40.0));
        assert_eq!(b.state("m", 40.0).0, CircuitState::HalfOpen);
        // la prueba falla → abierto otra vez
        assert_eq!(b.failure("m", &cfg, 41.0), CircuitState::Open);
        assert!(!b.allow("m", 50.0));
        assert!(b.allow("m", 72.0));
        b.success("m");
        assert_eq!(b.state("m", 72.0).0, CircuitState::Closed);
    }

    #[test]
    fn failures_outside_window_do_not_count() {
        let cfg = CircuitBreakerCfg { failure_threshold: 2, window: 10.0, open_seconds: 30.0 };
        let b = Breaker::default();
        b.failure("m", &cfg, 0.0);
        assert_eq!(b.failure("m", &cfg, 20.0), CircuitState::Closed);
    }

    #[test]
    fn cooldown_keeps_the_longest() {
        let c = Cooldowns::default();
        c.start("m", 100.0, "quota", 0.0);
        c.start("m", 10.0, "rate_limit", 0.0);
        assert_eq!(c.get("m", 50.0).unwrap().reason, "quota");
        assert!(c.get("m", 101.0).is_none());
    }
}
