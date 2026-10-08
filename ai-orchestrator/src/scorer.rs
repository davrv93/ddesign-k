//! Scorer: reordena la cadena de un agente con lo que dice la telemetría.
//!
//! ```text
//! puntaje = w_calidad·Q + w_éxito·S + w_velocidad·V + w_costo·C + bonus_de_posición
//! ```
//! Q, S y V se suavizan con un prior: sin datos valen el prior; con muchos, lo observado. El
//! bonus de posición mantiene el orden de la config mientras no haya datos y se desvanece al
//! acumularlos. Las estadísticas se buscan primero en el subtipo exacto de tarea (bucket) y,
//! si ahí no hay muestras suficientes, en el agente entero.

use crate::config::{Learning, ModelSpec, Priority};
use crate::telemetry::{Stat, Telemetry};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, HashMap};

const PRIOR_SUCCESS: f64 = 0.9;
const PRIOR_QUALITY: f64 = 0.75;
const PRIOR_SPEED: f64 = 0.5;
const POSITION_BONUS: f64 = 0.06;

#[derive(Debug, Clone, Serialize)]
pub struct Ranked {
    pub model: String,
    pub score: f64,
    pub samples: u64,
}

pub fn rank(
    cfg: &Learning,
    models: &BTreeMap<String, ModelSpec>,
    chain: &[String],
    fine: &HashMap<String, Stat>,
    coarse: &HashMap<String, Stat>,
    priority: Priority,
    rnd: impl FnMut() -> f64,
) -> Vec<Ranked> {
    let mut rnd = rnd;
    if !cfg.enabled || chain.len() < 2 {
        return chain.iter().map(|m| Ranked { model: m.clone(), score: 0.0, samples: 0 }).collect();
    }
    let w = cfg.weights(priority);
    let m_prior = (cfg.min_samples as f64 / 2.0).max(1.0);
    let len = chain.len() as f64;
    let mut ranked: Vec<Ranked> = chain
        .iter()
        .enumerate()
        .map(|(i, model)| {
            let st = match fine.get(model) {
                Some(s) if s.n >= cfg.min_samples as u64 => *s,
                _ => coarse.get(model).copied().unwrap_or_default(),
            };
            let n = st.n as f64;
            let succ = (st.ok as f64 + m_prior * PRIOR_SUCCESS) / (n + m_prior);
            let q_obs = st.quality.unwrap_or(PRIOR_QUALITY);
            let qual = (st.q_n as f64 * q_obs + m_prior * PRIOR_QUALITY) / (st.q_n as f64 + m_prior);
            let speed = match st.latency {
                Some(lat) => {
                    let v = 1.0 / (1.0 + lat / cfg.latency_ref);
                    let n_ok = (st.ok as f64).max(1.0);
                    (n_ok * v + m_prior * PRIOR_SPEED) / (n_ok + m_prior)
                }
                None => PRIOR_SPEED,
            };
            let cost = 1.0 - models.get(model).map(|m| m.cost.min(3)).unwrap_or(1) as f64 / 3.0;
            let pos = POSITION_BONUS * (1.0 - i as f64 / len) * (m_prior / (n + m_prior));
            let score = w.quality * qual + w.success * succ + w.speed * speed + w.cost * cost + pos;
            Ranked { model: model.clone(), score: (score * 1e4).round() / 1e4, samples: st.n }
        })
        .collect();
    ranked.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));

    // Exploración: de vez en cuando, primero uno con pocos datos, para que aprenda.
    if cfg.exploration > 0.0 && rnd() < cfg.exploration {
        let cold: Vec<usize> = (1..ranked.len()).filter(|&i| ranked[i].samples < cfg.min_samples as u64).collect();
        if !cold.is_empty() {
            let pick = cold[((rnd() * cold.len() as f64) as usize).min(cold.len() - 1)];
            let r = ranked.remove(pick);
            ranked.insert(0, r);
        }
    }
    ranked
}

pub fn rank_with(
    cfg: &Learning,
    models: &BTreeMap<String, ModelSpec>,
    telemetry: &Telemetry,
    chain: &[String],
    agent: &str,
    bucket: &str,
    priority: Priority,
) -> Vec<Ranked> {
    if !cfg.enabled || chain.len() < 2 {
        return rank(cfg, models, chain, &HashMap::new(), &HashMap::new(), priority, rand::random::<f64>);
    }
    let fine = telemetry.stats(agent, Some(bucket), cfg.window_days);
    let coarse = telemetry.stats(agent, None, cfg.window_days);
    rank(cfg, models, chain, &fine, &coarse, priority, rand::random::<f64>)
}

