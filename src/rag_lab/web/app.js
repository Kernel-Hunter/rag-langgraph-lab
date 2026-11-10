"use strict";

// ---------- connection ----------
const TOKEN = (() => {
  const m = location.hash.match(/token=([^&]+)/);
  if (m) {
    sessionStorage.setItem("raglab.token", m[1]);
    history.replaceState(null, "", location.pathname);
  }
  return sessionStorage.getItem("raglab.token") || "";
})();

async function api(path, { method = "GET", json, body, signal } = {}) {
  const headers = { "X-Token": TOKEN };
  if (json !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, signal, body: json !== undefined ? JSON.stringify(json) : body });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return res;
}

// ---------- helpers ----------
const $ = (s) => document.querySelector(s);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ICON = {
  spark: '<svg viewBox="0 0 20 20"><path d="M10 2.5l1.8 4.7 4.7 1.8-4.7 1.8L10 15.5l-1.8-4.7L3.5 9l4.7-1.8z"/></svg>',
  search: '<svg viewBox="0 0 20 20"><circle cx="9" cy="9" r="5"/><path d="M13 13l3.5 3.5"/></svg>',
  doc: '<svg viewBox="0 0 20 20"><path d="M5 3h7l3 3v11H5z"/><path d="M12 3v3h3"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 20 20"><path d="M6 8l4 4 4-4"/></svg>',
  copy: '<svg viewBox="0 0 20 20"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M13 7V5a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2"/></svg>',
  ok: '<svg viewBox="0 0 20 20"><path d="M5 10.5l3 3 7-7"/></svg>',
  warn: '<svg viewBox="0 0 20 20"><path d="M10 3l8 14H2z"/><path d="M10 8v4M10 14.5v.5"/></svg>',
  trash: '<svg viewBox="0 0 20 20"><path d="M4 6h12M8 6V4h4v2M6 6l1 11h6l1-11"/></svg>',
  check: '<svg viewBox="0 0 20 20"><path d="M5 10.5l3 3 7-7"/></svg>',
};

function toast(text, err = false) {
  const t = el("div", "toast" + (err ? " err" : ""), esc(text));
  $("#toasts").append(t);
  setTimeout(() => t.remove(), err ? 7000 : 3500);
}

// ---------- state ----------
let S = null; // server state
const chats = (() => { try { return JSON.parse(localStorage.getItem("raglab.chats")) || []; } catch (_) { return []; } })();
let currentId = chats[0]?.id || null;
let streaming = null; // AbortController while an answer streams
const modelCache = {};

const saveChats = () => { try { localStorage.setItem("raglab.chats", JSON.stringify(chats.slice(0, 100))); } catch (_) { /* storage full or blocked */ } };
const current = () => chats.find((c) => c.id === currentId) || null;
const provider = (id) => S?.providers.find((p) => p.id === id);

// ---------- server state ----------
let pollTimer = null;
async function refresh() {
  try {
    S = await (await api("/api/state")).json();
    renderStatus();
    renderModelBtn();
    renderMode();
    $("#docCount").textContent = S.docs.length;
    if (!$("#overlay").hidden) renderModal();
  } catch (e) {
    $("#statusText").textContent = "Disconnected";
    $("#status").className = "status error";
  }
  clearTimeout(pollTimer);
  const busy = !S || ["idle", "loading", "embedding"].includes(S.index.status);
  pollTimer = setTimeout(refresh, busy ? 900 : 8000);
}

async function saveSettings(values) {
  S = await (await api("/api/settings", { method: "POST", json: values })).json();
  renderStatus(); renderModelBtn(); renderMode();
  if (!$("#overlay").hidden) renderModal();
  refresh();
}

function renderStatus() {
  const { status, chunks, error } = S.index;
  const box = $("#status");
  const local = S.settings.embeddings === "local";
  const map = {
    ready: ["ready", `${S.docs.length} doc${S.docs.length === 1 ? "" : "s"} · ${chunks} chunks`],
    loading: ["busy", "Loading index…"],
    embedding: ["busy", local ? "Indexing… first run downloads a 65 MB model" : "Indexing documents…"],
    idle: ["busy", "Starting…"],
    empty: ["error", "No documents"],
    error: ["error", "Index error"],
  };
  const [cls, text] = map[status] || ["", status];
  box.className = "status " + cls;
  $("#statusText").textContent = text;
  box.title = error || "";
  if (status === "error" && error && box.dataset.err !== error) toast(error, true);
  box.dataset.err = error || "";
}

