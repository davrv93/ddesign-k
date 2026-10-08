//! Nivel 0: clasificación determinista, sin LLM.
//!
//! Mira la petición entera (no solo el texto): partes de imagen/audio/vídeo/archivo, `tools`,
//! `response_format`, tamaño, bloques de código y palabras clave en español e inglés. Devuelve
//! una decisión con su confianza; si no llega al umbral, decide el juez (nivel 1).

use regex::Regex;
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeSet;
use std::sync::LazyLock;

#[derive(Debug, Default, Clone, Serialize)]
pub struct Features {
    pub has_image: bool,
    pub has_audio: bool,
    pub has_video: bool,
    pub has_pdf: bool,
    pub has_file: bool,
    pub has_tools: bool,
    pub wants_json: bool,
    pub est_tokens: u64,
    #[serde(skip)]
    pub last_user_text: String,
    #[serde(skip)]
    pub all_text: String,
}

impl Features {
    pub fn modality(&self) -> &'static str {
        let mut media = vec![];
        if self.has_image { media.push("image") }
        if self.has_audio { media.push("audio") }
        if self.has_video { media.push("video") }
        if self.has_pdf || self.has_file { media.push("document") }
        match media.len() {
            0 => "text",
            1 => media[0],
            _ => "mixed",
        }
    }

    pub fn requirements(&self) -> BTreeSet<&'static str> {
        let mut r = BTreeSet::from(["text"]);
        for (on, cap) in [(self.has_image, "image"), (self.has_audio, "audio"), (self.has_video, "video"),
                          (self.has_pdf, "pdf"), (self.has_tools, "tools"), (self.wants_json, "json")] {
            if on {
                r.insert(cap);
            }
        }
        r
    }
}

static VIDEO_EXT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\.(mp4|mov|webm|mkv|avi)(\?|$)").unwrap());
static AUDIO_EXT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\.(mp3|wav|ogg|m4a|flac|opus)(\?|$)").unwrap());
static PDF_EXT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\.pdf(\?|$)").unwrap());

fn part_kind(part: &Value) -> Option<&'static str> {
    let t = part.get("type").and_then(Value::as_str).unwrap_or("");
    match t {
        "text" | "input_text" => Some("text"),
        "image_url" | "input_image" | "image" => {
            let url = match part.get("image_url") {
                Some(Value::Object(o)) => o.get("url").and_then(Value::as_str).unwrap_or(""),
                Some(Value::String(s)) => s,
                _ => part.get("url").and_then(Value::as_str).unwrap_or(""),
            };
            if url.starts_with("data:video/") || VIDEO_EXT.is_match(url) {
                Some("video")
            } else if url.starts_with("data:application/pdf") || PDF_EXT.is_match(url) {
                Some("pdf")
            } else {
                Some("image")
            }
        }
        "input_audio" | "audio" => Some("audio"),
        "video_url" | "input_video" | "video" => Some("video"),
        "file" | "input_file" | "document" => {
            let blob: String = part.get("file").unwrap_or(part).to_string().chars().take(400).collect::<String>().to_lowercase();
            if blob.contains("pdf") {
                Some("pdf")
            } else if blob.contains("video/") || VIDEO_EXT.is_match(&blob) {
                Some("video")
            } else if blob.contains("audio/") || AUDIO_EXT.is_match(&blob) {
                Some("audio")
            } else {
                Some("file")
            }
        }
        _ => None,
    }
}

