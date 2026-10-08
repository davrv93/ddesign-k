//! Salida en terminal: colores (si hay TTY y no hay NO_COLOR) y tablas alineadas.

use std::io::IsTerminal;
use std::sync::OnceLock;

fn color_on() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::io::stdout().is_terminal() && std::env::var_os("NO_COLOR").is_none())
}

fn paint(code: &str, s: &str) -> String {
    if color_on() { format!("\x1b[{code}m{s}\x1b[0m") } else { s.to_string() }
}

pub fn bold(s: &str) -> String { paint("1", s) }
pub fn dim(s: &str) -> String { paint("2", s) }
pub fn green(s: &str) -> String { paint("32", s) }
pub fn yellow(s: &str) -> String { paint("33", s) }
pub fn red(s: &str) -> String { paint("31", s) }
pub fn cyan(s: &str) -> String { paint("36", s) }

/// Ancho visible (sin códigos ANSI).
fn width(s: &str) -> usize {
    let mut w = 0;
    let mut esc = false;
    for c in s.chars() {
        if esc {
            if c.is_ascii_alphabetic() {
                esc = false;
            }
        } else if c == '\x1b' {
            esc = true;
        } else {
            w += 1;
        }
    }
    w
}

pub fn table(headers: &[&str], rows: &[Vec<String>]) {
    let n = headers.len();
    let mut widths: Vec<usize> = headers.iter().map(|h| h.chars().count()).collect();
    for r in rows {
        for (i, c) in r.iter().enumerate().take(n) {
            widths[i] = widths[i].max(width(c));
        }
    }
    let line = |cells: Vec<String>| {
        let mut out = String::new();
        for (i, c) in cells.iter().enumerate() {
            out.push_str(c);
            if i + 1 < cells.len() {
                out.push_str(&" ".repeat(widths[i].saturating_sub(width(c)) + 2));
            }
        }
        println!("{}", out.trim_end());
    };
    line(headers.iter().map(|h| dim(h)).collect());
    for r in rows {
        line(r.clone());
    }
}

pub fn num(v: &serde_json::Value, decimals: usize) -> String {
    match v.as_f64() {
        Some(f) if decimals == 0 => format!("{}", f.round() as i64),
        Some(f) => format!("{f:.decimals$}"),
        None => "—".into(),
    }
}

pub fn ago_or_in(ts: f64) -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
    let d = (ts - now).abs();
    let txt = if d < 60.0 { format!("{d:.0} s") } else if d < 3600.0 { format!("{:.0} min", d / 60.0) } else { format!("{:.1} h", d / 3600.0) };
    if ts >= now { format!("en {txt}") } else { format!("hace {txt}") }
}
