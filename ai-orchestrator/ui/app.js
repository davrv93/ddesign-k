// UI de gestión de ai-orchestrator. Sin dependencias: habla con /admin/api/*.
"use strict";

const CAPS = ["text", "code", "reasoning", "tools", "json", "image", "audio", "video", "pdf"];
const KINDS = ["rate_limit", "quota", "timeout", "server_error", "unavailable", "auth", "not_found",
  "context", "unsupported", "bad_request", "connection"];
const KIND_HELP = {
  rate_limit: "429", quota: "402 / cuota agotada", timeout: "408 / 504 / timeout", server_error: "5xx",
  unavailable: "modelo caído / sin endpoints", auth: "401 / 403", not_found: "404 modelo inexistente",
  context: "contexto excedido", unsupported: "no admite imagen, tools…", bad_request: "400 petición mal formada",
  connection: "sin conexión",
};
const PRESETS = {
  "OpenRouter": { base_url: "https://openrouter.ai/api/v1", api_key_env: "OPENROUTER_API_KEY",
    quota: { balance: "openrouter", requests_per_day: 50, requests_per_minute: 20 } },
  "OpenCode Zen": { base_url: "https://opencode.ai/zen/v1", api_key_env: "OPENCODE_API_KEY", quota: { balance: "none" } },
  "Gemini (AI Studio)": { base_url: "https://generativelanguage.googleapis.com/v1beta/openai", api_key_env: "GEMINI_API_KEY",
    quota: { balance: "none", reset_utc_offset_hours: -8 } },
  "DeepSeek": { base_url: "https://api.deepseek.com/v1", api_key_env: "DEEPSEEK_API_KEY",
    quota: { balance: "deepseek", balance_url: "https://api.deepseek.com/user/balance" } },
  "Groq": { base_url: "https://api.groq.com/openai/v1", api_key_env: "GROQ_API_KEY", quota: { balance: "none" } },
  "OpenAI": { base_url: "https://api.openai.com/v1", api_key_env: "OPENAI_API_KEY", quota: { balance: "none" } },
  "Ollama (local)": { base_url: "http://host.containers.internal:11434/v1", quota: { balance: "none" } },
  "Otro (formato OpenAI)": { base_url: "https://", quota: { balance: "none" } },
};

const state = { view: "panel", cfg: null, sel: {}, timer: null, providerModels: {} };

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* sin almacenamiento */ } },
};

