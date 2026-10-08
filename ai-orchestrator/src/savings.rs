//! Ahorro de tokens en el gateway, para cualquier cliente (Claude Code, OpenCode, `jmd chat`…).
//!
//! - **Estilo** (a la manera de caveman): una instrucción de sistema que pide respuestas cortas.
//!   Ahorra tokens de *salida*. Niveles: off · lite · full · ultra.
//! - **Compresión de salidas de herramientas** (a la manera de RTK, pero en el servidor): los
//!   mensajes `tool` largos se limpian (ANSI, líneas repetidas) y se recortan por el medio.
//!   Ahorra tokens de *entrada*. RTK propiamente dicho actúa en el cliente, antes de que la salida
//!   del comando llegue al agente; las dos cosas se suman.
//!
//! Ninguno toca código, comandos, rutas ni mensajes de error de la respuesta: el estilo lo pide
//! explícitamente y la compresión solo actúa sobre lo que el agente le manda al modelo.

use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::LazyLock;

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "snake_case")]
pub enum Style {
    #[default]
    Off,
    Lite,
    Full,
    Ultra,
}

impl Style {
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_lowercase().as_str() {
            "off" | "no" | "normal" => Some(Self::Off),
            "lite" => Some(Self::Lite),
            "full" | "caveman" => Some(Self::Full),
            "ultra" => Some(Self::Ultra),
            _ => None,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Lite => "lite",
            Self::Full => "full",
            Self::Ultra => "ultra",
        }
    }

    pub fn instruction(&self) -> Option<&'static str> {
        match self {
            Self::Off => None,
            Self::Lite => Some(
                "Answer concisely, in the user's language. No greetings, no filler, no restating the question, \
                 no closing summaries. Keep code, commands, file paths, identifiers, numbers and error messages exact and complete.",
            ),
            Self::Full => Some(
                "Terse mode. Reply in the user's language with as few words as possible: drop articles, filler, \
                 hedging and pleasantries; fragments are fine; prefer short lists over prose. Never shorten or alter code, \
                 commands, file paths, identifiers, numbers or error messages.",
            ),
            Self::Ultra => Some(
                "Maximum compression. Telegraphic style in the user's language: keywords, arrows, abbreviations, \
                 one idea per line. No explanations unless asked. Code, commands, file paths, identifiers, numbers and \
                 error messages stay exact and complete.",
            ),
        }
    }
}

/// Añade la instrucción de estilo al mensaje de sistema (o crea uno).
pub fn apply_style(body: &mut Value, style: Style) {
    let Some(text) = style.instruction() else { return };
    let Some(msgs) = body.get_mut("messages").and_then(Value::as_array_mut) else { return };
    if let Some(first) = msgs.first_mut().filter(|m| m["role"] == "system") {
        match &mut first["content"] {
            Value::String(s) => {
                s.push_str("\n\n");
                s.push_str(text);
            }
            Value::Array(parts) => parts.push(json!({"type": "text", "text": text})),
            other => *other = json!(text),
        }
    } else {
        msgs.insert(0, json!({"role": "system", "content": text}));
    }
}