pub fn extract_features(body: &Value) -> Features {
    let mut f = Features::default();
    let mut texts: Vec<String> = vec![];
    let mut media_tokens = 0u64;
    let empty = vec![];
    for msg in body.get("messages").and_then(Value::as_array).unwrap_or(&empty) {
        let role = msg.get("role").and_then(Value::as_str).unwrap_or("");
        let mut msg_text: Vec<String> = vec![];
        match msg.get("content") {
            Some(Value::String(s)) => msg_text.push(s.clone()),
            Some(Value::Array(parts)) => {
                for p in parts {
                    match part_kind(p) {
                        Some("text") => msg_text.push(p.get("text").and_then(Value::as_str).unwrap_or("").to_string()),
                        Some("image") => { f.has_image = true; media_tokens += 1000 }
                        Some("audio") => { f.has_audio = true; media_tokens += 2000 }
                        Some("video") => { f.has_video = true; media_tokens += 8000 }
                        Some("pdf") => { f.has_pdf = true; f.has_file = true; media_tokens += 4000 }
                        Some("file") => { f.has_file = true; media_tokens += 4000 }
                        _ => {}
                    }
                }
            }
            _ => {}
        }
        let text = msg_text.join("\n");
        if role == "user" {
            f.last_user_text = text.clone();
        }
        if role == "tool" || msg.get("tool_calls").is_some_and(|v| !v.is_null()) {
            f.has_tools = true;
        }
        texts.push(text);
    }
    let non_empty = |k: &str| body.get(k).is_some_and(|v| v.as_array().is_some_and(|a| !a.is_empty()));
    f.has_tools |= non_empty("tools") || non_empty("functions");
    f.wants_json = matches!(
        body.pointer("/response_format/type").and_then(Value::as_str),
        Some("json_object") | Some("json_schema")
    );
    f.all_text = texts.join("\n");
    let tools_chars = body.get("tools").map(|t| t.to_string().len()).unwrap_or(0);
    f.est_tokens = ((f.all_text.chars().count() + tools_chars) / 4) as u64 + media_tokens;
    f
}

// ---------------------------------------------------------------------------
// Señales de texto
// ---------------------------------------------------------------------------

fn words(list: &[&str]) -> Regex {
    Regex::new(&format!(r"(?i)\b({})\b", list.join("|"))).unwrap()
}