function fmtTime(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString([], { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
}
function fmtIn(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Math.round(ts - Date.now() / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}
const fmtNum = (n, d = 0) => (n == null ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d }));

function toast(msg, kind = "ok") {
  const d = document.createElement("div");
  d.className = `notice ${kind}`;
  d.textContent = msg;
  $("#toast").appendChild(d);
  setTimeout(() => d.remove(), kind === "bad" ? 8000 : 3500);
}

async function api(method, path, body, raw = false) {
  const headers = { Authorization: `Bearer ${store.get("aio_token") || ""}` };
  let payload;
  if (body !== undefined) {
    if (typeof body === "string") { headers["Content-Type"] = "text/plain"; payload = body; }
    else { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  }
  const r = await fetch(path, { method, headers, body: payload });
  if (r.status === 401) { login(); throw new Error("token inválido"); }
  const text = await r.text();
  if (raw) { if (!r.ok) throw new Error(text); return text; }
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!r.ok) throw new Error(data?.error?.message || text || `HTTP ${r.status}`);
  return data;
}

async function loadConfig() {
  state.cfg = await api("GET", "/admin/api/config");
  return state.cfg;
}

async function save(method, path, body, okMsg) {
  try {
    const r = await api(method, path, body);
    if (r.config) state.cfg = r.config;
    toast(okMsg || "Guardado");
    return true;
  } catch (e) {
    toast(e.message, "bad");
    return false;
  }
}

function bar(used, limit) {
  if (!limit) return "";
  const pct = Math.min(100, (used / limit) * 100);
  const cls = pct >= 90 ? "bad" : pct >= 70 ? "warn" : "";
  return `<div class="bar ${cls}"><span style="width:${(100 - pct).toFixed(1)}%"></span></div>`;
}

function remainingBar(remaining, limit) {
  if (remaining == null || !limit) return "";
  return bar(limit - remaining, limit);
}

// ---------------------------------------------------------------------------
// Login y navegación
// ---------------------------------------------------------------------------

function login() {
  if ($(".modal-bg")) return;
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  bg.innerHTML = `<form class="modal">
    <h3>Token de administración</h3>
    <p class="muted small">Es <span class="mono">ADMIN_TOKEN</span>; si no lo definiste, está en
      <span class="mono">data/admin_token</span> (y en el log del primer arranque).</p>
    <div class="field"><input id="tok" type="password" autocomplete="current-password" required></div>
    <button class="primary" type="submit">Entrar</button></form>`;
  document.body.appendChild(bg);
  $("#tok").focus();
  $("form", bg).onsubmit = (e) => {
    e.preventDefault();
    store.set("aio_token", $("#tok").value.trim());
    bg.remove();
    show(state.view);
  };
}

function show(view) {
  state.view = view;
  store.set("aio_view", view);
  clearInterval(state.timer);
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  const fn = VIEWS[view] || VIEWS.panel;
  fn().catch((e) => { if (e.message !== "token inválido") $("#main").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; });
}

// ---------------------------------------------------------------------------
// Panel: cuotas y estado
// ---------------------------------------------------------------------------

function quotaCard(name, q) {
  const b = q.balance;
  let balance = "";
  if (q.balance_kind && q.balance_kind !== "none") {
    if (!b) balance = `<div class="muted small">Saldo: aún sin consultar</div>`;
    else if (b.error) balance = `<div class="small"><span class="pill bad">saldo</span> ${esc(b.error)}</div>`;
    else balance = `<div class="quota-line">
        <div class="metric"><span>Saldo ${esc(b.unit)}${b.is_free_tier ? " · capa gratis" : ""}</span>
          <b>${fmtNum(b.remaining, 2)}${b.limit != null ? ` / ${fmtNum(b.limit, 2)}` : ""}</b></div>
        ${remainingBar(b.remaining, b.limit)}
        <div class="muted small">consultado ${fmtTime(b.at)}${b.usage != null ? ` · gastado ${fmtNum(b.usage, 2)}` : ""}</div>
      </div>`;
  }
  const line = (label, used, limit, remaining) => `<div class="quota-line">
      <div class="metric"><span>${label}</span><b>${limit ? `${fmtNum(remaining)} de ${fmtNum(limit)} restantes` : `${fmtNum(used)} usadas · sin límite`}</b></div>
      ${bar(used, limit)}</div>`;
  const deps = q.deployments.map((d) => {
    const h = d.headers;
    const hdr = h && h.remaining_requests != null
      ? `${fmtNum(h.remaining_requests)}${h.limit_requests ? `/${fmtNum(h.limit_requests)}` : ""} req${h.reset_requests_at ? ` · reinicia en ${fmtIn(h.reset_requests_at)}` : ""}`
      : h && h.remaining_tokens != null ? `${fmtNum(h.remaining_tokens)} tok` : "—";
    const local = d.requests_per_day ? `${fmtNum(d.remaining_today)}/${fmtNum(d.requests_per_day)}` : fmtNum(d.requests_today);
    return `<tr><td><span class="mono">${esc(d.group)}</span><div class="muted small mono">${esc(d.model)}</div></td>
      <td>${local}</td><td class="small">${hdr}</td></tr>`;
  }).join("");
  const status = !q.enabled ? `<span class="pill">desactivado</span>` : !q.has_key ? `<span class="pill bad">sin clave</span>` : `<span class="pill ok">activo</span>`;
  return `<div class="card">
    <div class="row"><h3>${esc(name)}</h3>${status}<span class="spacer"></span>
      ${q.balance_kind && q.balance_kind !== "none" ? `<button class="link" data-balance="${esc(name)}">Actualizar saldo</button>` : ""}</div>
    ${balance}
    ${line("Peticiones hoy", q.requests_today, q.requests_per_day, q.remaining_today)}
    ${q.tokens_per_day ? line("Tokens hoy", q.tokens_today, q.tokens_per_day, q.remaining_tokens_today) : `<div class="muted small">Tokens hoy: ${fmtNum(q.tokens_today)}</div>`}
    <div class="muted small">Último minuto: ${fmtNum(q.requests_last_minute)}${q.requests_per_minute ? ` / ${q.requests_per_minute}` : ""} · el día reinicia en ${fmtIn(q.resets_at)}</div>
    ${deps ? `<div class="table-wrap"><table><thead><tr><th>Modelo</th><th>Hoy (local)</th><th>Proveedor dice</th></tr></thead><tbody>${deps}</tbody></table></div>` : ""}
  </div>`;
}

function circuitPill(m) {
  if (m.cooldown) return `<span class="pill warn">cooldown ${fmtIn(m.cooldown.until)} · ${esc(m.cooldown.reason)}</span>`;
  if (m.circuit === "open") return `<span class="pill bad">circuito abierto ${fmtIn(m.open_until)}</span>`;
  if (m.circuit === "half_open") return `<span class="pill warn">semiabierto</span>`;
  const usable = m.deployments.some((d) => d.usable);
  return usable ? `<span class="pill ok">disponible</span>` : `<span class="pill bad">sin deployment usable</span>`;
}

async function viewPanel() {
  const main = $("#main");
  const render = async () => {
    const [quotas, status] = await Promise.all([api("GET", "/admin/api/quotas"), api("GET", "/admin/api/status")]);
    const byModel = {};
    for (const s of status.stats) {
      const m = (byModel[s.model] ||= { calls: 0, ok: 0, lat: [], q: [] });
      m.calls += s.calls; m.ok += s.success_rate * s.calls;
      if (s.latency != null) m.lat.push(s.latency);
      if (s.quality != null) m.q.push(s.quality);
    }
    const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
    const rows = Object.entries(status.models).map(([name, m]) => {
      const st = byModel[name];
      const reasons = m.deployments.filter((d) => !d.usable).map((d) => `${d.provider}: ${d.reason}`).join("; ");
      return `<tr><td class="mono">${esc(name)}</td><td>${circuitPill(m)}${reasons ? `<div class="muted small">${esc(reasons)}</div>` : ""}</td>
        <td>${st ? fmtNum(st.calls) : "0"}</td><td>${st ? `${fmtNum((st.ok / st.calls) * 100)} %` : "—"}</td>
        <td>${st && avg(st.lat) != null ? `${fmtNum(avg(st.lat), 1)} s` : "—"}</td>
        <td>${st && avg(st.q) != null ? fmtNum(avg(st.q), 2) : "—"}</td>
        <td><button class="link" data-reset="${esc(name)}">Reiniciar</button></td></tr>`;
    }).join("");
    main.innerHTML = `
      <div class="row"><h2>Cuotas</h2><span class="spacer"></span><span class="muted small">se actualiza cada 10 s</span></div>
      <div class="grid">${Object.entries(quotas).map(([n, q]) => quotaCard(n, q)).join("")}</div>
      <h2 style="margin-top:20px">Modelos</h2>
      <div class="card table-wrap"><table>
        <thead><tr><th>Modelo</th><th>Estado</th><th>Llamadas</th><th>Éxito</th><th>Latencia</th><th>Calidad</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>
        <p class="muted small">Estadísticas de los últimos ${state.cfg?.learning?.window_days ?? 14} días. «Reiniciar» cierra el circuito y quita los cooldowns.</p>
      </div>`;
    $$("[data-balance]").forEach((b) => b.onclick = async () => {
      b.disabled = true;
      try { await api("POST", `/admin/api/providers/${encodeURIComponent(b.dataset.balance)}/balance`); render(); }
      catch (e) { toast(e.message, "bad"); b.disabled = false; }
    });
    $$("[data-reset]").forEach((b) => b.onclick = async () => {
      await save("POST", `/admin/api/models/${encodeURIComponent(b.dataset.reset)}/reset`, undefined, "Modelo reiniciado");
      render();
    });
  };
  if (!state.cfg) await loadConfig();
  await render();
  state.timer = setInterval(() => render().catch(() => {}), 10000);
}

// ---------------------------------------------------------------------------
// Lista + formulario (proveedores, modelos, agentes)
// ---------------------------------------------------------------------------

function listView({ title, kind, items, summary, form, blank, help }) {
  const names = Object.keys(items);
  const sel = state.sel[kind] ?? names[0] ?? null;
  const main = $("#main");
  main.innerHTML = `<div class="row"><h2>${title}</h2><span class="spacer"></span><button id="add" class="primary">Añadir</button></div>
    ${help ? `<p class="muted">${help}</p>` : ""}
    <div class="split"><div id="list"></div><div id="form"></div></div>`;
  $("#list").innerHTML = names.map((n) => `<div class="list-item ${n === sel ? "selected" : ""}" data-name="${esc(n)}">
      <div style="min-width:0"><div class="name mono">${esc(n)}</div><div class="muted small">${summary(items[n])}</div></div></div>`).join("")
    || `<p class="muted">Nada todavía.</p>`;
  $$("#list .list-item").forEach((el) => el.onclick = () => { state.sel[kind] = el.dataset.name; show(state.view); });
  $("#add").onclick = () => { state.sel[kind] = null; form(null, blank()); };
  if (sel && items[sel]) form(sel, clone(items[sel]));
  else if (!names.length) form(null, blank());
}

function nameField(name) {
  return `<div class="field"><label>Nombre</label><input id="f-name" value="${esc(name || "")}" ${name ? "readonly" : ""}
    pattern="[A-Za-z0-9._\\-]+" placeholder="letras, números, - _ ." required></div>`;
}

function formButtons(isNew) {
  return `<div class="row"><button class="primary" type="submit">${isNew ? "Crear" : "Guardar"}</button>
    ${isNew ? "" : `<button type="button" class="danger" id="del">Eliminar</button>`}</div>`;
}

const num = (id) => { const v = $(id).value.trim(); return v === "" ? 0 : Number(v); };
const optNum = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
const optStr = (id) => { const v = $(id).value.trim(); return v === "" ? null : v; };

function parseJSON(id, fallback) {
  const v = $(id).value.trim();
  if (!v) return fallback;
  try { return JSON.parse(v); } catch { throw new Error(`${id.replace("#f-", "")}: JSON inválido`); }
}

// -- proveedores ---------------------------------------------------------------

async function viewProviders() {
  const cfg = await loadConfig();
  listView({
    title: "Proveedores", kind: "providers", items: cfg.providers,
    help: "Cualquier endpoint con formato OpenAI (<span class='mono'>/chat/completions</span> y <span class='mono'>/models</span>). La clave puede ir aquí (se guarda en data/config.yaml) o en una variable de entorno.",
    summary: (p) => `${esc(p.base_url)}${p.enabled === false ? " · desactivado" : ""}`,
    blank: () => ({ base_url: "https://", quota: { balance: "none" }, enabled: true }),
    form: providerForm,
  });
}

function providerForm(name, p) {
  const q = p.quota || {};
  const headers = Object.entries(p.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
  $("#form").innerHTML = `<form class="card" id="pf">
    ${name ? "" : `<div class="field"><label>Plantilla</label><select id="f-preset"><option value="">—</option>
      ${Object.keys(PRESETS).map((k) => `<option>${esc(k)}</option>`).join("")}</select></div>`}
    ${nameField(name)}
    <div class="field"><label>Base URL (sin /chat/completions)</label><input id="f-base" value="${esc(p.base_url)}" required></div>
    <div class="fields">
      <div class="field"><label>Clave API</label><input id="f-key" type="password" autocomplete="off" value="${esc(p.api_key || "")}" placeholder="vacío = usar la variable de entorno"></div>
      <div class="field"><label>Variable de entorno de la clave</label><input id="f-keyenv" value="${esc(p.api_key_env || "")}" placeholder="OPENROUTER_API_KEY"></div>
    </div>
    <div class="checks field"><label><input type="checkbox" id="f-enabled" ${p.enabled !== false ? "checked" : ""}> Activo</label></div>
    <div class="field"><label>Cabeceras extra (una por línea, «Nombre: valor»)</label><textarea id="f-headers" style="min-height:60px">${esc(headers)}</textarea></div>
    <h3>Cuota</h3>
    <div class="fields">
      <div class="field"><label>Saldo</label><select id="f-balance">
        ${["none", "openrouter", "deepseek", "json"].map((k) => `<option ${q.balance === k ? "selected" : ""}>${k}</option>`).join("")}</select></div>
      <div class="field"><label>URL de saldo</label><input id="f-burl" value="${esc(q.balance_url || "")}" placeholder="openrouter: {base}/key"></div>
      <div class="field"><label>Puntero JSON al restante</label><input id="f-rptr" value="${esc(q.remaining_pointer || "")}" placeholder="/data/remaining"></div>
      <div class="field"><label>Puntero JSON al límite</label><input id="f-lptr" value="${esc(q.limit_pointer || "")}" placeholder="/data/limit"></div>
      <div class="field"><label>Peticiones por día (0 = sin límite)</label><input id="f-rpd" type="number" min="0" value="${q.requests_per_day || 0}"></div>
      <div class="field"><label>Peticiones por minuto</label><input id="f-rpm" type="number" min="0" value="${q.requests_per_minute || 0}"></div>
      <div class="field"><label>Tokens por día</label><input id="f-tpd" type="number" min="0" value="${q.tokens_per_day || 0}"></div>
      <div class="field"><label>Reinicio diario (desfase UTC, h)</label><input id="f-off" type="number" min="-12" max="14" value="${q.reset_utc_offset_hours || 0}"></div>
    </div>
    ${formButtons(!name)}
    ${name ? `<hr style="border:0;border-top:1px solid var(--border);margin:16px 0">
      <div class="row"><button type="button" id="test">Probar y ver modelos</button><span id="test-out" class="muted small"></span></div>
      <div id="models-out"></div>` : ""}
  </form>`;
  const preset = $("#f-preset");
  if (preset) preset.onchange = () => {
    const pr = PRESETS[preset.value];
    if (!pr) return;
    $("#f-base").value = pr.base_url;
    $("#f-keyenv").value = pr.api_key_env || "";
    $("#f-balance").value = pr.quota.balance;
    $("#f-burl").value = pr.quota.balance_url || "";
    $("#f-rpd").value = pr.quota.requests_per_day || 0;
    $("#f-rpm").value = pr.quota.requests_per_minute || 0;
    $("#f-off").value = pr.quota.reset_utc_offset_hours || 0;
    if (!$("#f-name").value) $("#f-name").value = preset.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  };
  $("#pf").onsubmit = async (e) => {
    e.preventDefault();
    const n = $("#f-name").value.trim();
    const hdrs = {};
    for (const line of $("#f-headers").value.split("\n")) {
      const i = line.indexOf(":");
      if (i > 0) hdrs[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
    const body = {
      base_url: $("#f-base").value.trim(), api_key: optStr("#f-key"), api_key_env: optStr("#f-keyenv"),
      headers: hdrs, enabled: $("#f-enabled").checked,
      quota: { balance: $("#f-balance").value, balance_url: optStr("#f-burl"), remaining_pointer: optStr("#f-rptr"),
        limit_pointer: optStr("#f-lptr"), requests_per_day: num("#f-rpd"), requests_per_minute: num("#f-rpm"),
        tokens_per_day: num("#f-tpd"), reset_utc_offset_hours: num("#f-off") },
    };
    if (await save("PUT", `/admin/api/providers/${encodeURIComponent(n)}`, body)) { state.sel.providers = n; show("providers"); }
  };
  const del = $("#del");
  if (del) del.onclick = async () => {
    if (!confirm(`¿Eliminar el proveedor ${name}?`)) return;
    if (await save("DELETE", `/admin/api/providers/${encodeURIComponent(name)}`, undefined, "Eliminado")) { state.sel.providers = null; show("providers"); }
  };
  const test = $("#test");
  if (test) test.onclick = async () => {
    test.disabled = true;
    $("#test-out").textContent = "consultando…";
    try {
      const r = await api("POST", `/admin/api/providers/${encodeURIComponent(name)}/models`);
      state.providerModels[name] = r.models.map((m) => m.id);
      $("#test-out").innerHTML = r.ok ? `<span class="pill ok">OK</span> ${r.models.length} modelos · ${fmtNum(r.latency, 2)} s`
        : `<span class="pill bad">${esc(r.status ?? "sin conexión")}</span> ${esc(r.error || "")}`;
      const conf = r.configured.map((c) => `<tr><td class="mono">${esc(c.group)}</td><td class="mono">${esc(c.model)}</td>
        <td>${c.exists === true ? `<span class="pill ok">existe</span>` : c.exists === false ? `<span class="pill bad">no está en /models</span>` : "?"}</td></tr>`).join("");
      $("#models-out").innerHTML = `
        ${conf ? `<h3 style="margin-top:12px">Deployments configurados</h3><div class="table-wrap"><table><tbody>${conf}</tbody></table></div>` : ""}
        <h3 style="margin-top:12px">Modelos del proveedor</h3>
        <input id="mfilter" placeholder="filtrar… (p. ej. :free, nemotron)">
        <div class="table-wrap" style="max-height:340px;overflow:auto"><table id="mtable"><tbody>${r.models.map((m) =>
          `<tr><td class="mono">${esc(m.id)}</td><td>${m.free ? `<span class="pill ok">gratis</span>` : ""}</td>
           <td class="small muted">${m.context ? `${fmtNum(m.context)} ctx` : ""} ${esc((m.input_modalities || []).join(", "))}</td></tr>`).join("")}</tbody></table></div>`;
      $("#mfilter").oninput = (ev) => {
        const f = ev.target.value.toLowerCase();
        $$("#mtable tr").forEach((tr) => tr.style.display = tr.textContent.toLowerCase().includes(f) ? "" : "none");
      };
    } catch (e) { $("#test-out").textContent = e.message; }
    test.disabled = false;
  };
}

// -- modelos -----------------------------------------------------------------------

async function viewModels() {
  const cfg = await loadConfig();
  listView({
    title: "Modelos", kind: "models", items: cfg.models,
    help: "Un modelo es un grupo con uno o varios deployments (el mismo modelo en distintos proveedores), en orden de preferencia.",
    summary: (m) => `${m.deployments.map((d) => esc(d.provider)).join(" → ")} · ${[...m.capabilities].join(", ")}`,
    blank: () => ({ capabilities: ["text"], context: 32768, cost: 0, deployments: [{ provider: Object.keys(cfg.providers)[0] || "", model: "" }] }),
    form: modelForm,
  });
}

function depRow(d, providers) {
  const list = state.providerModels[d.provider] || [];
  return `<div class="dep">
    <select class="d-prov">${providers.map((p) => `<option ${p === d.provider ? "selected" : ""}>${esc(p)}</option>`).join("")}</select>
    <input class="d-model mono" value="${esc(d.model)}" placeholder="id del modelo en el proveedor" list="dl-${esc(d.provider)}" required>
    <input class="d-rpd" type="number" min="0" value="${d.requests_per_day || 0}" title="peticiones/día (0 = sin límite)">
    <input class="d-rpm" type="number" min="0" value="${d.requests_per_minute || 0}" title="peticiones/min (0 = sin límite)">
    <div class="row" style="flex-wrap:nowrap"><button type="button" class="link d-up">↑</button><button type="button" class="link d-down">↓</button>
      <button type="button" class="link danger d-rm">✕</button></div>
    <datalist id="dl-${esc(d.provider)}">${list.map((m) => `<option value="${esc(m)}">`).join("")}</datalist></div>`;
}

function modelForm(name, m) {
  const providers = Object.keys(state.cfg.providers);
  $("#form").innerHTML = `<form class="card" id="mf">
    ${nameField(name)}
    <div class="field"><label>Capacidades</label><div class="checks">${CAPS.map((c) =>
      `<label><input type="checkbox" value="${c}" class="f-cap" ${m.capabilities.includes(c) ? "checked" : ""}> ${c}</label>`).join("")}</div></div>
    <div class="fields">
      <div class="field"><label>Contexto (tokens)</label><input id="f-ctx" type="number" min="1" value="${m.context}"></div>
      <div class="field"><label>Costo</label><select id="f-cost">${["0 · gratis", "1 · barato", "2 · normal", "3 · caro"].map((l, i) =>
        `<option value="${i}" ${m.cost === i ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    </div>
    <label>Deployments (proveedor · modelo · req/día · req/min)</label>
    <div class="deps" id="deps">${m.deployments.map((d) => depRow(d, providers)).join("")}</div>
    <div class="field"><button type="button" id="add-dep">+ deployment</button>
      <span class="muted small">Sugerencias: «Proveedores → Probar y ver modelos» carga la lista del proveedor.</span></div>
    ${formButtons(!name)}</form>`;
  const readDeps = () => $$("#deps .dep").map((el) => ({
    provider: $(".d-prov", el).value, model: $(".d-model", el).value.trim(),
    requests_per_day: Number($(".d-rpd", el).value || 0), requests_per_minute: Number($(".d-rpm", el).value || 0),
  }));
  const redraw = (deps) => { $("#deps").innerHTML = deps.map((d) => depRow(d, providers)).join(""); wire(); };
  const wire = () => {
    $$("#deps .dep").forEach((el, i) => {
      $(".d-rm", el).onclick = () => { const d = readDeps(); d.splice(i, 1); redraw(d); };
      $(".d-up", el).onclick = () => { const d = readDeps(); if (i > 0) [d[i - 1], d[i]] = [d[i], d[i - 1]]; redraw(d); };
      $(".d-down", el).onclick = () => { const d = readDeps(); if (i < d.length - 1) [d[i + 1], d[i]] = [d[i], d[i + 1]]; redraw(d); };
      $(".d-prov", el).onchange = () => redraw(readDeps());
    });
  };
  wire();
  $("#add-dep").onclick = () => redraw([...readDeps(), { provider: providers[0] || "", model: "" }]);
  $("#mf").onsubmit = async (e) => {
    e.preventDefault();
    const n = $("#f-name").value.trim();
    const body = { capabilities: $$(".f-cap").filter((c) => c.checked).map((c) => c.value), context: num("#f-ctx"),
      cost: Number($("#f-cost").value), deployments: readDeps() };
    if (await save("PUT", `/admin/api/models/${encodeURIComponent(n)}`, body)) { state.sel.models = n; show("models"); }
  };
  const del = $("#del");
  if (del) del.onclick = async () => {
    if (!confirm(`¿Eliminar el modelo ${name}?`)) return;
    if (await save("DELETE", `/admin/api/models/${encodeURIComponent(name)}`, undefined, "Eliminado")) { state.sel.models = null; show("models"); }
  };
}

// -- agentes ---------------------------------------------------------------------------

async function viewAgents() {
  const cfg = await loadConfig();
  listView({
    title: "Agentes", kind: "agents", items: cfg.agents,
    help: "Cada agente es un <span class='mono'>model</span> que tu aplicación puede pedir (<span class='mono'>model=\"coding-deep\"</span>); con <span class='mono'>model=\"auto\"</span> el router elige. La cadena es el orden por defecto: la telemetría la reordena.",
    summary: (a) => `${esc(a.priority || "quality")} · ${a.chain.map(esc).join(" → ")}`,
    blank: () => ({ description: "", chain: [Object.keys(cfg.models)[0]].filter(Boolean), priority: "quality", params: {} }),
    form: agentForm,
  });
}

function agentForm(name, a) {
  const models = Object.keys(state.cfg.models);
  let chain = [...a.chain];
  $("#form").innerHTML = `<form class="card" id="af">
    ${nameField(name)}
    <div class="field"><label>Descripción (la lee el juez para decidir)</label><input id="f-desc" value="${esc(a.description || "")}"></div>
    <div class="fields">
      <div class="field"><label>Prioridad</label><select id="f-prio">${["quality", "speed", "cost"].map((p) =>
        `<option ${a.priority === p ? "selected" : ""}>${p}</option>`).join("")}</select></div>
      <div class="field"><label>Timeout por intento (s)</label><input id="f-timeout" type="number" min="1" value="${a.timeout ?? ""}" placeholder="por defecto"></div>
    </div>
    <label>Cadena de modelos</label><ul class="chain" id="chain"></ul>
    <div class="row field"><select id="f-addm" style="width:auto">${models.map((m) => `<option>${esc(m)}</option>`).join("")}</select>
      <button type="button" id="addm">+ modelo</button></div>
    <div class="field"><label>Parámetros por defecto (JSON; no pisan los de la petición)</label>
      <textarea id="f-params" style="min-height:60px">${esc(Object.keys(a.params || {}).length ? JSON.stringify(a.params, null, 2) : "")}</textarea></div>
    <div class="field"><label>System prompt (opcional; se antepone si la petición no trae uno)</label>
      <textarea id="f-sys" style="min-height:60px;font-family:inherit">${esc(a.system_prompt || "")}</textarea></div>
    ${formButtons(!name)}</form>`;
  const draw = () => {
    $("#chain").innerHTML = chain.map((m, i) => `<li><b>${i + 1}</b><span>${esc(m)}</span>
      <button type="button" class="link" data-up="${i}">↑</button><button type="button" class="link" data-down="${i}">↓</button>
      <button type="button" class="link danger" data-rm="${i}">✕</button></li>`).join("");
    $$("[data-up]").forEach((b) => b.onclick = () => { const i = +b.dataset.up; if (i > 0) [chain[i - 1], chain[i]] = [chain[i], chain[i - 1]]; draw(); });
    $$("[data-down]").forEach((b) => b.onclick = () => { const i = +b.dataset.down; if (i < chain.length - 1) [chain[i + 1], chain[i]] = [chain[i], chain[i + 1]]; draw(); });
    $$("[data-rm]").forEach((b) => b.onclick = () => { chain.splice(+b.dataset.rm, 1); draw(); });
  };
  draw();
  $("#addm").onclick = () => { const m = $("#f-addm").value; if (m && !chain.includes(m)) chain.push(m); draw(); };
  $("#af").onsubmit = async (e) => {
    e.preventDefault();
    const n = $("#f-name").value.trim();
    let params;
    try { params = parseJSON("#f-params", {}); } catch (err) { toast(err.message, "bad"); return; }
    const body = { description: $("#f-desc").value.trim(), chain, priority: $("#f-prio").value,
      timeout: optNum("#f-timeout"), params, system_prompt: optStr("#f-sys") };
    if (await save("PUT", `/admin/api/agents/${encodeURIComponent(n)}`, body)) { state.sel.agents = n; show("agents"); }
  };
  const del = $("#del");
  if (del) del.onclick = async () => {
    if (!confirm(`¿Eliminar el agente ${name}?`)) return;
    if (await save("DELETE", `/admin/api/agents/${encodeURIComponent(name)}`, undefined, "Eliminado")) { state.sel.agents = null; show("agents"); }
  };
}

// ---------------------------------------------------------------------------
// Router, fiabilidad y aprendizaje
// ---------------------------------------------------------------------------

async function viewRouting() {
  const cfg = await loadConfig();
  const r = cfg.router, rel = cfg.reliability, l = cfg.learning;
  const models = Object.keys(cfg.models);
  const policyRows = KINDS.map((k) => {
    const p = rel.policy[k] || {};
    return `<tr><td class="mono">${k}<div class="muted small">${esc(KIND_HELP[k])}</div></td>
      <td><input type="number" min="0" max="10" data-pk="${k}" data-f="retries" value="${p.retries ?? 0}" style="width:70px"></td>
      <td><input type="checkbox" data-pk="${k}" data-f="fallback" ${p.fallback !== false ? "checked" : ""} style="width:auto"></td>
      <td><input type="number" min="0" data-pk="${k}" data-f="cooldown" value="${p.cooldown ?? 0}" style="width:90px"></td>
      <td><input type="checkbox" data-pk="${k}" data-f="breaker" ${p.breaker !== false ? "checked" : ""} style="width:auto"></td></tr>`;
  }).join("");
  const w = (p, k) => l.weights?.[p]?.[k] ?? 0;
  $("#main").innerHTML = `<form id="rf">
    <h2>Router</h2>
    <div class="card">
      <div class="fields">
        <div class="field"><label>Perfil por defecto (si no viene <span class="mono">model</span>)</label><select id="r-def">
          ${["auto", ...Object.keys(cfg.agents)].map((a) => `<option ${cfg.default_profile === a ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></div>
        <div class="field"><label>Confianza mínima de las reglas (nivel 0)</label><input id="r-conf" type="number" step="0.05" min="0" max="1" value="${r.rules_min_confidence}"></div>
        <div class="field"><label>Contexto largo desde (tokens)</label><input id="r-long" type="number" min="1000" value="${r.long_context_tokens}"></div>
      </div>
      <h3>Juez (nivel 1)</h3>
      <div class="checks field"><label><input type="checkbox" id="j-on" ${r.judge.enabled ? "checked" : ""}> Usar el juez cuando las reglas dudan</label></div>
      <div class="field"><label>Modelos del juez, en orden</label><div class="checks">${models.map((m) =>
        `<label><input type="checkbox" class="j-m" value="${esc(m)}" ${r.judge.chain.includes(m) ? "checked" : ""}> ${esc(m)}</label>`).join("")}</div>
        <div class="muted small">Orden actual: ${r.judge.chain.map(esc).join(" → ") || "—"} (los nuevos se añaden al final).</div></div>
      <div class="fields">
        <div class="field"><label>Timeout del juez (s)</label><input id="j-to" type="number" min="1" value="${r.judge.timeout}"></div>
        <div class="field"><label>Caché de decisiones</label><input id="j-cache" type="number" min="1" value="${r.judge.cache_size}"></div>
        <div class="field"><label>Caracteres que ve el juez</label><input id="j-chars" type="number" min="500" value="${r.judge.max_chars}"></div>
      </div>
    </div>
    <h2 style="margin-top:20px">Fiabilidad</h2>
    <div class="card table-wrap"><table>
      <thead><tr><th>Error</th><th>Reintentos (mismo modelo)</th><th>Fallback</th><th>Cooldown (s)</th><th>Circuit breaker</th></tr></thead>
      <tbody>${policyRows}</tbody></table>
      <div class="fields" style="margin-top:12px">
        <div class="field"><label>Backoff base (s)</label><input id="b-base" type="number" step="0.1" min="0" value="${rel.backoff.base}"></div>
        <div class="field"><label>Backoff máximo (s)</label><input id="b-max" type="number" step="0.5" min="0" value="${rel.backoff.max}"></div>
        <div class="field"><label>Retry-After máximo a esperar (s)</label><input id="b-ra" type="number" min="0" value="${rel.backoff.max_retry_after}"></div>
        <div class="field"><label>Fallos para abrir el circuito</label><input id="c-th" type="number" min="1" value="${rel.circuit_breaker.failure_threshold}"></div>
        <div class="field"><label>Ventana de fallos (s)</label><input id="c-win" type="number" min="1" value="${rel.circuit_breaker.window}"></div>
        <div class="field"><label>Circuito abierto durante (s)</label><input id="c-open" type="number" min="1" value="${rel.circuit_breaker.open_seconds}"></div>
        <div class="field"><label>Timeout por intento (s)</label><input id="t-try" type="number" min="1" value="${rel.default_timeout}"></div>
        <div class="field"><label>Plazo total con fallbacks (s)</label><input id="t-dead" type="number" min="1" value="${rel.deadline}"></div>
      </div>
      <div class="checks"><label><input type="checkbox" id="b-resp" ${rel.backoff.respect_retry_after ? "checked" : ""}> Respetar Retry-After</label></div>
    </div>
    <h2 style="margin-top:20px">Aprendizaje</h2>
    <div class="card">
      <div class="checks field"><label><input type="checkbox" id="l-on" ${l.enabled ? "checked" : ""}> Reordenar las cadenas con la telemetría</label></div>
      <div class="fields">
        <div class="field"><label>Ventana (días)</label><input id="l-win" type="number" min="1" value="${l.window_days}"></div>
        <div class="field"><label>Muestras para confiar en los datos</label><input id="l-min" type="number" min="1" value="${l.min_samples}"></div>
        <div class="field"><label>Exploración (0–1)</label><input id="l-exp" type="number" step="0.01" min="0" max="1" value="${l.exploration}"></div>
        <div class="field"><label>Latencia de referencia (s)</label><input id="l-lat" type="number" min="0.1" step="0.5" value="${l.latency_ref}"></div>
      </div>
      <div class="table-wrap"><table><thead><tr><th>Prioridad</th><th>Calidad</th><th>Éxito</th><th>Velocidad</th><th>Costo</th></tr></thead><tbody>
        ${["quality", "speed", "cost"].map((p) => `<tr><td class="mono">${p}</td>${["quality", "success", "speed", "cost"].map((k) =>
          `<td><input type="number" step="0.05" min="0" max="1" data-wp="${p}" data-wk="${k}" value="${w(p, k)}" style="width:80px"></td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
    </div>
    <div class="row" style="margin-top:12px"><button class="primary" type="submit">Guardar</button></div>
  </form>`;
  $("#rf").onsubmit = async (e) => {
    e.preventDefault();
    const c = clone(state.cfg);
    c.default_profile = $("#r-def").value;
    c.router.rules_min_confidence = Number($("#r-conf").value);
    c.router.long_context_tokens = num("#r-long");
    const checked = $$(".j-m").filter((x) => x.checked).map((x) => x.value);
    c.router.judge = { enabled: $("#j-on").checked, timeout: num("#j-to"), cache_size: num("#j-cache"), max_chars: num("#j-chars"),
      chain: [...r.judge.chain.filter((m) => checked.includes(m)), ...checked.filter((m) => !r.judge.chain.includes(m))] };
    for (const el of $$("[data-pk]")) {
      const p = (c.reliability.policy[el.dataset.pk] ||= {});
      p[el.dataset.f] = el.type === "checkbox" ? el.checked : Number(el.value);
    }
    c.reliability.backoff = { base: Number($("#b-base").value), max: Number($("#b-max").value),
      respect_retry_after: $("#b-resp").checked, max_retry_after: Number($("#b-ra").value) };
    c.reliability.circuit_breaker = { failure_threshold: num("#c-th"), window: Number($("#c-win").value), open_seconds: Number($("#c-open").value) };
    c.reliability.default_timeout = Number($("#t-try").value);
    c.reliability.deadline = Number($("#t-dead").value);
    c.learning = { ...c.learning, enabled: $("#l-on").checked, window_days: num("#l-win"), min_samples: num("#l-min"),
      exploration: Number($("#l-exp").value), latency_ref: Number($("#l-lat").value) };
    for (const el of $$("[data-wp]")) ((c.learning.weights[el.dataset.wp] ||= {})[el.dataset.wk] = Number(el.value));
    if (await save("PUT", "/admin/api/config", c)) show("routing");
  };
}

// ---------------------------------------------------------------------------
// Probar
// ---------------------------------------------------------------------------

async function viewPlayground() {
  const cfg = state.cfg || (await loadConfig());
  const opts = ["auto", ...Object.keys(cfg.agents), ...Object.keys(cfg.models)];
  const saved = store.get("aio_pg") || "Revisa este proyecto y dime por qué la autenticación está fallando, luego corrígelo.";
  $("#main").innerHTML = `<h2>Probar</h2>
    <div class="card"><form id="pg">
      <div class="fields">
        <div class="field"><label>model</label><select id="p-model">${opts.map((o) => `<option>${esc(o)}</option>`).join("")}</select></div>
        <div class="field"><label>Prioridad</label><select id="p-prio"><option value="">la del agente</option><option>quality</option><option>speed</option><option>cost</option></select></div>
        <div class="field"><label>URL de imagen (opcional)</label><input id="p-img" placeholder="https://… o data:image/…"></div>
      </div>
      <div class="field"><label>Mensaje</label><textarea id="p-msg" style="font-family:inherit;min-height:100px">${esc(saved)}</textarea></div>
      <div class="row"><button type="button" id="p-route">Ver ruta (sin llamar)</button><button class="primary" type="submit">Enviar</button>
        <span id="p-status" class="muted small"></span></div>
    </form></div>
    <div id="p-out"></div>`;
  const body = () => {
    const text = $("#p-msg").value;
    store.set("aio_pg", text);
    const img = $("#p-img").value.trim();
    const content = img ? [{ type: "text", text }, { type: "image_url", image_url: { url: img } }] : text;
    const b = { model: $("#p-model").value, messages: [{ role: "user", content }] };
    if ($("#p-prio").value) b.orchestrator = { priority: $("#p-prio").value };
    return b;
  };
  const planHtml = (p) => `<div class="card"><h3>Ruta</h3>
    <div class="row"><span class="pill ok">${esc(p.decision.agent)}</span><span class="pill">${esc(p.decision.source)}</span>
      <span class="pill">confianza ${fmtNum(p.decision.confidence, 2)}</span><span class="pill">${esc(p.priority)}</span>
      <span class="pill">${esc(p.decision.modality)} · ${esc(p.decision.context)} · razonamiento ${esc(p.decision.reasoning)}</span></div>
    <p class="small">${p.decision.reasons.map(esc).join(" · ")}</p>
    <div class="small">Cadena: <span class="mono">${p.chain.map(esc).join(" → ")}</span></div>
    ${Object.keys(p.dropped || {}).length ? `<div class="small muted">Descartados: ${Object.entries(p.dropped).map(([m, w]) => `${esc(m)} (${esc(w)})`).join(", ")}</div>` : ""}
    ${p.ranked?.length ? `<div class="small muted">Puntajes: ${p.ranked.map((r) => `${esc(r.model)} ${fmtNum(r.score, 3)} (${r.samples})`).join(" · ")}</div>` : ""}</div>`;
  $("#p-route").onclick = async () => {
    try { $("#p-out").innerHTML = planHtml(await api("POST", "/admin/api/route", body())); }
    catch (e) { $("#p-out").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  };
  $("#pg").onsubmit = async (e) => {
    e.preventDefault();
    $("#p-status").textContent = "esperando respuesta…";
    const t0 = performance.now();
    try {
      const b = body();
      const [plan, r] = await Promise.all([api("POST", "/admin/api/route", b).catch(() => null), api("POST", "/admin/api/playground", b)]);
      const o = r.orchestrator;
      const attempts = o.attempts.map((a) => `<tr><td class="mono">${esc(a.model)}</td>
        <td>${a.ok ? `<span class="pill ok">ok</span>` : a.skipped ? `<span class="pill warn">saltado: ${esc(a.skipped)}</span>` : `<span class="pill bad">${esc(a.kind)} ${esc(a.status ?? "")}</span>`}</td>
        <td>${a.skipped ? "" : `${fmtNum(a.latency, 2)} s`}</td><td class="small muted">${esc(a.message || "")}</td></tr>`).join("");
      $("#p-out").innerHTML = `${plan ? planHtml(plan) : ""}
        <div class="card"><div class="row"><h3>Respuesta</h3><span class="spacer"></span>
          <span class="muted small">${esc(o.provider)} · <span class="mono">${esc(o.upstream_model)}</span> · ${fmtNum((performance.now() - t0) / 1000, 2)} s</span></div>
          <pre>${esc(r.choices?.[0]?.message?.content ?? JSON.stringify(r.choices?.[0]?.message, null, 2))}</pre>
          <h3>Intentos</h3><div class="table-wrap"><table><tbody>${attempts}</tbody></table></div>
          <div class="row" style="margin-top:8px"><span class="muted small">¿Qué tal la respuesta?</span>
            ${[1, 0.5, 0].map((q) => `<button type="button" data-q="${q}">${q === 1 ? "Buena" : q === 0.5 ? "Regular" : "Mala"}</button>`).join("")}</div></div>`;
      $("#p-status").textContent = "";
      $$("[data-q]").forEach((btn) => btn.onclick = async () => {
        try {
          await fetch("/v1/feedback", { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${store.get("aio_token") || ""}` },
            body: JSON.stringify({ request_id: o.request_id, quality: Number(btn.dataset.q) }) });
          toast("Gracias: cuenta para el aprendizaje");
        } catch (err) { toast(err.message, "bad"); }
      });
    } catch (err) {
      $("#p-status").textContent = "";
      $("#p-out").innerHTML = `<div class="notice bad">${esc(err.message)}</div>`;
    }
  };
}

// ---------------------------------------------------------------------------
// Peticiones
// ---------------------------------------------------------------------------

async function viewRequests() {
  const render = async () => {
    const rows = await api("GET", "/admin/api/requests?limit=200");
    $("#main").innerHTML = `<div class="row"><h2>Peticiones</h2><span class="spacer"></span><span class="muted small">últimos 200 intentos · cada 10 s</span></div>
      <div class="card table-wrap"><table><thead><tr><th>Hora</th><th>Agente</th><th>Modelo</th><th>Proveedor</th><th>Resultado</th>
        <th>Latencia</th><th>Tokens</th><th>Calidad</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td class="small">${fmtTime(r.ts)}</td><td class="mono">${esc(r.agent)}<div class="muted small">${esc(r.route_source || "")}</div></td>
        <td class="mono">${esc(r.model)}</td><td class="small">${esc(r.provider || "")}<div class="muted mono">${esc(r.upstream_model || "")}</div></td>
        <td>${r.ok ? `<span class="pill ok">${r.final ? "ok" : "ok (parcial)"}</span>` : `<span class="pill bad">${esc(r.error_kind)} ${esc(r.status ?? "")}</span>`}${r.stream ? ` <span class="pill">stream</span>` : ""}</td>
        <td>${r.latency != null ? `${fmtNum(r.latency, 2)} s` : "—"}</td>
        <td class="small">${r.prompt_tokens != null ? `${fmtNum(r.prompt_tokens)} + ${fmtNum(r.completion_tokens)}` : "—"}</td>
        <td>${r.quality != null ? fmtNum(r.quality, 2) : "—"}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">Sin peticiones todavía.</td></tr>`}
      </tbody></table></div>`;
  };
  await render();
  state.timer = setInterval(() => render().catch(() => {}), 10000);
}

// ---------------------------------------------------------------------------
// YAML
// ---------------------------------------------------------------------------

async function viewYaml() {
  const text = await api("GET", "/admin/api/config.yaml", undefined, true);
  $("#main").innerHTML = `<div class="row"><h2>Configuración en YAML</h2><span class="spacer"></span>
      <button id="y-dl">Descargar</button><button id="y-save" class="primary">Guardar</button></div>
    <p class="muted">Edición avanzada de todo de una vez. Las claves aparecen tapadas; si las dejas así, se conservan.</p>
    <textarea id="y-text" spellcheck="false" style="min-height:70vh">${esc(text)}</textarea>`;
  $("#y-save").onclick = async () => {
    try {
      const r = await api("PUT", "/admin/api/config.yaml", $("#y-text").value);
      state.cfg = r.config;
      toast("Guardado y aplicado");
    } catch (e) { toast(e.message, "bad"); }
  };
  $("#y-dl").onclick = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([$("#y-text").value], { type: "text/yaml" }));
    a.download = "ai-orchestrator-config.yaml";
    a.click();
    URL.revokeObjectURL(a.href);
  };
}


// ---------------------------------------------------------------------------
// Terminal: jmd, Claude Code, OpenCode, ahorro de tokens
// ---------------------------------------------------------------------------

function codeLine(text) {
  return `<div class="code-line"><pre>${esc(text)}</pre><button type="button" class="copy" data-copy="${esc(text)}">Copiar</button></div>`;
}

const JMD_COMMANDS = [
  ["jmd", "modo interactivo: comandos y, si no es un comando, chat"],
  ["jmd status", "gateway, tokens, Claude Code, OpenCode, RTK y caveman"],
  ["jmd models", "perfiles y modelos con su estado"],
  ["jmd providers · jmd quotas", "cuota y saldo por proveedor y modelo"],
  ["jmd provider <n> test", "modelos reales del proveedor y cuáles faltan"],
  ["jmd stats", "uso, éxito, latencia, calidad y ahorro (+ rtk gain)"],
  ["jmd route \"texto\"", "qué agente y cadena tocarían, sin llamar"],
  ["jmd chat \"texto\" -m coding", "pregunta con streaming (sin texto: chat)"],
  ["jmd style full --compress on", "respuestas cortas y salidas de herramientas recortadas"],
  ["jmd setup claude|opencode|all", "conecta el agente (y RTK) con el gateway"],
  ["jmd reset <modelo> · jmd ui", "quita cooldowns · abre esta UI"],
];

async function viewTerminal() {
  const cfg = await loadConfig();
  const sv = await api("GET", "/admin/api/savings");
  const origin = location.origin;
  const profiles = ["auto", ...Object.keys(cfg.agents), ...Object.keys(cfg.models)];
  const aliases = Object.entries(cfg.compat?.model_aliases || {});
  const claudeJson = JSON.stringify({ env: { ANTHROPIC_BASE_URL: origin, ANTHROPIC_AUTH_TOKEN: "<una de GATEWAY_API_KEYS, o cualquier texto si no hay>" } }, null, 2);
  const ocJson = JSON.stringify({ $schema: "https://opencode.ai/config.json", provider: { jmd: { npm: "@ai-sdk/openai-compatible", name: "JMD (gateway)",
    options: { baseURL: `${origin}/v1`, apiKey: "{env:JMD_API_KEY}" },
    models: Object.fromEntries(["auto", ...Object.keys(cfg.agents)].map((m) => [m, { name: m }])) } }, model: "jmd/auto" }, null, 2);
  const rows = (sv.by_style || []).map((r) => `<tr><td class="mono">${esc(r.style)}</td><td>${esc(r.client)}</td><td>${fmtNum(r.responses)}</td>
    <td>${fmtNum(r.avg_output_tokens)}</td><td>${fmtNum(r.avg_input_tokens)}</td><td>≈${fmtNum(r.tool_chars_saved / 4)} tok</td></tr>`).join("");
  $("#main").innerHTML = `<h2>Terminal e integraciones</h2>
    <div class="grid">
      <div class="card"><h3>1 · Instalar jmd</h3>
        <p class="muted small">Linux y WSL (el binario sale de este servidor):</p>
        ${codeLine(`mkdir -p ~/.local/bin && curl -fsSL ${origin}/download/jmd -o ~/.local/bin/jmd && chmod +x ~/.local/bin/jmd`)}
        <p class="muted small">macOS y Windows (necesita Rust):</p>
        ${codeLine("cargo install --git https://github.com/davrv93/ddesign-k ai-orchestrator --bin jmd")}
        <p class="muted small">Mientras no esté en la rama principal, añade <span class="mono">--branch claude/magical-noether-egkbo5</span>.</p>
        <p class="muted small">Conectar (el token es el mismo de esta UI):</p>
        ${codeLine(`jmd login --url ${origin} --token <ADMIN_TOKEN>`)}
        ${codeLine("jmd status")}
      </div>
      <div class="card"><h3>Comandos</h3>
        <table class="cmds"><tbody>${JMD_COMMANDS.map(([c, d]) => `<tr><td class="mono">${esc(c)}</td><td class="small">${esc(d)}</td></tr>`).join("")}</tbody></table>
      </div>
    </div>
    <div class="grid" style="margin-top:12px">
      <div class="card"><h3>2 · Claude Code</h3>
        <p class="small">El gateway habla también la API de Anthropic (<span class="mono">/v1/messages</span>), así que Claude Code, RTK y caveman funcionan tal cual.</p>
        ${codeLine("jmd setup claude")}
        <p class="muted small">o a mano, en <span class="mono">~/.claude/settings.json</span>:</p>
        <pre>${esc(claudeJson)}</pre>
        <p class="muted small">Si <span class="mono">ANTHROPIC_BASE_URL</span> está definida en la terminal, gana a settings.json.</p>
        <h3 style="margin-top:12px">Qué modelo usa cada nombre de Claude Code</h3>
        <form id="aliases"><table><thead><tr><th>Nombre que pide (con *)</th><th>Perfil del gateway</th></tr></thead><tbody>
          ${aliases.map(([p, t], i) => `<tr><td><input class="a-pat mono" value="${esc(p)}"></td><td><select class="a-tgt">${profiles.map((x) =>
            `<option ${x === t ? "selected" : ""}>${esc(x)}</option>`).join("")}${profiles.includes(t) ? "" : `<option selected>${esc(t)}</option>`}</select></td></tr>`).join("")}
          <tr><td><input class="a-pat mono" placeholder="nuevo, p. ej. claude-*"></td><td><select class="a-tgt">${profiles.map((x) => `<option>${esc(x)}</option>`).join("")}</select></td></tr>
        </tbody></table><div class="row" style="margin-top:8px"><button class="primary" type="submit">Guardar alias</button>
          <span class="muted small">deja el nombre vacío para borrar</span></div></form>
      </div>
      <div class="card"><h3>3 · OpenCode</h3>
        ${codeLine("jmd setup opencode")}
        <p class="muted small">o a mano, en <span class="mono">opencode.json</span>:</p>
        <pre>${esc(ocJson)}</pre>
      </div>
    </div>
    <div class="card" style="margin-top:12px"><h3>4 · Ahorro de tokens</h3>
      <form id="sv"><div class="fields">
        <div class="field"><label>Estilo de respuesta (a la manera de caveman, para todos los clientes)</label><select id="sv-style">
          ${["off", "lite", "full", "ultra"].map((x) => `<option ${sv.config.style === x ? "selected" : ""}>${x}</option>`).join("")}</select></div>
        <div class="field"><label>Máx. caracteres por salida de herramienta</label><input id="sv-max" type="number" min="500" value="${sv.config.tool_output_max_chars}"></div>
      </div>
      <div class="checks field"><label><input type="checkbox" id="sv-cmp" ${sv.config.compress_tool_output ? "checked" : ""}>
        Comprimir salidas de herramientas en el gateway (ANSI, líneas repetidas, recorte por el medio: a la manera de RTK)</label></div>
      <div class="row"><button class="primary" type="submit">Guardar</button></div></form>
      <p class="small muted">RTK comprime la salida de los comandos en el equipo de cada persona, antes de que llegue al agente (<span class="mono">jmd setup claude</span> instala su hook).
        caveman es un plugin de Claude Code; si lo usas, deja aquí el estilo en <span class="mono">off</span> para no recortar dos veces.</p>
      <div class="table-wrap"><table><thead><tr><th>Estilo</th><th>Cliente</th><th>Respuestas</th><th>Tokens salida (media)</th><th>Tokens entrada (media)</th><th>Quitado de herramientas</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6" class="muted">Sin datos todavía.</td></tr>`}</tbody></table></div>
      <p class="muted small">Últimos ${sv.window_days} días. Compara los tokens de salida entre off y full/ultra para ver cuánto ahorra el estilo.</p>
    </div>`;
  $$("[data-copy]").forEach((b) => b.onclick = async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Copiado"); } catch { toast("No se pudo copiar", "bad"); }
  });
  $("#sv").onsubmit = async (e) => {
    e.preventDefault();
    if (await save("PUT", "/admin/api/savings", { style: $("#sv-style").value, compress_tool_output: $("#sv-cmp").checked,
      tool_output_max_chars: num("#sv-max") })) show("terminal");
  };
  $("#aliases").onsubmit = async (e) => {
    e.preventDefault();
    const c = clone(state.cfg);
    c.compat = { model_aliases: {} };
    const pats = $$(".a-pat"), tgts = $$(".a-tgt");
    pats.forEach((p, i) => { const k = p.value.trim(); if (k) c.compat.model_aliases[k] = tgts[i].value; });
    if (await save("PUT", "/admin/api/config", c)) show("terminal");
  };
}

// ---------------------------------------------------------------------------

const VIEWS = { panel: viewPanel, providers: viewProviders, models: viewModels, agents: viewAgents,
  routing: viewRouting, playground: viewPlayground, terminal: viewTerminal, requests: viewRequests, yaml: viewYaml };

$$("#nav button").forEach((b) => b.onclick = () => show(b.dataset.view));
$("#logout").onclick = () => { store.set("aio_token", null); state.cfg = null; login(); };
$("#theme").onclick = () => {
  const cur = document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  store.set("aio_theme", next);
};
const theme = store.get("aio_theme");
if (theme) document.documentElement.dataset.theme = theme;

if (!store.get("aio_token")) login();
else show(store.get("aio_view") || "panel");
