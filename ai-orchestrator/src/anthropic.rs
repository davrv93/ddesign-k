//! Compatibilidad con la API de Anthropic (`POST /v1/messages`), para Claude Code y cualquier
//! cliente Anthropic. El gateway sigue hablando formato OpenAI con los proveedores: aquí solo se
//! traduce la petición a la entrada y la respuesta (o el stream) a la salida.
//!
//! ```text
//! Claude Code ──/v1/messages──► to_openai() ──► router + fallback ──► proveedor OpenAI
//!             ◄──eventos SSE─── StreamConverter ◄── chunks OpenAI ◄──┘
//! ```
//! Se descartan los bloques de razonamiento (`thinking`): llevan una firma que solo Anthropic
//! puede validar. Las herramientas de servidor (web_search de Anthropic…) no tienen equivalente.

use serde_json::{json, Map, Value};

fn text_of_blocks(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Array(blocks) => blocks
            .iter()
            .filter_map(|b| match b["type"].as_str() {
                Some("text") => b["text"].as_str().map(String::from),
                Some("image") => Some("[imagen]".into()),
                _ => None,
            })
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

fn media_part(block: &Value) -> Option<Value> {
    let src = &block["source"];
    match (block["type"].as_str()?, src["type"].as_str()?) {
        ("image", "base64") => Some(json!({"type": "image_url", "image_url": {
            "url": format!("data:{};base64,{}", src["media_type"].as_str().unwrap_or("image/png"), src["data"].as_str().unwrap_or(""))}})),
        ("image", "url") => Some(json!({"type": "image_url", "image_url": {"url": src["url"]}})),
        ("document", "base64") => Some(json!({"type": "file", "file": {
            "filename": block["title"].as_str().unwrap_or("documento.pdf"),
            "file_data": format!("data:{};base64,{}", src["media_type"].as_str().unwrap_or("application/pdf"), src["data"].as_str().unwrap_or(""))}})),
        ("document", "url") => Some(json!({"type": "file", "file": {"file_url": src["url"]}})),
        ("document", "text") => Some(json!({"type": "text", "text": src["data"]})),
        _ => None,
    }
}

fn user_content(parts: Vec<Value>) -> Value {
    if parts.iter().all(|p| p["type"] == "text") {
        json!(parts.iter().filter_map(|p| p["text"].as_str()).collect::<Vec<_>>().join("\n"))
    } else {
        json!(parts)
    }
}

/// Petición Anthropic → petición OpenAI (chat/completions).
pub fn to_openai(req: &Value) -> Result<Value, String> {
    let mut out = Map::new();
    let mut messages: Vec<Value> = vec![];
    match &req["system"] {
        Value::Null => {}
        s => {
            let text = text_of_blocks(s);
            if !text.is_empty() {
                messages.push(json!({"role": "system", "content": text}));
            }
        }
    }
    let msgs = req["messages"].as_array().ok_or("messages: debe ser una lista")?;
    for m in msgs {
        let role = m["role"].as_str().unwrap_or("user");
        match (&m["content"], role) {
            (Value::String(s), _) => messages.push(json!({"role": role, "content": s})),
            (Value::Array(blocks), "assistant") => {
                let mut text = vec![];
                let mut calls = vec![];
                for b in blocks {
                    match b["type"].as_str() {
                        Some("text") => text.push(b["text"].as_str().unwrap_or("").to_string()),
                        Some("tool_use") => calls.push(json!({
                            "id": b["id"], "type": "function",
                            "function": {"name": b["name"], "arguments": b["input"].to_string()}})),
                        _ => {} // thinking, redacted_thinking, server_tool_use…
                    }
                }
                let mut msg = json!({"role": "assistant",
                    "content": if text.is_empty() && !calls.is_empty() { Value::Null } else { json!(text.join("\n")) }});
                if !calls.is_empty() {
                    msg["tool_calls"] = json!(calls);
                }
                messages.push(msg);
            }
            (Value::Array(blocks), _) => {
                // Los tool_result van primero: en OpenAI deben seguir al mensaje con tool_calls.
                let mut parts = vec![];
                for b in blocks {
                    match b["type"].as_str() {
                        Some("tool_result") => {
                            let mut content = text_of_blocks(&b["content"]);
                            if b["is_error"].as_bool() == Some(true) {
                                content = format!("[error] {content}");
                            }
                            messages.push(json!({"role": "tool", "tool_call_id": b["tool_use_id"], "content": content}));
                        }
                        Some("text") => parts.push(json!({"type": "text", "text": b["text"]})),
                        Some("image") | Some("document") => parts.extend(media_part(b)),
                        _ => {}
                    }
                }
                if !parts.is_empty() {
                    messages.push(json!({"role": "user", "content": user_content(parts)}));
                }
            }
            _ => {}
        }
    }
    if messages.iter().all(|m| m["role"] == "system") {
        return Err("messages: no hay mensajes".into());
    }
    out.insert("messages".into(), json!(messages));
    if let Some(m) = req["model"].as_str() {
        out.insert("model".into(), json!(m));
    }
    if let Some(v) = req.get("max_tokens").filter(|v| !v.is_null()) {
        out.insert("max_tokens".into(), v.clone());
    }
    for k in ["temperature", "top_p", "stream"] {
        if let Some(v) = req.get(k).filter(|v| !v.is_null()) {
            out.insert(k.into(), v.clone());
        }
    }
    if let Some(stop) = req["stop_sequences"].as_array().filter(|s| !s.is_empty()) {
        out.insert("stop".into(), json!(stop));
    }
    if let Some(tools) = req["tools"].as_array() {
        let mapped: Vec<Value> = tools
            .iter()
            .filter(|t| t.get("input_schema").is_some())
            .map(|t| json!({"type": "function", "function": {
                "name": t["name"], "description": t["description"].as_str().unwrap_or(""),
                "parameters": t["input_schema"]}}))
            .collect();
        if !mapped.is_empty() {
            out.insert("tools".into(), json!(mapped));
            match req["tool_choice"]["type"].as_str() {
                Some("any") => { out.insert("tool_choice".into(), json!("required")); }
                Some("none") => { out.insert("tool_choice".into(), json!("none")); }
                Some("tool") => {
                    out.insert("tool_choice".into(), json!({"type": "function", "function": {"name": req["tool_choice"]["name"]}}));
                }
                _ => {}
            }
            if req["tool_choice"]["disable_parallel_tool_use"].as_bool() == Some(true) {
                out.insert("parallel_tool_calls".into(), json!(false));
            }
        }
    }
    if let Some(o) = req.get("orchestrator") {
        out.insert("orchestrator".into(), o.clone());
    }
    Ok(Value::Object(out))
}

pub fn stop_reason(finish: Option<&str>, has_tools: bool) -> &'static str {
    if has_tools {
        return "tool_use";
    }
    match finish {
        Some("length") => "max_tokens",
        Some("tool_calls") | Some("function_call") => "tool_use",
        Some("content_filter") => "refusal",
        Some("stop_sequence") => "stop_sequence",
        _ => "end_turn",
    }
}

