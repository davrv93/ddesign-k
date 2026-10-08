//! `jmd setup`: conecta los agentes de terminal con el gateway.
//!
//! - **Claude Code** habla la API de Anthropic → `ANTHROPIC_BASE_URL` apunta al gateway, que
//!   expone `/v1/messages`. Los nombres que pide (`claude-sonnet-…`) los traduce
//!   `compat.model_aliases` a un agente.
//! - **OpenCode** habla OpenAI → un proveedor `jmd` en `opencode.json` con los agentes como modelos.
//! - **RTK** (rtk-ai/rtk) comprime la salida de los comandos en el cliente; su hook es para
//!   Claude Code y se instala con `rtk init --global`.
//! - **caveman** (JuliusBrussee/caveman) es un plugin de Claude Code; el mismo efecto para
//!   cualquier agente lo da el estilo del gateway (`jmd style full`).
//!
//! Nunca se pisa un archivo: se fusiona con lo que haya y se deja una copia `.bak`.

use crate::client::home;
use anyhow::{bail, Context, Result};
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::process::Command;

pub const NO_KEY: &str = "jmd-sin-clave";

fn object(existing: Option<&str>, what: &str) -> Result<Map<String, Value>> {
    match existing.map(str::trim).filter(|t| !t.is_empty()) {
        None => Ok(Map::new()),
        Some(t) => match serde_json::from_str::<Value>(t) {
            Ok(Value::Object(m)) => Ok(m),
            Ok(_) => bail!("{what} no es un objeto JSON"),
            Err(e) => bail!("{what} no es JSON válido ({e}); si tiene comentarios (JSONC), añade el bloque a mano"),
        },
    }
}

/// Claude Code: fusiona `env.ANTHROPIC_BASE_URL` y `env.ANTHROPIC_AUTH_TOKEN` en settings.json.
pub fn claude_settings(existing: Option<&str>, url: &str, token: &str) -> Result<Value> {
    let mut root = object(existing, "settings.json de Claude Code")?;
    let env = root.entry("env").or_insert_with(|| json!({}));
    let Value::Object(env) = env else { bail!("settings.json: «env» no es un objeto") };
    env.insert("ANTHROPIC_BASE_URL".into(), json!(url));
    env.insert("ANTHROPIC_AUTH_TOKEN".into(), json!(token));
    Ok(Value::Object(root))
}

/// Quita la conexión con el gateway de settings.json (para `jmd setup claude --undo`).
pub fn claude_undo(existing: &str) -> Result<Value> {
    let mut root = object(Some(existing), "settings.json de Claude Code")?;
    if let Some(Value::Object(env)) = root.get_mut("env") {
        env.remove("ANTHROPIC_BASE_URL");
        env.remove("ANTHROPIC_AUTH_TOKEN");
    }
    Ok(Value::Object(root))
}

/// OpenCode: proveedor `jmd` (OpenAI-compatible) con `auto` y los agentes como modelos.
pub fn opencode_config(existing: Option<&str>, url: &str, models: &[(String, String)], api_key: &str) -> Result<Value> {
    let mut root = object(existing, "opencode.json")?;
    root.entry("$schema").or_insert_with(|| json!("https://opencode.ai/config.json"));
    let mut list = Map::new();
    for (id, desc) in models {
        let name = if desc.is_empty() { id.clone() } else { format!("{id} · {desc}") };
        list.insert(id.clone(), json!({"name": name}));
    }
    let provider = root.entry("provider").or_insert_with(|| json!({}));
    let Value::Object(provider) = provider else { bail!("opencode.json: «provider» no es un objeto") };
    provider.insert("jmd".into(), json!({
        "npm": "@ai-sdk/openai-compatible",
        "name": "JMD (gateway)",
        "options": {"baseURL": format!("{url}/v1"), "apiKey": api_key},
        "models": list,
    }));
    root.entry("model").or_insert_with(|| json!("jmd/auto"));
    if models.iter().any(|(id, _)| id == "cheap") {
        root.entry("small_model").or_insert_with(|| json!("jmd/cheap"));
    }
    Ok(Value::Object(root))
}

pub fn claude_path(project: bool) -> PathBuf {
    if project { PathBuf::from(".claude").join("settings.local.json") } else { home().join(".claude").join("settings.json") }
}

pub fn opencode_path(project: bool) -> PathBuf {
    if project {
        PathBuf::from("opencode.json")
    } else {
        std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).unwrap_or_else(|| home().join(".config"))
            .join("opencode").join("opencode.json")
    }
}

/// Escribe el JSON dejando una copia `.bak` del original. Devuelve la ruta de la copia.
pub fn write_with_backup(path: &Path, value: &Value) -> Result<Option<PathBuf>> {
    if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
        std::fs::create_dir_all(dir).with_context(|| format!("no se pudo crear {}", dir.display()))?;
    }
    let backup = if path.exists() {
        let b = path.with_extension(format!("{}.bak", path.extension().and_then(|e| e.to_str()).unwrap_or("json")));
        std::fs::copy(path, &b)?;
        Some(b)
    } else {
        None
    };
    std::fs::write(path, serde_json::to_string_pretty(value)? + "\n")
        .with_context(|| format!("no se pudo escribir {}", path.display()))?;
    Ok(backup)
}

/// Añade una línea a .gitignore si el proyecto lo tiene y le falta.
pub fn gitignore(line: &str) -> Result<bool> {
    let p = Path::new(".gitignore");
    if !p.exists() {
        return Ok(false);
    }
    let text = std::fs::read_to_string(p)?;
    if text.lines().any(|l| l.trim() == line) {
        return Ok(false);
    }
    let sep = if text.ends_with('\n') || text.is_empty() { "" } else { "\n" };
    std::fs::write(p, format!("{text}{sep}{line}\n"))?;
    Ok(true)
}