static CODE_WORDS: LazyLock<Regex> = LazyLock::new(|| words(&[
    r"c[oó]digo", "code", r"funci[oó]n", "function", r"m[eé]todo", "clase", "class", "bug", r"debug\w*",
    r"depura\w*", r"compila\w*", "compile", r"error de (sintaxis|compilación|tipo)", r"stack ?trace",
    "traceback", "exception", r"excepci[oó]n", r"refactor\w*", "endpoint", "api", "sql", "query", "regex",
    "script", r"tests?", "unit test", "pull request", "commit", "git", r"repo(sitorio)?", "python",
    "javascript", "typescript", "golang", "rust", "java", "kotlin", "php", "react", "qwik", "fastapi",
    "django", r"docker(file)?", "kubernetes", "yaml", "json", "npm", "pip", "cargo", r"implementa\w*",
    "implement", r"programa(r)?", "variable", "loop", "bucle", "array", "lint",
]));
static CODE_SYMBOLS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\b(c\+\+|c#)").unwrap());
static CODE_DEEP: LazyLock<Regex> = LazyLock::new(|| words(&[
    r"refactor\w*", "arquitectura", "architecture", r"todo el (proyecto|repo|repositorio|c[oó]digo)",
    r"entire (project|codebase|repo)", r"migra(r|ción)?", "migrate", r"redise[ñn]a(r)?", "codebase",
    "varios archivos", "multiple files", "monorepo", r"por qu[eé] (falla|no funciona|est[aá] fallando)",
    "root cause", r"causa ra[ií]z", "race condition", "memory leak", "deadlock",
]));
static CODE_FAST: LazyLock<Regex> = LazyLock::new(|| words(&[
    r"una l[ií]nea", "one-liner", "one liner", r"r[aá]pido", "quick", r"autocomplet\w*", "snippet",
    "regex", r"renombra(r)?", "rename", "typo",
]));
static REASONING_WORDS: LazyLock<Regex> = LazyLock::new(|| words(&[
    "demuestra", "prove", "prueba que", "calcula", "calculate", "resuelve", "solve", "paso a paso",
    "step by step", "razona", r"reason(ing)?", "analiza", r"analy[sz]e", r"an[aá]lisis", "estrategia",
    "strategy", r"planifica\w*", r"optimiza\w*", r"optimi[sz]e", r"eval[uú]a", "evaluate", r"trade-?offs?",
    "decide", r"decisi[oó]n", "probabilidad", "probability", r"ecuaci[oó]n", "equation", "teorema",
    "theorem", r"l[oó]gica", "logic", "acertijo", "puzzle", r"por qu[eé]", "why", "pros y contras",
    "pros and cons",
]));
static RESEARCH_WORDS: LazyLock<Regex> = LazyLock::new(|| words(&[
    r"investiga\w*", "research", "fuentes", "sources", "estado del arte", "state of the art", "literatura",
    "literature", r"papers?", r"art[ií]culos", r"bibliograf[ií]a", r"compara\w*", "compare",
    r"benchmarks?", "tendencias", "trends", "mercado", "market", "informe", "report",
]));
static GREETING: LazyLock<Regex> = LazyLock::new(|| Regex::new(
    r"(?i)^\s*(hola|holi|buenas|buenos d[ií]as|buenas (tardes|noches)|hey|hi|hello|gracias|thanks|ok|okay|vale|perfecto|genial|adi[oó]s|bye)[\s!.,?¡¿]*$",
).unwrap());
static CODE_LINE: LazyLock<Regex> = LazyLock::new(|| Regex::new(
    r"(?m)^\s*(def |class |import |from \S+ import|function |const |let |var |func |fn |package |public |private |#include|SELECT |INSERT |UPDATE |CREATE |return |if \(|for \(|\}\s*$|\{\s*$)",
).unwrap());
static MATH: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(\d+\s*[\+\-\*/\^=]\s*\d+|∫|∑|√|\bdx\b|lim\s*\()").unwrap());

fn hits(rx: &Regex, text: &str) -> u32 {
    rx.find_iter(text).count() as u32
}

// ---------------------------------------------------------------------------
// Decisión
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct RouteDecision {
    pub agent: String,
    pub modality: String,
    pub reasoning: String, // low | medium | high
    pub tools: bool,
    pub context: String, // small | medium | large
    pub priority: Option<String>,
    pub confidence: f64,
    pub source: String, // rules | judge | profile | hint | explicit
    pub reasons: Vec<String>,
}

impl RouteDecision {
    fn new(agent: &str, f: &Features, ctx: &str, reasoning: &str, confidence: f64, reason: impl Into<String>) -> Self {
        Self {
            agent: agent.into(),
            modality: f.modality().into(),
            reasoning: reasoning.into(),
            tools: f.has_tools,
            context: ctx.into(),
            priority: None,
            confidence,
            source: "rules".into(),
            reasons: vec![reason.into()],
        }
    }

    /// Subtipo de tarea para el aprendizaje: «para ESTE tipo de coding».
    pub fn bucket(&self) -> String {
        format!("{}|{}|tools={}|r={}", self.modality, self.context, self.tools as u8, self.reasoning)
    }
}

pub fn context_bucket(tokens: u64, long_threshold: u64) -> &'static str {
    if tokens >= long_threshold {
        "large"
    } else if tokens >= 4000 {
        "medium"
    } else {
        "small"
    }
}

pub fn classify(f: &Features, long_threshold: u64) -> RouteDecision {
    let text = if f.last_user_text.is_empty() { &f.all_text } else { &f.last_user_text };
    let ctx = context_bucket(f.est_tokens, long_threshold);

    let fences = f.all_text.matches("```").count() as u32;
    let code = hits(&CODE_WORDS, text) + hits(&CODE_SYMBOLS, text) + 3 * fences / 2
        + hits(&CODE_LINE, &f.all_text).min(6);
    let deep = hits(&CODE_DEEP, text);
    let fast = hits(&CODE_FAST, text);
    let reasoning = hits(&REASONING_WORDS, text) + 2 * hits(&MATH, text);
    let research = hits(&RESEARCH_WORDS, text);

    // 1. El medio no admite discusión.
    if f.has_video || f.has_audio {
        return RouteDecision::new("multimodal", f, ctx, "medium", 0.95, "audio/vídeo en la petición");
    }
    if f.has_pdf || f.has_file {
        return RouteDecision::new("document", f, ctx, "medium", 0.9, "archivo/PDF adjunto");
    }
    if f.has_image {
        if code >= 3 {
            return RouteDecision::new("coding", f, ctx, "medium", 0.8, "imagen + señales de código");
        }
        return RouteDecision::new("vision", f, ctx, "low", 0.9, "imagen adjunta");
    }

    // 2. Texto.
    if GREETING.is_match(text) && !f.has_tools {
        return RouteDecision::new("general", f, ctx, "low", 0.97, "saludo/cortesía");
    }
    let mut scores = [("coding", code), ("reasoning", reasoning), ("research", research)];
    scores.sort_by_key(|s| std::cmp::Reverse(s.1));
    let (best, top) = scores[0];
    let second = scores[1].1;

    if top == 0 {
        let short = text.chars().count() < 280;
        return RouteDecision::new("general", f, ctx, "low", if short { 0.85 } else { 0.55 },
            if short { "sin señales; mensaje corto" } else { "sin señales; mensaje largo" });
    }

    let margin = (top - second) as f64 / top.max(1) as f64;
    let confidence = (0.5 + 0.12 * top as f64).min(0.95) * (0.6 + 0.4 * margin);
    let why = format!("señales código={code} razonamiento={reasoning} investigación={research}");

    match best {
        "coding" => {
            let big = deep > 0 || ctx == "large";
            let level = if big { "high" } else if reasoning > 0 { "medium" } else { "low" };
            let (agent, extra) = if big {
                ("coding-deep", Some("refactor/arquitectura/contexto grande"))
            } else if fast > 0 && text.chars().count() < 400 && ctx == "small" {
                ("coding-fast", Some("tarea de código corta"))
            } else {
                ("coding", None)
            };
            let mut d = RouteDecision::new(agent, f, ctx, level, confidence, why);
            d.reasons.extend(extra.map(String::from));
            d
        }
        "research" => RouteDecision::new("research", f, ctx, "medium", confidence, why),
        _ => RouteDecision::new("reasoning", f, ctx, if reasoning >= 3 { "high" } else { "medium" }, confidence, why),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn decide(body: Value) -> RouteDecision {
        classify(&extract_features(&body), 32000)
    }

    fn user(text: &str) -> Value {
        json!({"messages": [{"role": "user", "content": text}]})
    }

    #[test]
    fn image_goes_to_vision() {
        let d = decide(json!({"messages": [{"role": "user", "content": [
            {"type": "text", "text": "¿qué ves?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]}));
        assert_eq!(d.agent, "vision");
        assert_eq!(d.modality, "image");
    }

    #[test]
    fn audio_and_pdf() {
        let d = decide(json!({"messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": "...", "format": "wav"}}]}]}));
        assert_eq!(d.agent, "multimodal");
        let d = decide(json!({"messages": [{"role": "user", "content": [
            {"type": "file", "file": {"filename": "a.pdf", "file_data": "data:application/pdf;base64,AA"}}]}]}));
        assert_eq!(d.agent, "document");
    }

    #[test]
    fn code_with_fence_is_coding() {
        let d = decide(user("Revisa esta función de Python, da un error:\n```python\ndef f(x):\n    return x/0\n```"));
        assert!(d.agent.starts_with("coding"), "{:?}", d);
        assert!(d.confidence >= 0.75, "{:?}", d);
    }

    #[test]
    fn refactor_project_is_deep() {
        let d = decide(user("Revisa todo el proyecto y dime por qué la autenticación está fallando, luego refactoriza el código"));
        assert_eq!(d.agent, "coding-deep");
        assert_eq!(d.reasoning, "high");
    }

    #[test]
    fn quick_regex_is_fast() {
        assert_eq!(decide(user("dame un regex rápido para validar un email en javascript")).agent, "coding-fast");
    }

    #[test]
    fn math_is_reasoning() {
        let d = decide(user("Resuelve paso a paso: si 3x + 5 = 20, calcula x y demuestra que es única"));
        assert_eq!(d.agent, "reasoning");
    }

    #[test]
    fn research_words() {
        assert_eq!(decide(user("Investiga el estado del arte y compara las fuentes sobre baterías")).agent, "research");
    }

    #[test]
    fn greeting_is_confident_general() {
        let d = decide(user("hola!"));
        assert_eq!(d.agent, "general");
        assert!(d.confidence > 0.9);
    }

    #[test]
    fn long_unclear_text_has_low_confidence() {
        let d = decide(user(&"Necesito que me ayudes con una cosa que me tiene preocupado desde hace días. ".repeat(6)));
        assert!(d.confidence < 0.75, "{:?}", d);
    }

    #[test]
    fn tools_and_json_are_requirements() {
        let f = extract_features(&json!({
            "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {"name": "f"}}],
            "response_format": {"type": "json_object"}}));
        let r = f.requirements();
        assert!(r.contains("tools") && r.contains("json"));
    }
}