function renderModelBtn() {
  const p = provider(S.settings.provider);
  const model = S.settings.model || p?.chat_model || "";
  $("#modelLabel").textContent = p ? `${p.label.replace(/ \(.*\)/, "")} · ${model || "choose model"}` : "Choose a model";
  $("#modelDot").className = "pdot " + (!p ? "" : p.local || p.has_key ? "ok" : "no");
  $("#checkToggle").classList.toggle("on", !!S.settings.check);
  $("#checkToggle").style.display = S.settings.mode === "agent" ? "none" : "";
  $("#footnote").textContent = p && p.needs_key && !p.has_key
    ? `Add your ${p.label} key in Settings to start chatting.`
    : "Answers use only your documents. Check the sources for anything important.";
}

function renderMode() {
  document.querySelectorAll("#modeSwitch button").forEach((b) => b.classList.toggle("on", b.dataset.mode === S.settings.mode));
}

// ---------- sidebar ----------
function renderSidebar() {
  const list = $("#chatList");
  list.innerHTML = "";
  if (!chats.length) list.append(el("div", "pop-note", "Your chats appear here."));
  for (const c of chats) {
    const item = el("button", "chat-item" + (c.id === currentId ? " active" : ""));
    item.append(el("span", "", esc(c.title || "New chat")));
    const del = el("span", "del", ICON.trash);
    del.title = "Delete chat";
    del.onclick = (e) => {
      e.stopPropagation();
      chats.splice(chats.indexOf(c), 1);
      if (currentId === c.id) currentId = chats[0]?.id || null;
      saveChats(); renderSidebar(); renderThread();
    };
    item.append(del);
    item.onclick = () => { if (!streaming) { currentId = c.id; renderSidebar(); renderThread(); } };
    list.append(item);
  }
  $("#chatTitle").textContent = current()?.title || "New chat";
}

function newChat() {
  if (streaming) return;
  currentId = null;
  renderSidebar(); renderThread();
  $("#input").focus();
}

// ---------- thread ----------
const SUGGESTIONS = [
  ["Summarize", "Summarize the key points of my documents."],
  ["Dates and deadlines", "What deadlines or dates are mentioned?"],
  ["Absences", "How many absences are allowed before I fail?"],
  ["Test a refusal", "Can I bring my dog?"],
];

function renderThread() {
  const c = current();
  const msgs = $("#messages");
  msgs.innerHTML = "";
  $("#empty").hidden = !!(c && c.messages.length);
  if (!c || !c.messages.length) {
    const box = $("#suggestions");
    box.innerHTML = "";
    for (const [title, q] of SUGGESTIONS) {
      const b = el("button", "suggestion", `<strong>${esc(title)}</strong>${esc(q)}`);
      b.onclick = () => send(q);
      box.append(b);
    }
  } else {
    c.messages.forEach((m) => msgs.append(m.role === "user" ? userNode(m) : botNode(m)));
  }
  $("#chatTitle").textContent = c?.title || "New chat";
  scrollDown(true);
}

function userNode(m) {
  const n = el("div", "msg user");
  n.append(el("div", "body", esc(m.text)));
  return n;
}

function renderMarkdown(text) {
  const html = DOMPurify.sanitize(marked.parse(text || "", { breaks: true, gfm: true }));
  return html.replace(/\[(?:pages?|pp?\.)\s*(\d+)(?:\s*(?:-|–|,|and)\s*(\d+))?[^\]<]{0,24}\]/gi,
    (_, a, b) => `<span class="cite" data-page="${a}">p.${a}${b ? "–" + b : ""}</span>`);
}

function botNode(m) {
  const n = el("div", "msg bot");
  n.append(el("div", "avatar", ICON.spark));
  const body = el("div", "body");
  n.append(body);
  fillBot(body, m);
  return n;
}

