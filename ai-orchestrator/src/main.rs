//! Arranque: carga la configuración (data/config.yaml, o la semilla la primera vez), abre la
//! telemetría, sondea saldos en segundo plano y sirve la API y la UI.

use ai_orchestrator::{api, config::Config, engine::Engine, quotas::probe_balance, telemetry::Telemetry};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

fn env(k: &str, default: &str) -> String {
    std::env::var(k).ok().filter(|v| !v.is_empty()).unwrap_or_else(|| default.to_string())
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| "info,ai_orchestrator=info".into()))
        .with_ansi(std::io::IsTerminal::is_terminal(&std::io::stdout()))
        .init();

    let data_dir = PathBuf::from(env("DATA_DIR", "data"));
    std::fs::create_dir_all(&data_dir)?;
    let live = data_dir.join("config.yaml");
    let seed = PathBuf::from(env("CONFIG_SEED", "config.yaml"));
    let text = if live.exists() {
        std::fs::read_to_string(&live)?
    } else {
        let t = std::fs::read_to_string(&seed)
            .map_err(|e| anyhow::anyhow!("no se pudo leer la semilla {}: {e}", seed.display()))?;
        std::fs::write(&live, &t)?;
        tracing::info!("primera ejecución: {} copiado a {}", seed.display(), live.display());
        t
    };
    let cfg = Config::from_yaml(&text).map_err(|e| anyhow::anyhow!("{}: {e}", live.display()))?;

    let gateway_keys: Vec<String> = env("GATEWAY_API_KEYS", "").split(',').map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty()).collect();
    if gateway_keys.is_empty() {
        tracing::warn!("GATEWAY_API_KEYS vacío: /v1 acepta peticiones sin clave");
    }
    let admin_token = match std::env::var("ADMIN_TOKEN").ok().filter(|t| !t.is_empty()) {
        Some(t) => t,
        None => {
            let path = data_dir.join("admin_token");
            match std::fs::read_to_string(&path) {
                Ok(t) if !t.trim().is_empty() => t.trim().to_string(),
                _ => {
                    let t = uuid::Uuid::new_v4().simple().to_string();
                    std::fs::write(&path, &t)?;
                    tracing::warn!("ADMIN_TOKEN no definido: se generó uno y se guardó en {}", path.display());
                    t
                }
            }
        }
    };

    let telemetry = Telemetry::open(data_dir.join("telemetry.db").to_str().unwrap_or("data/telemetry.db"))?;
    let engine = Arc::new(Engine::new(cfg, live, telemetry, gateway_keys, admin_token));

    // Saldos de los proveedores que lo exponen, cada 5 minutos.
    let e = engine.clone();
    tokio::spawn(async move {
        loop {
            let cfg = e.cfg();
            for (name, p) in cfg.providers.iter().filter(|(_, p)| p.enabled) {
                if let Some(b) = probe_balance(&e.client, p).await {
                    if let Some(err) = &b.error {
                        tracing::warn!(provider = %name, "saldo: {err}");
                    }
                    e.quotas.set_balance(name, b);
                }
            }
            tokio::time::sleep(Duration::from_secs(300)).await;
        }
    });

    let addr = format!("{}:{}", env("HOST", "0.0.0.0"), env("PORT", "4000"));
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    tracing::info!("ai-orchestrator escuchando en http://{addr}  (UI en /ui/)");
    axum::serve(listener, api::router(engine))
        .with_graceful_shutdown(shutdown())
        .await?;
    Ok(())
}

/// Ctrl-C en local; SIGTERM en docker/podman stop.
async fn shutdown() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let term = async {
        if let Ok(mut s) = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            s.recv().await;
        }
    };
    #[cfg(not(unix))]
    let term = std::future::pending::<()>();
    tokio::select! {
        _ = ctrl_c => {},
        _ = term => {},
    }
    tracing::info!("apagando");
}
