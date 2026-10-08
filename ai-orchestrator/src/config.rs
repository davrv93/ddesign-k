//! Configuración: proveedores, modelos, agentes, router, fiabilidad, aprendizaje y cuotas.
//!
//! `config.yaml` es la semilla; la configuración viva está en `data/config.yaml`, que es la
//! que edita la UI. Cada cambio se valida antes de guardarse y se aplica en caliente.

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

pub const CAPABILITIES: &[&str] = &["text", "code", "reasoning", "tools", "json", "image", "audio", "video", "pdf"];
pub const MASK: &str = "••••••••";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Config {
    pub providers: BTreeMap<String, Provider>,
    pub models: BTreeMap<String, ModelSpec>,
    pub agents: BTreeMap<String, AgentSpec>,
    #[serde(default = "default_profile")]
    pub default_profile: String,
    #[serde(default)]
    pub router: RouterCfg,
    #[serde(default)]
    pub reliability: Reliability,
    #[serde(default)]
    pub learning: Learning,
}

fn default_profile() -> String {
    "auto".into()
}

// ---------------------------------------------------------------------------
// Proveedores: cualquier endpoint con formato OpenAI (`/chat/completions`, `/models`).
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Default)]
pub struct Provider {
    pub base_url: String,
    /// Clave guardada desde la UI (data/config.yaml). Si falta, se lee `api_key_env`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_key_env: Option<String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub headers: BTreeMap<String, String>,
    #[serde(default)]
    pub quota: QuotaCfg,
    #[serde(default = "yes")]
    pub enabled: bool,
}

fn yes() -> bool {
    true
}

impl Provider {
    pub fn key(&self) -> Option<String> {
        if let Some(k) = self.api_key.as_ref().filter(|k| !k.is_empty()) {
            return Some(k.clone());
        }
        self.api_key_env.as_ref().and_then(|e| std::env::var(e).ok()).filter(|k| !k.is_empty())
    }

    /// Falta la clave que se configuró. Un proveedor sin `api_key` ni `api_key_env` (Ollama
    /// local, un proxy propio) se llama sin autenticación.
    pub fn missing_key(&self) -> bool {
        self.key().is_none() && (self.api_key_env.is_some() || self.api_key.is_some())
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "snake_case")]
pub enum BalanceKind {
    /// Sin endpoint de saldo: solo cabeceras `x-ratelimit-*` y presupuestos locales.
    #[default]
    None,
    /// `GET {base_url}/key` → `data.limit_remaining`, `data.usage`, `data.limit`.
    Openrouter,
    /// `GET {balance_url}` → `balance_infos[0].total_balance`.
    Deepseek,
    /// `GET {balance_url}` y punteros JSON configurables.
    Json,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Default)]
pub struct QuotaCfg {
    #[serde(default)]
    pub balance: BalanceKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub balance_url: Option<String>,
    /// Puntero JSON (RFC 6901) al saldo restante, p. ej. `/data/remaining`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub remaining_pointer: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limit_pointer: Option<String>,
    /// Presupuestos locales del proveedor entero (0 = sin límite).
    #[serde(default)]
    pub requests_per_day: u64,
    #[serde(default)]
    pub requests_per_minute: u64,
    #[serde(default)]
    pub tokens_per_day: u64,
    /// Hora (UTC) a la que el proveedor reinicia la cuota diaria, como desfase: 0 = medianoche UTC,
    /// -8 = medianoche del Pacífico (Google AI Studio).
    #[serde(default)]
    pub reset_utc_offset_hours: i32,
}

// ---------------------------------------------------------------------------
// Modelos (grupos con uno o varios deployments) y agentes.
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Deployment {
    pub provider: String,
    pub model: String,
    /// Presupuestos de este deployment (cuota por modelo, p. ej. la capa gratis de Gemini).
    #[serde(default, skip_serializing_if = "is_zero")]
    pub requests_per_day: u64,
    #[serde(default, skip_serializing_if = "is_zero")]
    pub requests_per_minute: u64,
}