function fillBot(body, m, live = false) {
  const openBefore = body.querySelector(".sources.open") !== null;
  body.innerHTML = "";
  if (m.searches?.length) {
    const tr = el("div", "trace");
    m.searches.forEach((q) => tr.append(el("span", "tchip", `${ICON.search}${esc(q)}`)));
    body.append(tr);
  }
  if (m.error) {
    body.append(el("div", "msg-error", esc(m.error)));
  } else if (!m.text && live) {
    const label = m.sources?.length ? `Reading ${m.sources.length} passages…` : (S?.settings.mode === "agent" ? "Thinking…" : "Searching your documents…");
    body.append(el("div", "thinking", `<span class="shimmer">${esc(label)}</span>`));
  } else {
    const md = el("div", "md" + (live ? " cursor" : ""), renderMarkdown(m.text));
    md.querySelectorAll(".cite").forEach((c) => (c.onclick = () => flashSource(body, c.dataset.page)));
    body.append(md);
  }
  if (m.supported === true || m.supported === false) {
    body.append(el("div", "verdict " + (m.supported ? "yes" : "no"),
      m.supported ? `${ICON.ok}Supported by the sources` : `${ICON.warn}Not fully supported by the sources`));
  }
  if (m.sources?.length) {
    const wrap = el("div", "sources" + (openBefore ? " open" : ""));
    const pages = [...new Set(m.sources.map((s) => s.page))].join(", ");
    const t = el("button", "sources-toggle", `${ICON.doc}${m.sources.length} sources · p.${esc(pages)}${ICON.chev}`);
    t.onclick = () => wrap.classList.toggle("open");
    const grid = el("div", "source-grid");
    m.sources.forEach((s) => {
      const card = el("button", "source");
      card.dataset.page = s.page;
      card.innerHTML = `<div class="src-head"><span class="pg">p.${esc(s.page)}</span><span class="fn">${esc(s.source)}</span></div><div class="excerpt">${esc(s.text)}</div>`;
      card.onclick = () => card.classList.toggle("expanded");
      grid.append(card);
    });
    wrap.append(t, grid);
    body.append(wrap);
  }
  if (!live && m.text) {
    const act = el("div", "msg-actions");
    const copy = el("button", "", ICON.copy);
    copy.title = "Copy";
    copy.onclick = () => navigator.clipboard?.writeText(m.text).then(() => toast("Copied"));
    act.append(copy);
    body.append(act);
  }
}

function flashSource(body, page) {
  const wrap = body.querySelector(".sources");
  if (!wrap) return;
  wrap.classList.add("open");
  wrap.querySelectorAll(`.source[data-page="${page}"]`).forEach((c) => {
    c.classList.add("flash");
    c.scrollIntoView({ block: "nearest", behavior: "smooth" });
    setTimeout(() => c.classList.remove("flash"), 1400);
  });
}

function scrollDown(force = false) {
  const t = $("#thread");
  if (force || t.scrollHeight - t.scrollTop - t.clientHeight < 160) t.scrollTop = t.scrollHeight;
}

// ---------- sending ----------
async function send(text) {
  const question = (text ?? $("#input").value).trim();
  if (!question || streaming) return;
  if (!S || S.index.status !== "ready") { toast(S?.index.error || "Documents are still indexing. One moment.", true); return; }
  const p = provider(S.settings.provider);
  if (p && p.needs_key && !p.has_key) { openModal("models", p.id); toast(`Add your ${p.label} key first.`, true); return; }

  let c = current();
  if (!c) {
    c = { id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()), title: question.slice(0, 60), messages: [] };
    chats.unshift(c);
    currentId = c.id;
  }
  $("#input").value = ""; autosize();
  c.messages.push({ role: "user", text: question });
  const m = { role: "bot", text: "", sources: [], searches: [], supported: null, error: null };
  c.messages.push(m);
  saveChats(); renderSidebar(); renderThread();

  const body = $("#messages").lastElementChild.querySelector(".body");
  fillBot(body, m, true);
  scrollDown(true);

  streaming = new AbortController();
  setSending(true);
  let frame = 0;
  const paint = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => { fillBot(body, m, true); scrollDown(); }); };

  try {
    const res = await api("/api/chat", { method: "POST", json: { question }, signal: streaming.signal });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = /^event: (.*)$/m.exec(raw)?.[1];
        const data = JSON.parse(/^data: (.*)$/m.exec(raw)?.[1] || "{}");
        if (ev === "token") m.text += data.text;
        else if (ev === "sources") m.sources = [...m.sources, ...data.items];
        else if (ev === "search") m.searches.push(data.query);
        else if (ev === "check") m.supported = data.supported;
        else if (ev === "error") m.error = data.message;
        paint();
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") m.error = e.message;
  } finally {
    cancelAnimationFrame(frame);
    streaming = null;
    setSending(false);
    if (!m.text && !m.error) m.text = "_Stopped._";
    saveChats();
    fillBot(body, m, false);
    scrollDown();
  }
}

