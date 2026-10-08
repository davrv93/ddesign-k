//! ai-orchestrator: gateway OpenAI-compatible con router, fallbacks, circuit breaker,
//! cuotas, aprendizaje y UI de gestión.

pub mod api;
pub mod classifier;
pub mod config;
pub mod engine;
pub mod quotas;
pub mod reliability;
pub mod scorer;
pub mod telemetry;