static ANSI: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\x1b\[[0-9;?]*[ -/]*[@-~]").unwrap());

/// Limpia y recorta una salida de herramienta. Devuelve el texto nuevo (o el mismo).
pub fn compress_text(text: &str, max_chars: usize) -> String {
    let clean = ANSI.replace_all(text, "");
    // Líneas idénticas seguidas → una con el contador.
    let mut out: Vec<String> = vec![];
    let mut last: Option<&str> = None;
    let mut run = 0usize;
    let flush = |out: &mut Vec<String>, line: Option<&str>, run: usize| {
        if let Some(l) = line {
            // Las líneas en blanco no llevan contador: se quedan en una.
            out.push(if run > 1 && !l.is_empty() { format!("{l}  [×{run}]") } else { l.to_string() });
        }
    };
    for line in clean.lines() {
        let line = line.trim_end();
        if Some(line) == last {
            run += 1;
            continue;
        }
        flush(&mut out, last, run);
        last = Some(line);
        run = 1;
    }
    flush(&mut out, last, run);
    // Varias líneas en blanco seguidas → una.
    out.dedup_by(|a, b| a.is_empty() && b.is_empty());
    let joined = out.join("\n");
    if max_chars == 0 || joined.chars().count() <= max_chars {
        return joined;
    }
    // Recorte por el medio: el principio (contexto) y el final (el error suele estar ahí).
    let head_budget = max_chars * 6 / 10;
    let tail_budget = max_chars - head_budget;
    let mut head: Vec<&str> = vec![];
    let mut used = 0;
    for l in &out {
        if used + l.len() + 1 > head_budget {
            break;
        }
        used += l.len() + 1;
        head.push(l);
    }
    let mut tail: Vec<&str> = vec![];
    used = 0;
    for l in out.iter().rev() {
        if used + l.len() + 1 > tail_budget || head.len() + tail.len() >= out.len() {
            break;
        }
        used += l.len() + 1;
        tail.push(l);
    }
    tail.reverse();
    let omitted = out.len() - head.len() - tail.len();
    if omitted == 0 || (head.is_empty() && tail.is_empty()) {
        // Pocas líneas pero muy largas: recorte por caracteres.
        let chars: Vec<char> = joined.chars().collect();
        let h: String = chars[..head_budget].iter().collect();
        let t: String = chars[chars.len() - tail_budget..].iter().collect();
        return format!("{h}\n[… {} caracteres omitidos por el gateway …]\n{t}", chars.len() - max_chars);
    }
    format!("{}\n[… {omitted} líneas omitidas por el gateway …]\n{}", head.join("\n"), tail.join("\n"))
}

/// Comprime los mensajes `tool` de una petición OpenAI. Devuelve los caracteres ahorrados.
pub fn compress_tool_messages(body: &mut Value, max_chars: usize) -> usize {
    let Some(msgs) = body.get_mut("messages").and_then(Value::as_array_mut) else { return 0 };
    let mut saved = 0usize;
    for m in msgs.iter_mut().filter(|m| m["role"] == "tool") {
        match &mut m["content"] {
            Value::String(s) => {
                let new = compress_text(s, max_chars);
                saved += s.chars().count().saturating_sub(new.chars().count());
                *s = new;
            }
            Value::Array(parts) => {
                for p in parts.iter_mut() {
                    if let Some(Value::String(s)) = p.get_mut("text") {
                        let new = compress_text(s, max_chars);
                        saved += s.chars().count().saturating_sub(new.chars().count());
                        *s = new;
                    }
                }
            }
            _ => {}
        }
    }
    saved
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn style_goes_into_existing_system_message() {
        let mut b = json!({"messages": [{"role": "system", "content": "Eres útil."}, {"role": "user", "content": "hola"}]});
        apply_style(&mut b, Style::Full);
        let s = b["messages"][0]["content"].as_str().unwrap();
        assert!(s.starts_with("Eres útil.") && s.contains("Terse mode"));
        assert_eq!(b["messages"].as_array().unwrap().len(), 2);

        let mut b = json!({"messages": [{"role": "user", "content": "hola"}]});
        apply_style(&mut b, Style::Lite);
        assert_eq!(b["messages"][0]["role"], "system");
        let mut b = json!({"messages": [{"role": "user", "content": "hola"}]});
        apply_style(&mut b, Style::Off);
        assert_eq!(b["messages"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn collapses_ansi_and_repeated_lines() {
        let t = "\x1b[32mok\x1b[0m test a\nwarning: x\nwarning: x\nwarning: x\n\n\n\nfin";
        assert_eq!(compress_text(t, 0), "ok test a\nwarning: x  [×3]\n\nfin");
    }

    #[test]
    fn cuts_the_middle_and_keeps_the_error_at_the_end() {
        let mut t: Vec<String> = (0..500).map(|i| format!("compilando módulo {i}")).collect();
        t.push("error[E0308]: mismatched types".into());
        let out = compress_text(&t.join("\n"), 2000);
        assert!(out.chars().count() < 2200);
        assert!(out.contains("líneas omitidas"));
        assert!(out.ends_with("error[E0308]: mismatched types"));
        assert!(out.starts_with("compilando módulo 0"));
    }

    #[test]
    fn long_single_line_is_cut_by_chars() {
        let out = compress_text(&"x".repeat(5000), 1000);
        assert!(out.contains("caracteres omitidos"));
    }

    #[test]
    fn only_tool_messages_are_touched() {
        let big = "línea\n".repeat(10) + &"y".repeat(3000);
        let mut b = json!({"messages": [
            {"role": "user", "content": big.clone()},
            {"role": "tool", "tool_call_id": "1", "content": big.clone()}]});
        let saved = compress_tool_messages(&mut b, 500);
        assert!(saved > 2000);
        assert_eq!(b["messages"][0]["content"], big);
    }
}