function setSending(on) {
  const b = $("#send");
  b.classList.toggle("stop", on);
  b.title = on ? "Stop" : "Send";
}

// ---------- model picker ----------
let popProvider = null;
function openModelPop() {
  const pop = $("#modelPop");
  pop.hidden = false;
  popProvider = S.settings.provider;
  $("#modelSearch").value = "";
  renderPop();
  const r = $("#modelBtn").getBoundingClientRect();
  pop.style.left = Math.max(16, Math.min(r.left, innerWidth - pop.offsetWidth - 16)) + "px";
  pop.style.top = Math.max(16, r.top - pop.offsetHeight - 10) + "px";
  $("#modelSearch").focus();
}

function loadModels(pid) {
  if (!modelCache[pid]) {
    modelCache[pid] = { loading: true, list: provider(pid)?.suggestions || [] };
    api(`/api/models?provider=${encodeURIComponent(pid)}`).then((r) => r.json()).then((d) => {
      modelCache[pid] = { loading: false, list: d.models };
      if (!$("#modelPop").hidden) renderPop();
    }).catch(() => { modelCache[pid].loading = false; });
  }
  return modelCache[pid];
}

function renderPop() {
  const q = $("#modelSearch").value.toLowerCase();
  const left = $("#popProviders");
  left.innerHTML = "";
  for (const p of S.providers) {
    if (q && !p.label.toLowerCase().includes(q) && !(modelCache[p.id]?.list || []).some((m) => m.toLowerCase().includes(q))) continue;
    const b = el("button", "pop-item" + (p.id === popProvider ? " on" : ""),
      `<span class="pdot ${p.local || p.has_key ? "ok" : "no"}"></span><span class="lbl">${esc(p.label)}</span>${p.local ? '<span class="tag">local</span>' : p.has_key ? "" : '<span class="tag">no key</span>'}`);
    b.onclick = () => { popProvider = p.id; renderPop(); };
    left.append(b);
  }
  const right = $("#popModels");
  right.innerHTML = "";
  const p = provider(popProvider);
  if (!p) return;
  if (p.needs_key && !p.has_key) {
    const note = el("div", "pop-note", `Add a ${esc(p.label)} key to use it. `);
    const go = el("button", "", "Open settings");
    go.onclick = () => { $("#modelPop").hidden = true; openModal("models", p.id); };
    note.append(go);
    right.append(note);
  }
  const cache = loadModels(p.id);
  const active = S.settings.provider === p.id ? (S.settings.model || p.chat_model) : null;
  const list = cache.list.filter((m) => !q || m.toLowerCase().includes(q) || p.label.toLowerCase().includes(q));
  if (cache.loading) right.append(el("div", "pop-note shimmer", "Fetching models…"));
  for (const m of list.slice(0, 300)) {
    const b = el("button", "pop-item" + (m === active ? " on" : ""), `<span class="lbl">${esc(m)}</span>${m === active ? `<span class="chk">${ICON.check}</span>` : ""}`);
    b.onclick = () => pickModel(p.id, m);
    right.append(b);
  }
  const custom = el("div", "pop-custom");
  const inp = el("input");
  inp.placeholder = "Or type a model id";
  inp.onkeydown = (e) => { if (e.key === "Enter" && inp.value.trim()) pickModel(p.id, inp.value.trim()); };
  const use = el("button", "btn", "Use");
  use.onclick = () => inp.value.trim() && pickModel(p.id, inp.value.trim());
  custom.append(inp, use);
  right.append(custom);
}