fn is_zero(v: &u64) -> bool {
    *v == 0
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ModelSpec {
    pub capabilities: BTreeSet<String>,
    #[serde(default = "default_context")]
    pub context: u64,
    /// 0 gratis · 1 barato · 2 normal · 3 caro
    #[serde(default)]
    pub cost: u8,
    pub deployments: Vec<Deployment>,
}

fn default_context() -> u64 {
    32768
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
#[serde(rename_all = "snake_case")]
pub enum Priority {
    #[default]
    Quality,
    Speed,
    Cost,
}

impl Priority {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "quality" => Some(Self::Quality),
            "speed" => Some(Self::Speed),
            "cost" => Some(Self::Cost),
            _ => None,
        }
    }
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Quality => "quality",
            Self::Speed => "speed",
            Self::Cost => "cost",
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct AgentSpec {
    #[serde(default)]
    pub description: String,
    pub chain: Vec<String>,
    #[serde(default)]
    pub priority: Priority,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout: Option<f64>,
    #[serde(default, skip_serializing_if = "serde_json::Map::is_empty")]
    pub params: serde_json::Map<String, serde_json::Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub system_prompt: Option<String>,
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct RouterCfg {
    pub rules_min_confidence: f64,
    pub long_context_tokens: u64,
    pub judge: JudgeCfg,
}

impl Default for RouterCfg {
    fn default() -> Self {
        Self { rules_min_confidence: 0.75, long_context_tokens: 32000, judge: JudgeCfg::default() }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct JudgeCfg {
    pub enabled: bool,
    pub chain: Vec<String>,
    pub timeout: f64,
    pub cache_size: usize,
    pub max_chars: usize,
}

impl Default for JudgeCfg {
    fn default() -> Self {
        Self { enabled: true, chain: vec![], timeout: 10.0, cache_size: 512, max_chars: 6000 }
    }
}

// ---------------------------------------------------------------------------
// Fiabilidad
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    RateLimit,
    Quota,
    Timeout,
    ServerError,
    Unavailable,
    Auth,
    NotFound,
    Context,
    Unsupported,
    BadRequest,
    Connection,
}

impl ErrorKind {
    pub const ALL: [ErrorKind; 11] = [
        Self::RateLimit, Self::Quota, Self::Timeout, Self::ServerError, Self::Unavailable, Self::Auth,
        Self::NotFound, Self::Context, Self::Unsupported, Self::BadRequest, Self::Connection,
    ];
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::RateLimit => "rate_limit",
            Self::Quota => "quota",
            Self::Timeout => "timeout",
            Self::ServerError => "server_error",
            Self::Unavailable => "unavailable",
            Self::Auth => "auth",
            Self::NotFound => "not_found",
            Self::Context => "context",
            Self::Unsupported => "unsupported",
            Self::BadRequest => "bad_request",
            Self::Connection => "connection",
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
pub struct ErrorPolicy {
    /// Reintentos en el MISMO modelo antes de pasar al siguiente.
    #[serde(default)]
    pub retries: u32,
    /// Si se pasa al siguiente modelo de la cadena (false = el error va al cliente).
    #[serde(default = "yes")]
    pub fallback: bool,
    /// Segundos que el modelo queda fuera tras abandonarlo por este error.
    #[serde(default)]
    pub cooldown: f64,
    /// Si cuenta como fallo para el circuit breaker.
    #[serde(default = "yes")]
    pub breaker: bool,
}

const fn pol(retries: u32, fallback: bool, cooldown: f64, breaker: bool) -> ErrorPolicy {
    ErrorPolicy { retries, fallback, cooldown, breaker }
}

pub fn default_policy(kind: ErrorKind) -> ErrorPolicy {
    use ErrorKind::*;
    match kind {
        RateLimit => pol(2, true, 30.0, true),
        Quota => pol(0, true, 3600.0, true),
        Timeout => pol(1, true, 0.0, true),
        ServerError => pol(2, true, 0.0, true),
        Unavailable => pol(0, true, 60.0, true),
        Auth => pol(0, true, 600.0, false),
        NotFound => pol(0, true, 3600.0, false),
        Context => pol(0, true, 0.0, false),
        Unsupported => pol(0, true, 0.0, false),
        BadRequest => pol(0, false, 0.0, false),
        Connection => pol(2, true, 0.0, true),
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Backoff {
    pub base: f64,
    pub max: f64,
    pub respect_retry_after: bool,
    pub max_retry_after: f64,
}

impl Default for Backoff {
    fn default() -> Self {
        Self { base: 0.5, max: 8.0, respect_retry_after: true, max_retry_after: 20.0 }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct CircuitBreakerCfg {
    pub failure_threshold: u32,
    pub window: f64,
    pub open_seconds: f64,
}

impl Default for CircuitBreakerCfg {
    fn default() -> Self {
        Self { failure_threshold: 5, window: 60.0, open_seconds: 30.0 }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Reliability {
    pub policy: BTreeMap<ErrorKind, ErrorPolicy>,
    pub backoff: Backoff,
    pub circuit_breaker: CircuitBreakerCfg,
    pub default_timeout: f64,
    pub deadline: f64,
}

impl Default for Reliability {
    fn default() -> Self {
        Self {
            policy: ErrorKind::ALL.iter().map(|k| (*k, default_policy(*k))).collect(),
            backoff: Backoff::default(),
            circuit_breaker: CircuitBreakerCfg::default(),
            default_timeout: 120.0,
            deadline: 300.0,
        }
    }
}

impl Reliability {
    pub fn policy(&self, kind: ErrorKind) -> ErrorPolicy {
        self.policy.get(&kind).copied().unwrap_or_else(|| default_policy(kind))
    }
}

// ---------------------------------------------------------------------------
// Aprendizaje
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
pub struct Weights {
    pub quality: f64,
    pub success: f64,
    pub speed: f64,
    pub cost: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Learning {
    pub enabled: bool,
    pub window_days: u32,
    pub min_samples: u32,
    pub exploration: f64,
    pub latency_ref: f64,
    pub weights: BTreeMap<Priority, Weights>,
}

impl Default for Learning {
    fn default() -> Self {
        let w = |quality, success, speed, cost| Weights { quality, success, speed, cost };
        Self {
            enabled: true,
            window_days: 14,
            min_samples: 20,
            exploration: 0.05,
            latency_ref: 10.0,
            weights: BTreeMap::from([
                (Priority::Quality, w(0.55, 0.35, 0.10, 0.0)),
                (Priority::Speed, w(0.25, 0.30, 0.45, 0.0)),
                (Priority::Cost, w(0.20, 0.30, 0.10, 0.40)),
            ]),
        }
    }
}

impl Learning {
    pub fn weights(&self, p: Priority) -> Weights {
        self.weights.get(&p).copied().unwrap_or(Weights { quality: 0.5, success: 0.4, speed: 0.1, cost: 0.0 })
    }
}

// ---------------------------------------------------------------------------
// Validación, carga y guardado
// ---------------------------------------------------------------------------

fn valid_name(n: &str) -> bool {
    !n.is_empty() && n.len() <= 64 && n.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c))
}

impl Config {
    pub fn validate(&self) -> Result<(), String> {
        for (name, p) in &self.providers {
            if !valid_name(name) {
                return Err(format!("nombre de proveedor inválido: {name:?}"));
            }
            if !(p.base_url.starts_with("http://") || p.base_url.starts_with("https://")) {
                return Err(format!("proveedor {name}: base_url debe empezar por http(s)://"));
            }
            if matches!(p.quota.balance, BalanceKind::Json | BalanceKind::Deepseek) && p.quota.balance_url.is_none() {
                return Err(format!("proveedor {name}: el saldo {:?} necesita balance_url", p.quota.balance));
            }
            if p.quota.balance == BalanceKind::Json && p.quota.remaining_pointer.is_none() {
                return Err(format!("proveedor {name}: el saldo json necesita remaining_pointer"));
            }
        }
        for (name, m) in &self.models {
            if !valid_name(name) {
                return Err(format!("nombre de modelo inválido: {name:?}"));
            }
            if m.deployments.is_empty() {
                return Err(format!("modelo {name}: sin deployments"));
            }
            for c in &m.capabilities {
                if !CAPABILITIES.contains(&c.as_str()) {
                    return Err(format!("modelo {name}: capacidad desconocida {c:?}"));
                }
            }
            for d in &m.deployments {
                if !self.providers.contains_key(&d.provider) {
                    return Err(format!("modelo {name}: proveedor desconocido {:?}", d.provider));
                }
                if d.model.trim().is_empty() {
                    return Err(format!("modelo {name}: deployment sin model"));
                }
            }
        }
        for (name, a) in &self.agents {
            if !valid_name(name) {
                return Err(format!("nombre de agente inválido: {name:?}"));
            }
            if self.models.contains_key(name) {
                return Err(format!("{name:?} es a la vez agente y modelo"));
            }
            if a.chain.is_empty() {
                return Err(format!("agente {name}: cadena vacía"));
            }
            for m in &a.chain {
                if !self.models.contains_key(m) {
                    return Err(format!("agente {name}: modelo desconocido {m:?}"));
                }
            }
        }
        for m in &self.router.judge.chain {
            if !self.models.contains_key(m) {
                return Err(format!("router.judge.chain: modelo desconocido {m:?}"));
            }
        }
        if self.agents.contains_key("auto") || self.models.contains_key("auto") {
            return Err("'auto' está reservado para el router".into());
        }
        if self.default_profile != "auto" && !self.agents.contains_key(&self.default_profile) {
            return Err(format!("default_profile desconocido: {:?}", self.default_profile));
        }
        Ok(())
    }

    pub fn from_yaml(text: &str) -> Result<Self, String> {
        let cfg: Config = serde_yaml::from_str(text).map_err(|e| format!("YAML inválido: {e}"))?;
        cfg.validate()?;
        Ok(cfg)
    }

    pub fn to_yaml(&self) -> String {
        serde_yaml::to_string(self).unwrap_or_default()
    }

    /// Copia para mostrar: las claves guardadas se tapan.
    pub fn masked(&self) -> Config {
        let mut c = self.clone();
        for p in c.providers.values_mut() {
            if p.api_key.as_ref().is_some_and(|k| !k.is_empty()) {
                p.api_key = Some(MASK.into());
            }
        }
        c
    }

    /// Al guardar desde la UI, una clave tapada significa «la que ya estaba».
    pub fn unmask_from(&mut self, previous: &Config) {
        for (name, p) in self.providers.iter_mut() {
            if p.api_key.as_deref() == Some(MASK) {
                p.api_key = previous.providers.get(name).and_then(|o| o.api_key.clone());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seed_config_is_valid() {
        let text = include_str!("../config.yaml");
        let cfg = Config::from_yaml(text).expect("config.yaml válido");
        assert!(cfg.agents.contains_key("coding"));
        assert!(!cfg.reliability.policy(ErrorKind::BadRequest).fallback);
        // ida y vuelta por YAML sin perder nada
        assert_eq!(Config::from_yaml(&cfg.to_yaml()).unwrap(), cfg);
    }

    #[test]
    fn rejects_unknown_references() {
        let mut cfg = Config::from_yaml(include_str!("../config.yaml")).unwrap();
        cfg.agents.get_mut("coding").unwrap().chain.push("no-existe".into());
        assert!(cfg.validate().unwrap_err().contains("no-existe"));
    }

    #[test]
    fn keyless_providers_are_usable() {
        let local = Provider { base_url: "http://ollama:11434/v1".into(), ..Default::default() };
        assert!(!local.missing_key());
        let env = Provider { api_key_env: Some("AIO_TEST_NO_EXISTE".into()), ..local.clone() };
        assert!(env.missing_key());
        let saved = Provider { api_key: Some("k".into()), ..env };
        assert!(!saved.missing_key());
    }

    #[test]
    fn masking_round_trip() {
        let mut cfg = Config::from_yaml(include_str!("../config.yaml")).unwrap();
        cfg.providers.get_mut("openrouter").unwrap().api_key = Some("sk-secreta".into());
        let mut shown = cfg.masked();
        assert_eq!(shown.providers["openrouter"].api_key.as_deref(), Some(MASK));
        shown.unmask_from(&cfg);
        assert_eq!(shown.providers["openrouter"].api_key.as_deref(), Some("sk-secreta"));
    }
}