fn tool_input(args: &str) -> Value {
    if args.trim().is_empty() {
        return json!({});
    }
    match serde_json::from_str::<Value>(args) {
        Ok(v @ Value::Object(_)) => v,
        Ok(other) => json!({"value": other}),
        Err(_) => json!({"raw": args}),
    }
}

pub fn new_id(prefix: &str) -> String {
    format!("{prefix}_{}", uuid::Uuid::new_v4().simple())
}

/// Respuesta OpenAI → respuesta Anthropic.
pub fn from_openai(resp: &Value, model: &str) -> Value {
    let choice = &resp["choices"][0];
    let msg = &choice["message"];
    let mut content = vec![];
    if let Some(t) = msg["content"].as_str().filter(|t| !t.is_empty()) {
        content.push(json!({"type": "text", "text": t}));
    }
    let calls = msg["tool_calls"].as_array().cloned().unwrap_or_default();
    for c in &calls {
        let id = c["id"].as_str().filter(|s| !s.is_empty()).map(String::from).unwrap_or_else(|| new_id("toolu"));
        content.push(json!({"type": "tool_use", "id": id, "name": c["function"]["name"],
            "input": tool_input(c["function"]["arguments"].as_str().unwrap_or(""))}));
    }
    json!({
        "id": new_id("msg"), "type": "message", "role": "assistant", "model": model, "content": content,
        "stop_reason": stop_reason(choice["finish_reason"].as_str(), !calls.is_empty()), "stop_sequence": null,
        "usage": {"input_tokens": resp["usage"]["prompt_tokens"].as_u64().unwrap_or(0),
                  "output_tokens": resp["usage"]["completion_tokens"].as_u64().unwrap_or(0)},
    })
}

