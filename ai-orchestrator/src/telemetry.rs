//! Telemetría: una fila por intento contra un modelo (SQLite).
//!
//! De aquí salen las estadísticas con las que el scorer reordena las cadenas, lo gastado hoy
//! por proveedor (para los presupuestos de cuota al reiniciar) y la lista de peticiones de la UI.

use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use std::collections::HashMap;
use std::sync::Mutex;

use crate::reliability::now;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    request_id TEXT NOT NULL,
    profile TEXT,
    agent TEXT NOT NULL,
    bucket TEXT,
    route_source TEXT,
    model TEXT NOT NULL,
    provider TEXT,
    upstream_model TEXT,
    ok INTEGER NOT NULL,
    final INTEGER NOT NULL DEFAULT 0,
    latency REAL,
    error_kind TEXT,
    status INTEGER,
    stream INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    quality_auto REAL,
    quality_user REAL
);
CREATE INDEX IF NOT EXISTS calls_agent_model ON calls(agent, model, ts);
CREATE INDEX IF NOT EXISTS calls_request ON calls(request_id);
CREATE INDEX IF NOT EXISTS calls_provider ON calls(provider, ts);
";

#[derive(Debug, Default, Clone)]
pub struct Row {
    pub request_id: String,
    pub profile: String,
    pub agent: String,
    pub bucket: Option<String>,
    pub route_source: String,
    pub model: String,
    pub provider: Option<String>,
    pub upstream_model: Option<String>,
    pub ok: bool,
    pub is_final: bool,
    pub latency: Option<f64>,
    pub error_kind: Option<String>,
    pub status: Option<u16>,
    pub stream: bool,
    pub prompt_tokens: Option<u64>,
    pub completion_tokens: Option<u64>,
    pub quality_auto: Option<f64>,
}

#[derive(Debug, Default, Clone, Copy, Serialize)]
pub struct Stat {
    pub n: u64,
    pub ok: u64,
    pub latency: Option<f64>,
    pub quality: Option<f64>,
    pub q_n: u64,
}

type StatsCache = HashMap<(String, Option<String>, u32), (f64, HashMap<String, Stat>)>;

pub struct Telemetry {
    db: Mutex<Connection>,
    cache: Mutex<StatsCache>,
}

impl Telemetry {
    pub fn open(path: &str) -> anyhow::Result<Self> {
        if path != ":memory:" {
            if let Some(dir) = std::path::Path::new(path).parent() {
                std::fs::create_dir_all(dir)?;
            }
        }
        let db = Connection::open(path)?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;
        db.execute_batch(SCHEMA)?;
        Ok(Self { db: Mutex::new(db), cache: Mutex::new(HashMap::new()) })
    }