async function pickModel(pid, model) {
  $("#modelPop").hidden = true;
  await saveSettings({ provider: pid, model });
  const p = provider(pid);
  toast(`Using ${p.label} · ${model}`);
  if (p.needs_key && !p.has_key) openModal("models", pid);
}

// ---------- settings modal ----------
let activeTab = "models";
function openModal(tab = "models", focusProvider = null) {
  activeTab = tab;
  $("#overlay").hidden = false;
  renderModal();
  if (focusProvider) setTimeout(() => document.querySelector(`[data-key="${focusProvider}"]`)?.focus(), 50);
}

function renderModal() {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === activeTab));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.panel === activeTab));
  if (activeTab === "models") renderProviders();
  if (activeTab === "docs") renderDocs();
  if (activeTab === "retrieval") renderRetrieval();
}

function keyRow(pid, hasKey, placeholder) {
  const row = el("div", "key-row");
  const inp = el("input");
  inp.type = "password";
  inp.dataset.key = pid;
  inp.placeholder = hasKey ? "•••••••• saved" : placeholder;
  const save = el("button", "btn", hasKey ? "Replace" : "Save");
  const doSave = async () => {
    if (!inp.value.trim()) return;
    await saveSettings({ keys: { [pid]: inp.value.trim() } });
    delete modelCache[pid];
    inp.blur();
    toast("Key saved");
    renderProviders();
  };
  save.onclick = doSave;
  inp.onkeydown = (e) => { if (e.key === "Enter") doSave(); };
  row.append(inp, save);
  if (hasKey) {
    const rm = el("button", "btn", "Remove");
    rm.onclick = async () => { await saveSettings({ keys: { [pid]: "" } }); delete modelCache[pid]; renderProviders(); };
    row.append(rm);
  }
  return row;
}

function renderProviders() {
  const grid = $("#providerGrid");
  if (grid.contains(document.activeElement)) return; // do not wipe a key being typed
  grid.innerHTML = "";
  for (const p of S.providers) {
    const card = el("div", "pcard");
    const state = p.local ? '<span class="state ok">● local</span>'
      : p.has_key ? '<span class="state ok">● connected</span>'
      : `<span class="state">${p.id === "custom" ? "optional key" : "no key"}</span>`;
    card.innerHTML = `<div class="pcard-head">${esc(p.label)}${state}</div>`;
    if (p.needs_key) {
      card.append(keyRow(p.id, p.has_key, p.id === "custom" ? "API key (if needed)" : "Paste API key"));
    } else {
      card.append(el("div", "muted", p.id === "ollama" ? "Runs on this computer. Install Ollama and pull a model." : "Runs on this computer. Start its local server first."));
    }
    if (p.key_url) {
      const a = el("a", "", p.needs_key ? "Get a key ↗" : "Download ↗");
      a.href = p.key_url; a.target = "_blank"; a.rel = "noreferrer";
      card.append(a);
    }
    grid.append(card);
  }
  const cu = $("#customUrl");
  if (document.activeElement !== cu) cu.value = S.custom_base_url || "";
  cu.onchange = () => saveSettings({ custom_base_url: cu.value }).then(() => toast("Custom endpoint saved"));
}

function renderDocs() {
  const list = $("#docList");
  list.innerHTML = "";
  if (!S.docs.length) list.append(el("div", "muted", "No documents yet."));
  for (const d of S.docs) {
    const row = el("div", "doc");
    const ext = d.name.split(".").pop();
    row.innerHTML = `<span class="ext">${esc(ext)}</span><span class="name">${esc(d.name)}</span><span class="size">${(d.size / 1024).toFixed(1)} KB</span>`;
    const rm = el("button", "icon-btn", ICON.trash);
    rm.title = "Remove";
    rm.onclick = async () => { S = await (await api(`/api/docs/${encodeURIComponent(d.name)}`, { method: "DELETE" })).json(); renderDocs(); refresh(); };
    row.append(rm);
    list.append(row);
  }
}