pub fn error_body(status: u16, message: &str) -> Value {
    let kind = match status {
        400 | 413 | 422 => "invalid_request_error",
        401 => "authentication_error",
        403 => "permission_error",
        404 => "not_found_error",
        429 => "rate_limit_error",
        503 | 529 => "overloaded_error",
        _ => "api_error",
    };
    json!({"type": "error", "error": {"type": kind, "message": message}})
}

// ---------------------------------------------------------------------------
// Streaming
// ---------------------------------------------------------------------------

/// Parte bytes SSE en cargas `data:` completas (aunque un chunk corte una línea o un carácter).
#[derive(Default)]
pub struct SseDecoder {
    buf: Vec<u8>,
}

impl SseDecoder {
    pub fn feed(&mut self, bytes: &[u8]) -> Vec<String> {
        self.buf.extend_from_slice(bytes);
        let mut out = vec![];
        while let Some(pos) = self.buf.iter().position(|b| *b == b'\n') {
            let line: Vec<u8> = self.buf.drain(..=pos).collect();
            let line = String::from_utf8_lossy(&line);
            if let Some(data) = line.trim_end().strip_prefix("data:") {
                let data = data.trim();
                if !data.is_empty() {
                    out.push(data.to_string());
                }
            }
        }
        out
    }
}

fn event(name: &str, data: Value) -> String {
    format!("event: {name}\ndata: {data}\n\n")
}

#[derive(Default)]
struct ToolAcc {
    id: String,
    name: String,
    args: String,
}

/// Convierte chunks OpenAI en eventos Anthropic. El texto sale en vivo; las llamadas a
/// herramientas se acumulan y salen completas al final (algunos proveedores las intercalan).
pub struct StreamConverter {
    model: String,
    input_tokens: u64,
    started: bool,
    text_open: bool,
    index: usize,
    tools: Vec<(u64, ToolAcc)>,
    finish: Option<String>,
    output_chars: usize,
    usage_out: Option<u64>,
    done: bool,
}

impl StreamConverter {
    pub fn new(model: &str, input_tokens: u64) -> Self {
        Self { model: model.into(), input_tokens, started: false, text_open: false, index: 0, tools: vec![],
            finish: None, output_chars: 0, usage_out: None, done: false }
    }

    pub fn start(&mut self) -> Vec<String> {
        if self.started {
            return vec![];
        }
        self.started = true;
        vec![
            event("message_start", json!({"type": "message_start", "message": {
                "id": new_id("msg"), "type": "message", "role": "assistant", "model": self.model, "content": [],
                "stop_reason": null, "stop_sequence": null,
                "usage": {"input_tokens": self.input_tokens, "output_tokens": 1}}})),
            event("ping", json!({"type": "ping"})),
        ]
    }