/// Calidad aproximada sin otro LLM: detecta lo claramente malo (vacío, cortado, JSON inválido).
/// Es una cota, no una nota; la nota fina llega por `POST /v1/feedback`.
pub fn auto_quality(text: &str, finish_reason: Option<&str>, has_tool_calls: bool, wants_json: bool) -> f64 {
    let text = text.trim();
    if has_tool_calls {
        return 0.8;
    }
    if text.is_empty() {
        return 0.0;
    }
    if finish_reason == Some("length") {
        return 0.5;
    }
    if wants_json && serde_json::from_str::<Value>(text).is_err() {
        return 0.2;
    }
    if text.chars().count() < 2 {
        return 0.3;
    }
    0.8
}

pub fn quality_of_body(body: &Value, wants_json: bool) -> f64 {
    let choice = &body["choices"][0];
    let msg = &choice["message"];
    let text = msg["content"].as_str().unwrap_or("");
    let tools = msg.get("tool_calls").is_some_and(|t| t.as_array().is_some_and(|a| !a.is_empty()));
    auto_quality(text, choice["finish_reason"].as_str(), tools, wants_json)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;

    fn setup() -> (Learning, BTreeMap<String, ModelSpec>) {
        let cfg = Config::from_yaml(include_str!("../config.yaml")).unwrap();
        let mut l = cfg.learning.clone();
        l.exploration = 0.0;
        (l, cfg.models)
    }

    fn chain() -> Vec<String> {
        vec!["laguna".into(), "muse-spark".into(), "nemotron-super".into()]
    }

    #[test]
    fn without_data_keeps_config_order() {
        let (l, models) = setup();
        let r = rank(&l, &models, &chain(), &HashMap::new(), &HashMap::new(), Priority::Quality, || 0.5);
        assert_eq!(r.iter().map(|x| x.model.as_str()).collect::<Vec<_>>(), ["laguna", "muse-spark", "nemotron-super"]);
    }

    #[test]
    fn data_overrides_order() {
        let (l, models) = setup();
        let mut coarse = HashMap::new();
        coarse.insert("laguna".to_string(), Stat { n: 100, ok: 40, latency: Some(20.0), quality: Some(0.5), q_n: 40 });
        coarse.insert("muse-spark".to_string(), Stat { n: 100, ok: 98, latency: Some(5.0), quality: Some(0.94), q_n: 98 });
        let r = rank(&l, &models, &chain(), &HashMap::new(), &coarse, Priority::Quality, || 0.5);
        assert_eq!(r[0].model, "muse-spark");
        assert_eq!(r.last().unwrap().model, "laguna");
    }

    #[test]
    fn fine_bucket_wins_when_enough_samples() {
        let (l, models) = setup();
        let mut coarse = HashMap::new();
        coarse.insert("laguna".to_string(), Stat { n: 100, ok: 99, latency: Some(3.0), quality: Some(0.9), q_n: 99 });
        let mut fine = HashMap::new();
        fine.insert("laguna".to_string(), Stat { n: 30, ok: 5, latency: Some(30.0), quality: Some(0.3), q_n: 5 });
        let r = rank(&l, &models, &chain(), &fine, &coarse, Priority::Quality, || 0.5);
        assert_ne!(r[0].model, "laguna");
    }

    #[test]
    fn cost_priority_prefers_free() {
        let (l, models) = setup();
        let c = vec!["gemini-pro".to_string(), "gemini-flash-lite".to_string()];
        let r = rank(&l, &models, &c, &HashMap::new(), &HashMap::new(), Priority::Cost, || 0.5);
        assert_eq!(r[0].model, "gemini-flash-lite");
    }

    #[test]
    fn auto_quality_flags_bad_answers() {
        assert_eq!(auto_quality("", None, false, false), 0.0);
        assert_eq!(auto_quality("hola", Some("length"), false, false), 0.5);
        assert_eq!(auto_quality("no es json", None, false, true), 0.2);
        assert_eq!(auto_quality("{\"a\":1}", Some("stop"), false, true), 0.8);
    }
}