pub fn which(cmd: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts: Vec<String> = if cfg!(windows) {
        std::env::var("PATHEXT").unwrap_or(".EXE;.CMD;.BAT".into()).split(';').map(|e| e.to_lowercase()).collect()
    } else {
        vec![String::new()]
    };
    for dir in std::env::split_paths(&path) {
        for ext in &exts {
            let p = dir.join(format!("{cmd}{ext}"));
            if p.is_file() {
                return Some(p);
            }
        }
    }
    None
}

fn output(cmd: &str, args: &[&str]) -> Option<(bool, String)> {
    let o = Command::new(cmd).args(args).output().ok()?;
    Some((o.status.success(), String::from_utf8_lossy(&o.stdout).to_string() + &String::from_utf8_lossy(&o.stderr)))
}

pub enum Rtk {
    Missing,
    /// Hay un `rtk`, pero no es Rust Token Killer (p. ej. Rust Type Kit).
    Other(String),
    Ok(String),
}

/// RTK de verdad tiene el subcomando `gain` (estadísticas de ahorro).
pub fn rtk() -> Rtk {
    if which("rtk").is_none() {
        return Rtk::Missing;
    }
    let version = output("rtk", &["--version"]).map(|(_, o)| o.trim().to_string()).unwrap_or_default();
    match output("rtk", &["gain", "--help"]) {
        Some((true, _)) => Rtk::Ok(version),
        _ => Rtk::Other(version),
    }
}

pub fn rtk_gain() -> Option<String> {
    match output("rtk", &["gain"]) {
        Some((true, o)) if !o.trim().is_empty() => Some(o),
        _ => None,
    }
}

pub fn rtk_init_global() -> Result<String> {
    match output("rtk", &["init", "--global"]) {
        Some((true, o)) => Ok(o),
        Some((false, o)) => bail!("`rtk init --global` falló:\n{o}"),
        None => bail!("no se pudo ejecutar rtk"),
    }
}

/// ¿Hay un plugin o skill «caveman» instalado en Claude Code?
pub fn caveman_installed() -> bool {
    fn walk(dir: &Path, depth: usize) -> bool {
        let Ok(rd) = std::fs::read_dir(dir) else { return false };
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().to_lowercase();
            if name.contains("caveman") {
                return true;
            }
            let is_dir = e.file_type().map(|t| t.is_dir()).unwrap_or(false);
            if depth > 0 && is_dir && !name.starts_with('.') && walk(&e.path(), depth - 1) {
                return true;
            }
        }
        false
    }
    let claude = home().join(".claude");
    walk(&claude.join("plugins"), 4) || walk(&claude.join("skills"), 1) || walk(Path::new(".claude"), 2)
}

pub fn version_of(cmd: &str) -> Option<String> {
    which(cmd)?;
    output(cmd, &["--version"]).map(|(_, o)| o.lines().next().unwrap_or("").trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn claude_merge_keeps_everything_else() {
        let existing = r#"{"model": "opus", "env": {"FOO": "1"}, "hooks": {"PreToolUse": []}}"#;
        let v = claude_settings(Some(existing), "http://localhost:4000", "k").unwrap();
        assert_eq!(v["model"], "opus");
        assert_eq!(v["env"]["FOO"], "1");
        assert_eq!(v["env"]["ANTHROPIC_BASE_URL"], "http://localhost:4000");
        assert_eq!(v["env"]["ANTHROPIC_AUTH_TOKEN"], "k");
        assert!(v["hooks"]["PreToolUse"].is_array());
        let undone = claude_undo(&v.to_string()).unwrap();
        assert_eq!(undone["env"], json!({"FOO": "1"}));
        assert!(claude_settings(Some("// comentario\n{}"), "u", "k").is_err());
        assert_eq!(claude_settings(None, "u", "k").unwrap()["env"]["ANTHROPIC_BASE_URL"], "u");
    }

    #[test]
    fn opencode_merge_adds_provider_without_touching_the_rest() {
        let existing = r#"{"model": "anthropic/claude", "provider": {"otro": {"npm": "x"}}, "theme": "dark"}"#;
        let models = vec![("auto".to_string(), "el router elige".to_string()), ("cheap".to_string(), String::new())];
        let v = opencode_config(Some(existing), "http://gw:4000", &models, "{env:JMD_API_KEY}").unwrap();
        assert_eq!(v["model"], "anthropic/claude");
        assert_eq!(v["small_model"], "jmd/cheap");
        assert_eq!(v["theme"], "dark");
        assert_eq!(v["provider"]["otro"]["npm"], "x");
        assert_eq!(v["provider"]["jmd"]["options"]["baseURL"], "http://gw:4000/v1");
        assert_eq!(v["provider"]["jmd"]["models"]["auto"]["name"], "auto · el router elige");
        let fresh = opencode_config(None, "http://gw:4000", &models, NO_KEY).unwrap();
        assert_eq!(fresh["model"], "jmd/auto");
        assert_eq!(fresh["$schema"], "https://opencode.ai/config.json");
    }

    #[test]
    fn backup_is_written() {
        let dir = std::env::temp_dir().join(format!("jmd-test-{}", std::process::id()));
        let p = dir.join("s.json");
        assert!(write_with_backup(&p, &json!({"a": 1})).unwrap().is_none());
        let b = write_with_backup(&p, &json!({"a": 2})).unwrap().unwrap();
        assert!(std::fs::read_to_string(b).unwrap().contains("1"));
        let _ = std::fs::remove_dir_all(dir);
    }
}