    /// Una carga `data:` del stream OpenAI.
    pub fn push(&mut self, data: &str) -> Vec<String> {
        let mut out = self.start();
        if data == "[DONE]" {
            return out;
        }
        let Ok(chunk) = serde_json::from_str::<Value>(data) else { return out };
        if let Some(n) = chunk["usage"]["completion_tokens"].as_u64() {
            self.usage_out = Some(n);
        }
        if let Some(n) = chunk["usage"]["prompt_tokens"].as_u64() {
            self.input_tokens = n;
        }
        for ch in chunk["choices"].as_array().into_iter().flatten() {
            let delta = &ch["delta"];
            if let Some(t) = delta["content"].as_str().filter(|t| !t.is_empty()) {
                if !self.text_open {
                    out.push(event("content_block_start", json!({"type": "content_block_start", "index": self.index,
                        "content_block": {"type": "text", "text": ""}})));
                    self.text_open = true;
                }
                self.output_chars += t.chars().count();
                out.push(event("content_block_delta", json!({"type": "content_block_delta", "index": self.index,
                    "delta": {"type": "text_delta", "text": t}})));
            }
            for tc in delta["tool_calls"].as_array().into_iter().flatten() {
                let idx = tc["index"].as_u64().unwrap_or(self.tools.len() as u64);
                let pos = match self.tools.iter().position(|(i, _)| *i == idx) {
                    Some(p) => p,
                    None => {
                        self.tools.push((idx, ToolAcc::default()));
                        self.tools.len() - 1
                    }
                };
                let acc = &mut self.tools[pos].1;
                if let Some(id) = tc["id"].as_str().filter(|s| !s.is_empty()) {
                    acc.id = id.into();
                }
                if let Some(n) = tc["function"]["name"].as_str().filter(|s| !s.is_empty()) {
                    acc.name.push_str(n);
                }
                if let Some(a) = tc["function"]["arguments"].as_str() {
                    acc.args.push_str(a);
                    self.output_chars += a.chars().count();
                }
            }
            if let Some(f) = ch["finish_reason"].as_str() {
                self.finish = Some(f.into());
            }
        }
        out
    }