async function upload(files) {
  for (const f of files) {
    try {
      S = await (await api(`/api/docs?name=${encodeURIComponent(f.name)}`, { method: "POST", body: await f.arrayBuffer() })).json();
      toast(`Added ${f.name}`);
    } catch (e) { toast(`${f.name}: ${e.message}`, true); }
  }
  if (!$("#overlay").hidden) renderModal();
  refresh();
}

function renderRetrieval() {
  const sel = $("#embedSelect");
  const s = S.settings;
  const opts = [["local", "Built-in · free, runs on this computer"]].concat(
    S.providers.filter((p) => p.embeddings).map((p) => [p.id, `${p.label}${p.local ? "" : " · needs key"}`]));
  sel.innerHTML = opts.map(([v, l]) => `<option value="${v}" ${v === s.embeddings ? "selected" : ""}>${esc(l)}</option>`).join("");
  sel.onchange = () => saveSettings({ embeddings: sel.value }).then(() => toast("Re-indexing with new embeddings"));
  $("#embedHint").textContent = "turns text into vectors";
  const cs = $("#chunkSize"), k = $("#kRange");
  if (document.activeElement !== cs) cs.value = s.chunk_size;
  if (document.activeElement !== k) k.value = s.k;
  $("#chunkOut").textContent = cs.value; $("#kOut").textContent = k.value;
  cs.oninput = () => ($("#chunkOut").textContent = cs.value);
  k.oninput = () => ($("#kOut").textContent = k.value);
  cs.onchange = () => saveSettings({ chunk_size: +cs.value, chunk_overlap: Math.min(100, Math.round(cs.value / 6)) });
  k.onchange = () => saveSettings({ k: +k.value });
}

// ---------- wiring ----------
function autosize() {
  const t = $("#input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 220) + "px";
}

$("#input").addEventListener("input", autosize);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  if (streaming) streaming.abort(); else send();
});
$("#newChat").onclick = newChat;
$("#toggleSidebar").onclick = () => {
  const app = $("#app");
  if (innerWidth <= 820) app.classList.toggle("show-side"); else app.classList.toggle("collapsed");
};
$("#openSettings").onclick = () => openModal("models");
$("#openDocs").onclick = () => openModal("docs");
$("#status").onclick = () => openModal(S?.index.status === "empty" ? "docs" : "retrieval");
$("#closeModal").onclick = () => ($("#overlay").hidden = true);
$("#overlay").addEventListener("mousedown", (e) => { if (e.target.id === "overlay") $("#overlay").hidden = true; });
document.querySelectorAll("#tabs button").forEach((b) => (b.onclick = () => { activeTab = b.dataset.tab; renderModal(); }));
document.querySelectorAll("#modeSwitch button").forEach((b) => (b.onclick = () => saveSettings({ mode: b.dataset.mode })));
$("#checkToggle").onclick = () => saveSettings({ check: !S.settings.check });
$("#modelBtn").onclick = (e) => { e.stopPropagation(); if ($("#modelPop").hidden) openModelPop(); else $("#modelPop").hidden = true; };
$("#modelSearch").addEventListener("input", renderPop);
document.addEventListener("mousedown", (e) => {
  const pop = $("#modelPop");
  if (!pop.hidden && !pop.contains(e.target) && !$("#modelBtn").contains(e.target)) pop.hidden = true;
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { $("#modelPop").hidden = true; $("#overlay").hidden = true; }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") { e.preventDefault(); newChat(); }
  if ((e.metaKey || e.ctrlKey) && e.key === ",") { e.preventDefault(); openModal("models"); }
});
$("#fileInput").addEventListener("change", (e) => { upload([...e.target.files]); e.target.value = ""; });
const drop = $("#drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
drop.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); upload([...e.dataTransfer.files]); });
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer?.files.length) upload([...e.dataTransfer.files]); });
if (!/Mac/.test(navigator.platform)) document.querySelector(".btn-new kbd").textContent = "Ctrl N";

renderSidebar();
renderThread();
refresh();
$("#input").focus();
