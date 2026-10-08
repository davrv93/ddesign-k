//! Conexión con el gateway: configuración local (~/.config/jmd/config.json) y llamadas HTTP.

use anyhow::{anyhow, bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Settings {
    /// URL del gateway, sin /v1 (p. ej. http://localhost:4000).
    #[serde(default)]
    pub url: String,
    /// Token de administración (ADMIN_TOKEN): models, stats, quotas, style…
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub admin_token: Option<String>,
    /// Clave de /v1 (una de GATEWAY_API_KEYS), si el gateway la pide.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_key: Option<String>,
}

pub fn home() -> PathBuf {
    std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")).map(PathBuf::from).unwrap_or_else(|| ".".into())
}

pub fn config_path() -> PathBuf {
    if let Some(p) = std::env::var_os("JMD_CONFIG") {
        return p.into();
    }
    let base = std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from)
        .or_else(|| std::env::var_os("APPDATA").map(PathBuf::from))
        .unwrap_or_else(|| home().join(".config"));
    base.join("jmd").join("config.json")
}

impl Settings {
    /// Archivo de configuración y, encima, las variables JMD_URL, JMD_ADMIN_TOKEN y JMD_API_KEY.
    pub fn load() -> Self {
        let mut s: Settings = std::fs::read_to_string(config_path()).ok()
            .and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default();
        let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
        if let Some(u) = env("JMD_URL") {
            s.url = u;
        }
        if let Some(t) = env("JMD_ADMIN_TOKEN") {
            s.admin_token = Some(t);
        }
        if let Some(k) = env("JMD_API_KEY") {
            s.api_key = Some(k);
        }
        if s.url.is_empty() {
            s.url = "http://localhost:4000".into();
        }
        s.url = s.url.trim_end_matches('/').trim_end_matches("/v1").to_string();
        s
    }

    pub fn save(&self) -> Result<PathBuf> {
        let path = config_path();
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        std::fs::write(&path, serde_json::to_string_pretty(self)?)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
        }
        Ok(path)
    }
}

pub struct Client {
    pub s: Settings,
    http: reqwest::Client,
}

impl Client {
    pub fn new(s: Settings) -> Self {
        let http = reqwest::Client::builder()
            .user_agent(concat!("jmd/", env!("CARGO_PKG_VERSION")))
            .connect_timeout(Duration::from_secs(5))
            .build()
            .expect("cliente HTTP");
        Self { s, http }
    }

    fn admin_token(&self) -> Result<&str> {
        self.s.admin_token.as_deref().filter(|t| !t.is_empty())
            .ok_or_else(|| anyhow!("falta el token de administración: `jmd login --token …` o JMD_ADMIN_TOKEN"))
    }

    async fn check(resp: reqwest::Response) -> Result<Value> {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        let v: Value = serde_json::from_str(&text).unwrap_or(Value::String(text.clone()));
        if !status.is_success() {
            let msg = v["error"]["message"].as_str().map(String::from).unwrap_or(text);
            if status.as_u16() == 401 {
                bail!("{msg} (HTTP 401). Revisa `jmd login`.");
            }
            bail!("{msg} (HTTP {status})");
        }
        Ok(v)
    }

    fn unreachable(&self, e: reqwest::Error) -> anyhow::Error {
        if e.is_connect() || e.is_timeout() {
            anyhow!("no se pudo conectar con {} ({e}). ¿Está levantado? `docker compose up -d` o `jmd login --url …`", self.s.url)
        } else {
            anyhow!(e)
        }
    }

    pub async fn admin_get(&self, path: &str) -> Result<Value> {
        let r = self.http.get(format!("{}{path}", self.s.url)).bearer_auth(self.admin_token()?)
            .timeout(Duration::from_secs(30)).send().await.map_err(|e| self.unreachable(e))?;
        Self::check(r).await
    }