    pub fn record(&self, r: &Row) {
        let db = self.db.lock().unwrap();
        let res = db.execute(
            "INSERT INTO calls (ts, request_id, profile, agent, bucket, route_source, model, provider, upstream_model,
                ok, final, latency, error_kind, status, stream, prompt_tokens, completion_tokens, quality_auto)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18)",
            params![now(), r.request_id, r.profile, r.agent, r.bucket, r.route_source, r.model, r.provider,
                r.upstream_model, r.ok, r.is_final, r.latency, r.error_kind, r.status, r.stream,
                r.prompt_tokens, r.completion_tokens, r.quality_auto],
        );
        if let Err(e) = res {
            tracing::warn!("telemetría: {e}");
        }
    }

    pub fn set_quality(&self, request_id: &str, quality: f64) -> usize {
        let n = self.db.lock().unwrap()
            .execute("UPDATE calls SET quality_user = ?1 WHERE request_id = ?2 AND final = 1", params![quality, request_id])
            .unwrap_or(0);
        self.cache.lock().unwrap().clear();
        n
    }

    /// Estadísticas por modelo de un agente (y subtipo, si se pide), cacheadas 30 s.
    pub fn stats(&self, agent: &str, bucket: Option<&str>, window_days: u32) -> HashMap<String, Stat> {
        let key = (agent.to_string(), bucket.map(String::from), window_days);
        let t = now();
        if let Some((exp, v)) = self.cache.lock().unwrap().get(&key) {
            if *exp > t {
                return v.clone();
            }
        }
        let since = t - window_days as f64 * 86400.0;
        let sql = "SELECT model, COUNT(*), SUM(ok),
                AVG(CASE WHEN ok = 1 THEN latency END),
                AVG(CASE WHEN ok = 1 THEN COALESCE(quality_user, quality_auto) END),
                SUM(CASE WHEN ok = 1 AND COALESCE(quality_user, quality_auto) IS NOT NULL THEN 1 ELSE 0 END)
            FROM calls WHERE agent = ?1 AND ts >= ?2 AND (?3 IS NULL OR bucket = ?3) GROUP BY model";
        let mut out = HashMap::new();
        {
            let db = self.db.lock().unwrap();
            if let Ok(mut st) = db.prepare_cached(sql) {
                let rows = st.query_map(params![agent, since, bucket], |r| {
                    Ok((r.get::<_, String>(0)?, Stat {
                        n: r.get::<_, i64>(1)? as u64,
                        ok: r.get::<_, Option<i64>>(2)?.unwrap_or(0) as u64,
                        latency: r.get(3)?,
                        quality: r.get(4)?,
                        q_n: r.get::<_, Option<i64>>(5)?.unwrap_or(0) as u64,
                    }))
                });
                if let Ok(rows) = rows {
                    out.extend(rows.flatten());
                }
            };
        }
        self.cache.lock().unwrap().insert(key, (t + 30.0, out.clone()));
        out
    }

    /// Peticiones y tokens enviados desde `since`, por (proveedor, modelo del proveedor).
    /// Los 429 no cuentan: no consumen cuota.
    pub fn usage_since(&self, provider: &str, since: f64) -> Vec<(String, u64, u64)> {
        let db = self.db.lock().unwrap();
        let mut st = match db.prepare_cached(
            "SELECT upstream_model, COUNT(*), COALESCE(SUM(COALESCE(prompt_tokens,0) + COALESCE(completion_tokens,0)),0)
             FROM calls WHERE provider = ?1 AND ts >= ?2 AND COALESCE(status, 0) != 429 AND upstream_model IS NOT NULL
             GROUP BY upstream_model",
        ) {
            Ok(s) => s,
            Err(_) => return vec![],
        };
        st.query_map(params![provider, since], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)? as u64, r.get::<_, i64>(2)? as u64))
        })
        .map(|rows| rows.flatten().collect())
        .unwrap_or_default()
    }

    pub fn summary(&self, window_days: u32) -> Vec<serde_json::Value> {
        let since = now() - window_days as f64 * 86400.0;
        let db = self.db.lock().unwrap();
        let mut st = match db.prepare_cached(
            "SELECT agent, model, COUNT(*), SUM(ok), AVG(CASE WHEN ok = 1 THEN latency END),
                    AVG(CASE WHEN ok = 1 THEN COALESCE(quality_user, quality_auto) END)
             FROM calls WHERE ts >= ?1 GROUP BY agent, model ORDER BY agent, COUNT(*) DESC",
        ) {
            Ok(s) => s,
            Err(_) => return vec![],
        };
        st.query_map(params![since], |r| {
            let n: i64 = r.get(2)?;
            let ok: Option<i64> = r.get(3)?;
            Ok(serde_json::json!({
                "agent": r.get::<_, String>(0)?, "model": r.get::<_, String>(1)?, "calls": n,
                "success_rate": ok.unwrap_or(0) as f64 / n.max(1) as f64,
                "latency": r.get::<_, Option<f64>>(4)?, "quality": r.get::<_, Option<f64>>(5)?,
            }))
        })
        .map(|rows| rows.flatten().collect())
        .unwrap_or_default()
    }

    pub fn recent(&self, limit: u32) -> Vec<serde_json::Value> {
        let db = self.db.lock().unwrap();
        let mut st = match db.prepare_cached(
            "SELECT ts, request_id, profile, agent, route_source, model, provider, upstream_model, ok, final,
                    latency, error_kind, status, stream, prompt_tokens, completion_tokens,
                    COALESCE(quality_user, quality_auto)
             FROM calls ORDER BY id DESC LIMIT ?1",
        ) {
            Ok(s) => s,
            Err(_) => return vec![],
        };
        st.query_map(params![limit], |r| {
            Ok(serde_json::json!({
                "ts": r.get::<_, f64>(0)?, "request_id": r.get::<_, String>(1)?,
                "profile": r.get::<_, Option<String>>(2)?, "agent": r.get::<_, String>(3)?,
                "route_source": r.get::<_, Option<String>>(4)?, "model": r.get::<_, String>(5)?,
                "provider": r.get::<_, Option<String>>(6)?, "upstream_model": r.get::<_, Option<String>>(7)?,
                "ok": r.get::<_, bool>(8)?, "final": r.get::<_, bool>(9)?, "latency": r.get::<_, Option<f64>>(10)?,
                "error_kind": r.get::<_, Option<String>>(11)?, "status": r.get::<_, Option<i64>>(12)?,
                "stream": r.get::<_, bool>(13)?, "prompt_tokens": r.get::<_, Option<i64>>(14)?,
                "completion_tokens": r.get::<_, Option<i64>>(15)?, "quality": r.get::<_, Option<f64>>(16)?,
            }))
        })
        .map(|rows| rows.flatten().collect())
        .unwrap_or_default()
    }

    pub fn has_request(&self, request_id: &str) -> bool {
        self.db.lock().unwrap()
            .query_row("SELECT 1 FROM calls WHERE request_id = ?1 LIMIT 1", params![request_id], |_| Ok(()))
            .optional()
            .ok()
            .flatten()
            .is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(model: &str, ok: bool, latency: f64, q: Option<f64>) -> Row {
        Row {
            request_id: uuid::Uuid::new_v4().to_string(), agent: "coding".into(), bucket: Some("b".into()),
            model: model.into(), provider: Some("or".into()), upstream_model: Some(format!("{model}:free")),
            ok, is_final: ok, latency: Some(latency), quality_auto: q, prompt_tokens: Some(10),
            completion_tokens: Some(5), ..Default::default()
        }
    }

    #[test]
    fn aggregates_and_feedback() {
        let t = Telemetry::open(":memory:").unwrap();
        t.record(&row("a", true, 2.0, Some(0.8)));
        t.record(&row("a", false, 1.0, None));
        let mut r = row("b", true, 4.0, Some(0.8));
        r.request_id = "req-1".into();
        t.record(&r);
        let s = t.stats("coding", Some("b"), 14);
        assert_eq!((s["a"].n, s["a"].ok), (2, 1));
        assert_eq!(s["a"].latency, Some(2.0));
        assert_eq!(t.set_quality("req-1", 0.1), 1);
        let s = t.stats("coding", None, 14);
        assert_eq!(s["b"].quality, Some(0.1));
        let u = t.usage_since("or", 0.0);
        assert_eq!(u.iter().map(|x| x.1).sum::<u64>(), 3);
        assert_eq!(u.iter().map(|x| x.2).sum::<u64>(), 45);
    }
}