    /// Cierra bloques y el mensaje. Se llama al terminar el stream (haya o no `[DONE]`).
    pub fn finish(&mut self) -> Vec<String> {
        if self.done {
            return vec![];
        }
        self.done = true;
        let mut out = self.start();
        if self.text_open {
            out.push(event("content_block_stop", json!({"type": "content_block_stop", "index": self.index})));
            self.index += 1;
            self.text_open = false;
        }
        for (_, t) in std::mem::take(&mut self.tools) {
            let id = if t.id.is_empty() { new_id("toolu") } else { t.id.clone() };
            let input = tool_input(&t.args);
            out.push(event("content_block_start", json!({"type": "content_block_start", "index": self.index,
                "content_block": {"type": "tool_use", "id": id, "name": t.name, "input": {}}})));
            out.push(event("content_block_delta", json!({"type": "content_block_delta", "index": self.index,
                "delta": {"type": "input_json_delta", "partial_json": input.to_string()}})));
            out.push(event("content_block_stop", json!({"type": "content_block_stop", "index": self.index})));
            self.index += 1;
            self.finish = Some("tool_calls".into());
        }
        let output = self.usage_out.unwrap_or((self.output_chars / 4) as u64);
        out.push(event("message_delta", json!({"type": "message_delta",
            "delta": {"stop_reason": stop_reason(self.finish.as_deref(), false), "stop_sequence": null},
            "usage": {"output_tokens": output}})));
        out.push(event("message_stop", json!({"type": "message_stop"})));
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_a_claude_code_style_request() {
        let req = json!({
            "model": "claude-sonnet-4-5", "max_tokens": 1024, "stream": true,
            "system": [{"type": "text", "text": "Eres Claude Code.", "cache_control": {"type": "ephemeral"}}],
            "messages": [
                {"role": "user", "content": "lista los archivos"},
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "…", "signature": "x"},
                    {"type": "text", "text": "Voy."},
                    {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": "a.rs\nb.rs"}]},
                    {"type": "text", "text": "¿y ahora?"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}}]}],
            "tools": [{"name": "Bash", "description": "corre", "input_schema": {"type": "object"}},
                      {"type": "web_search_20250305", "name": "web_search"}],
            "tool_choice": {"type": "any", "disable_parallel_tool_use": true},
        });
        let o = to_openai(&req).unwrap();
        let m = o["messages"].as_array().unwrap();
        assert_eq!(m[0], json!({"role": "system", "content": "Eres Claude Code."}));
        assert_eq!(m[2]["content"], "Voy.");
        assert_eq!(m[2]["tool_calls"][0]["function"]["arguments"], "{\"command\":\"ls\"}");
        assert_eq!(m[3], json!({"role": "tool", "tool_call_id": "toolu_1", "content": "a.rs\nb.rs"}));
        assert_eq!(m[4]["role"], "user");
        assert_eq!(m[4]["content"][1]["image_url"]["url"], "data:image/png;base64,AAA");
        assert_eq!(o["tools"].as_array().unwrap().len(), 1);
        assert_eq!(o["tool_choice"], "required");
        assert_eq!(o["parallel_tool_calls"], false);
        assert_eq!(o["max_tokens"], 1024);
    }

    #[test]
    fn converts_a_response_with_tool_calls() {
        let resp = json!({"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
            {"id": "call_9", "function": {"name": "Read", "arguments": "{\"path\":\"a.rs\"}"}}]}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 7}});
        let a = from_openai(&resp, "claude-sonnet-4-5");
        assert_eq!(a["stop_reason"], "tool_use");
        assert_eq!(a["content"][0], json!({"type": "tool_use", "id": "call_9", "name": "Read", "input": {"path": "a.rs"}}));
        assert_eq!(a["usage"]["input_tokens"], 50);
    }

    #[test]
    fn decoder_handles_split_lines_and_utf8() {
        let mut d = SseDecoder::default();
        let full = "data: {\"a\":\"ñ\"}\n\ndata: [DONE]\n\n".as_bytes();
        let (x, y) = full.split_at(12); // corta dentro de «ñ»
        let mut got = d.feed(x);
        got.extend(d.feed(y));
        assert_eq!(got, vec!["{\"a\":\"ñ\"}".to_string(), "[DONE]".to_string()]);
    }

    #[test]
    fn stream_text_then_tool() {
        let mut c = StreamConverter::new("claude", 10);
        let mut ev = vec![];
        ev.extend(c.push(r#"{"choices":[{"delta":{"content":"Ho"}}]}"#));
        ev.extend(c.push(r#"{"choices":[{"delta":{"content":"la"}}]}"#));
        ev.extend(c.push(r#"{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"Bash","arguments":"{\"comm"}}]}}]}"#));
        ev.extend(c.push(r#"{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"and\":\"ls\"}"}}]},"finish_reason":"tool_calls"}]}"#));
        ev.extend(c.push("[DONE]"));
        ev.extend(c.finish());
        let names: Vec<&str> = ev.iter().map(|e| e.lines().next().unwrap().trim_start_matches("event: ")).collect();
        assert_eq!(names, ["message_start", "ping", "content_block_start", "content_block_delta", "content_block_delta",
            "content_block_stop", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"]);
        let tool_start: Value = serde_json::from_str(ev[6].lines().nth(1).unwrap().trim_start_matches("data: ")).unwrap();
        assert_eq!(tool_start["index"], 1);
        assert_eq!(tool_start["content_block"]["name"], "Bash");
        let delta: Value = serde_json::from_str(ev[7].lines().nth(1).unwrap().trim_start_matches("data: ")).unwrap();
        assert_eq!(delta["delta"]["partial_json"], "{\"command\":\"ls\"}");
        let md: Value = serde_json::from_str(ev[9].lines().nth(1).unwrap().trim_start_matches("data: ")).unwrap();
        assert_eq!(md["delta"]["stop_reason"], "tool_use");
        assert!(c.finish().is_empty());
    }
}