    pub async fn admin_send(&self, method: reqwest::Method, path: &str, body: Option<&Value>) -> Result<Value> {
        let mut req = self.http.request(method, format!("{}{path}", self.s.url)).bearer_auth(self.admin_token()?)
            .timeout(Duration::from_secs(60));
        if let Some(b) = body {
            req = req.json(b);
        }
        let r = req.send().await.map_err(|e| self.unreachable(e))?;
        Self::check(r).await
    }

    fn v1(&self, method: reqwest::Method, path: &str) -> reqwest::RequestBuilder {
        let mut req = self.http.request(method, format!("{}/v1{path}", self.s.url)).header("x-jmd-client", "jmd");
        if let Some(k) = self.s.api_key.as_deref().filter(|k| !k.is_empty()) {
            req = req.bearer_auth(k);
        }
        req
    }

    pub async fn health(&self) -> Result<Value> {
        let r = self.http.get(format!("{}/health", self.s.url)).timeout(Duration::from_secs(5)).send().await
            .map_err(|e| self.unreachable(e))?;
        Self::check(r).await
    }

    pub async fn models(&self) -> Result<Value> {
        let r = self.v1(reqwest::Method::GET, "/models").timeout(Duration::from_secs(15)).send().await
            .map_err(|e| self.unreachable(e))?;
        Self::check(r).await
    }

    pub async fn route(&self, body: &Value) -> Result<Value> {
        let r = self.v1(reqwest::Method::POST, "/route").json(body).timeout(Duration::from_secs(30)).send().await
            .map_err(|e| self.unreachable(e))?;
        Self::check(r).await
    }

    pub async fn feedback(&self, request_id: &str, quality: f64) -> Result<Value> {
        let r = self.v1(reqwest::Method::POST, "/feedback")
            .json(&serde_json::json!({"request_id": request_id, "quality": quality})).send().await
            .map_err(|e| self.unreachable(e))?;
        Self::check(r).await
    }

    /// Chat en streaming: llama a `on_text` con cada trozo; devuelve las cabeceras x-orchestrator-*.
    pub async fn chat_stream(&self, body: &Value, mut on_text: impl FnMut(&str)) -> Result<ChatMeta> {
        use futures::StreamExt;
        let started = std::time::Instant::now();
        let resp = self.v1(reqwest::Method::POST, "/chat/completions").json(body).send().await
            .map_err(|e| self.unreachable(e))?;
        if !resp.status().is_success() {
            return Err(Self::check(resp).await.err().unwrap_or_else(|| anyhow!("error")));
        }
        let h = |k: &str| resp.headers().get(k).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
        let mut meta = ChatMeta {
            request_id: h("x-orchestrator-request-id"), agent: h("x-orchestrator-agent"),
            model: h("x-orchestrator-model"), upstream_model: h("x-orchestrator-upstream-model"),
            attempts: h("x-orchestrator-attempts"), ..Default::default()
        };
        let mut stream = resp.bytes_stream();
        let mut buf: Vec<u8> = vec![];
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.context("se cortó el stream")?;
            buf.extend_from_slice(&chunk);
            while let Some(pos) = buf.iter().position(|b| *b == b'\n') {
                let line: Vec<u8> = buf.drain(..=pos).collect();
                let line = String::from_utf8_lossy(&line);
                let Some(data) = line.trim().strip_prefix("data:").map(str::trim) else { continue };
                if data == "[DONE]" || data.is_empty() {
                    continue;
                }
                if let Ok(v) = serde_json::from_str::<Value>(data) {
                    if let Some(t) = v["choices"][0]["delta"]["content"].as_str() {
                        meta.text.push_str(t);
                        on_text(t);
                    }
                    if let Some(u) = v["usage"]["completion_tokens"].as_u64() {
                        meta.output_tokens = Some(u);
                    }
                }
            }
        }
        meta.seconds = started.elapsed().as_secs_f64();
        Ok(meta)
    }
}

#[derive(Debug, Default, Clone)]
pub struct ChatMeta {
    pub request_id: String,
    pub agent: String,
    pub model: String,
    pub upstream_model: String,
    pub attempts: String,
    pub text: String,
    pub output_tokens: Option<u64>,
    pub seconds: f64,
}
