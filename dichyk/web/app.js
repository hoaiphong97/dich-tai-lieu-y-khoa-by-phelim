"use strict";

/* ============================ Tiện ích ============================ */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name) => `<svg class="i"><use href="#i-${name}"/></svg>`;
const store = {
  get(k, d = null) { try { const v = localStorage.getItem("dyk:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("dyk:" + k, JSON.stringify(v)); } catch { /* bỏ qua */ } },
};

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.method = opts.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.detail) || `Lỗi ${res.status}`);
  return data;
}

let toastTimer;
function toast(msg, isErr = false) {
  $(".toast")?.remove();
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : "");
  el.textContent = msg;
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), isErr ? 6000 : 3000);
}

function fmtSize(b) { return b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB"; }
function fmtEta(s) {
  if (!s) return "—";
  if (s < 60) return `${s} giây`;
  const m = Math.round(s / 60);
  return m < 60 ? `~${m} phút` : `~${Math.floor(m / 60)} giờ ${m % 60} phút`;
}
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("vi-VN") + " " + d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Đã sao chép"); }
  catch {
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove(); toast("Đã sao chép");
  }
}

const STATUS = {
  queued: ["Đang chờ", ""], extracting: ["Đang đọc", "teal"], translating: ["Đang dịch", "teal"],
  waiting_agent: ["Chờ agent", "amber"], assembling: ["Đang ghép", "teal"],
  done: ["Hoàn tất", "green"], error: ["Lỗi", "red"], cancelled: ["Đã huỷ", ""],
};
const ACTIVE = ["queued", "extracting", "translating", "assembling"];
/* % tiến độ: lúc ghép bản dịch tính theo bước đang chạy (trang/hình), còn lại theo số đoạn đã dịch.
   null = chưa đo được (thanh chạy vô định). */
function jobPct(job) {
  if (job.status === "done") return 100;
  if (job.status === "assembling") return job.step_total ? Math.round((100 * job.step_done) / job.step_total) : null;
  if (["queued", "extracting"].includes(job.status)) return null;
  return job.total ? Math.round((100 * job.done) / job.total) : 0;
}
const statusChip = (s) => `<span class="chip ${STATUS[s]?.[1] || ""}">${STATUS[s]?.[0] || s}</span>`;

/* ============================ Trạng thái ============================ */
const state = {
  settings: null,
  presets: {},
  file: null,
  scope: "all",
  from: 1, to: 1, spec: "", chapter: -1,
  options: null,
  jobId: store.get("jobId"),
  quickJobId: null,
  pollTimer: null,
  resultTab: "preview",
};

/* ============================ Điều hướng ============================ */
const VIEWS = {
  "tai-lieu": ["doc", "Dịch tài liệu", "PDF tiếng Anh → Markdown tiếng Việt", renderDoc],
  "dich-nhanh": ["quick", "Dịch nhanh", "Dán một đoạn văn để dịch ngay", renderQuick],
  "lich-su": ["history", "Lịch sử", "Các tài liệu đã dịch", renderHistory],
  "thuat-ngu": ["terms", "Thuật ngữ", "Kho thuật ngữ Anh – Việt dùng khi dịch", renderTerms],
  "cai-dat": ["settings", "Cài đặt", "Nguồn dịch và tuỳ chọn mặc định", renderSettings],
};

function route() {
  const key = location.hash.replace(/^#\//, "") || "tai-lieu";
  const [id, title, sub, render] = VIEWS[key] || VIEWS["tai-lieu"];
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === id));
  $("#title").textContent = title;
  $("#subtitle").textContent = sub;
  $("#top-actions").innerHTML = "";
  stopPolling();
  render();
}
window.addEventListener("hashchange", route);

/* ============================ Nguồn dịch ============================ */
function engineLabel(s) {
  const preset = state.presets[s.preset];
  if (s.engine === "agent") return "Agent · dịch qua file";
  const name = preset ? preset.label.split(" (")[0] : s.engine;
  return `${name} · ${s.model || "chưa chọn model"}`;
}

async function refreshEngineStatus() {
  const s = state.settings;
  $("#engine-label").textContent = engineLabel(s);
  const dot = $("#engine-dot");
  dot.className = "dot";
  if (s.engine === "agent") { dot.classList.add("ok"); return; }
  if (!s.model) { dot.classList.add("bad"); return; }
  try {
    const { models } = await api("/api/engine/models", { body: { values: {} } });
    const ok = !models.length || models.includes(s.model) || models.some((m) => m.startsWith(s.model + ":"));
    dot.classList.add(ok ? "ok" : "bad");
    dot.parentElement.title = ok ? "Nguồn dịch sẵn sàng" : `Không thấy model ${s.model}`;
  } catch (e) {
    dot.classList.add("bad");
    dot.parentElement.title = e.message;
  }
}
$("#engine-pill").addEventListener("click", () => (location.hash = "#/cai-dat"));

/* ============================ Dịch tài liệu ============================ */
function defaultOptions() {
  const s = state.settings;
  return {
    bilingual_terms: s.bilingual_terms, keep_source: s.keep_source, detect_tables: s.detect_tables,
    output_pdf: s.output_pdf, output_md: s.output_md, pdf_interleave: s.pdf_interleave,
  };
}

async function renderDoc() {
  if (state.jobId) {
    try {
      const job = await api(`/api/jobs/${state.jobId}`);
      if (job.type === "pdf") return renderJob(job);
    } catch { /* job không còn */ }
    state.jobId = null; store.set("jobId", null);
  }
  if (state.file) return renderSetup();
  renderDrop();
}

function renderDrop() {
  $("#view").innerHTML = `
    <div class="drop" id="drop">
      <div>
        <div class="big">${icon("upload")}</div>
        <h2>Chọn tài liệu PDF cần dịch</h2>
        <p>Kéo thả file vào đây, hoặc bấm nút bên dưới. Sách dày hàng trăm trang vẫn được.</p>
        <button class="btn primary lg" id="pick">${icon("file")} Chọn file PDF</button>
        <div class="upload-bar" id="upload-bar" hidden><div></div></div>
        <p class="small muted" id="upload-note" style="margin-top:12px"></p>
      </div>
    </div>`;
  const drop = $("#drop");
  $("#pick").onclick = pickFile;
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) uploadFile(f); });
}

async function pickFile() {
  // Trong cửa sổ app: dùng hộp thoại chọn file của hệ điều hành (không phải chép file)
  if (window.pywebview?.api?.pick_pdf) {
    try {
      const path = await window.pywebview.api.pick_pdf();
      if (path) await openLocal(path);
      return;
    } catch { /* rơi xuống cách tải lên */ }
  }
  $("#file-input").click();
}
$("#file-input").addEventListener("change", (e) => { const f = e.target.files[0]; if (f) uploadFile(f); e.target.value = ""; });

async function openLocal(path) {
  try { setFile(await api("/api/files/local", { body: { path } })); }
  catch (e) { toast(e.message, true); }
}

function uploadFile(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) return toast("Chỉ hỗ trợ file PDF", true);
  const bar = $("#upload-bar"), note = $("#upload-note");
  if (bar) { bar.hidden = false; note.textContent = `Đang mở ${file.name}…`; }
  const form = new FormData();
  form.append("file", file);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/files");
  xhr.upload.onprogress = (e) => { if (bar && e.lengthComputable) bar.firstElementChild.style.width = (100 * e.loaded / e.total) + "%"; };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* */ }
    if (xhr.status >= 400) { toast(data.detail || "Không tải được file", true); if (bar) bar.hidden = true; return; }
    setFile(data);
  };
  xhr.onerror = () => toast("Không tải được file", true);
  xhr.send(form);
}

function setFile(info) {
  state.file = info;
  state.scope = "all";
  state.from = 1; state.to = Math.min(info.pages, 10); state.spec = ""; state.chapter = -1;
  state.options = defaultOptions();
  renderSetup();
}

function currentSpec() {
  const f = state.file;
  if (state.scope === "all") return "";
  if (state.scope === "chapter") {
    const c = f.toc[state.chapter];
    return c ? `${c.start}-${c.end}` : "";
  }
  return state.spec.trim() || `${state.from}-${state.to}`;
}

function countPages(spec, total) {
  if (!spec) return total;
  const pages = new Set();
  for (const part of spec.split(/[,;\s]+/).filter(Boolean)) {
    const m = part.match(/^(\d+)\s*[-–]\s*(\d+)$/);
    if (m) { let a = +m[1], b = +m[2]; if (a > b) [a, b] = [b, a]; for (let p = a; p <= b; p++) if (p >= 1 && p <= total) pages.add(p); }
    else if (/^\d+$/.test(part) && +part >= 1 && +part <= total) pages.add(+part);
    else return NaN;
  }
  return pages.size;
}

function renderSetup() {
  const f = state.file, o = state.options;
  $("#top-actions").innerHTML = `<button class="btn ghost" id="change-file">${icon("file")} Chọn file khác</button>`;
  $("#change-file").onclick = () => { state.file = null; renderDrop(); $("#top-actions").innerHTML = ""; };
  const hasToc = f.toc.length > 0;
  $("#view").innerHTML = `
    <div class="setup">
      <div class="panel">
        <div class="panel-body">
          <div class="file-card">
            <div class="file-icon">PDF</div>
            <div class="grow">
              <div class="name">${esc(f.name)}</div>
              <div class="muted small">${f.pages} trang · ${fmtSize(f.size)}${f.title && f.title !== f.name.replace(/\.pdf$/i, "") ? " · " + esc(f.title) : ""}</div>
            </div>
          </div>
          ${f.scanned_pages ? `<div class="notice amber" style="margin-top:14px">${icon("alert")}<div>Khoảng ${f.scanned_pages} trang là ảnh scan. App chưa đọc được chữ trong ảnh (OCR), các trang này sẽ bị bỏ qua.</div></div>` : ""}
        </div>
        <div class="panel-body" style="border-top:1px solid var(--line)">
          <div class="label" style="margin-bottom:8px">Phạm vi dịch</div>
          <div class="seg" id="scope">
            <button data-v="all">Toàn bộ</button>
            <button data-v="range">Khoảng trang</button>
            ${hasToc ? `<button data-v="chapter">Theo chương</button>` : ""}
          </div>
          <div id="scope-body"></div>
        </div>
        <div class="panel-body" style="border-top:1px solid var(--line);padding-top:8px;padding-bottom:8px">
          ${toggle("bilingual_terms", "Ghi kèm thuật ngữ tiếng Anh", "Lần đầu gặp trong mỗi mục: “xương hàm dưới (mandible)”", o.bilingual_terms)}
          ${toggle("keep_source", "Kèm bản gốc dưới mỗi đoạn", "Tiện đối chiếu; file Markdown sẽ dài gấp đôi", o.keep_source)}
          ${toggle("detect_tables", "Nhận diện bảng", "Dịch từng ô bảng, giữ nguyên khung bảng", o.detect_tables)}
        </div>
        <div class="panel-body" style="border-top:1px solid var(--line);padding-top:8px;padding-bottom:8px">
          <div class="label" style="margin:6px 0 2px">File kết quả</div>
          ${toggle("output_pdf", "PDF giữ bố cục, có hình", "Chữ Việt đặt vào đúng chỗ chữ gốc; hình, bảng, sơ đồ giữ nguyên", o.output_pdf)}
          ${toggle("pdf_interleave", "Xen kẽ trang gốc trong PDF", "Mỗi trang dịch đi kèm trang gốc ngay trước nó, tiện đối chiếu", o.pdf_interleave)}
          ${toggle("output_md", "Markdown kèm hình", "File chữ gọn để đọc, hình được cắt ra thư mục riêng", o.output_md)}
        </div>
        <div class="summary-line">
          <div class="grow" id="summary"></div>
          <button class="btn primary lg" id="start">${icon("play")} Bắt đầu dịch</button>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h2>Mục lục</h2><span class="muted small">${hasToc ? "Bấm để chọn chương" : ""}</span></div>
        ${hasToc ? `<div class="toc" id="toc">${f.toc.map((c, i) => `
            <button data-i="${i}" class="lv${c.level}"><span class="t">${esc(c.title)}</span><span class="p">tr. ${c.start}–${c.end}</span></button>`).join("")}</div>`
          : `<div class="panel-body muted small">PDF này không có mục lục (bookmark). Hãy chọn “Khoảng trang” để dịch một phần.</div>`}
      </div>
    </div>`;

  $$("#scope button").forEach((b) => (b.onclick = () => { state.scope = b.dataset.v; if (state.scope === "chapter" && state.chapter < 0) state.chapter = 0; drawScope(); }));
  $$("#toc button").forEach((b) => (b.onclick = () => { state.scope = "chapter"; state.chapter = +b.dataset.i; drawScope(); }));
  $$(".toggle input").forEach((inp) => (inp.onchange = () => { state.options[inp.name] = inp.checked; }));
  $("#start").onclick = startJob;
  drawScope();
}

function toggle(name, title, desc, on) {
  return `<label class="toggle"><input type="checkbox" name="${name}" ${on ? "checked" : ""}><span class="sw"></span>
    <span><b>${title}</b><small>${desc}</small></span></label>`;
}

function drawScope() {
  const f = state.file;
  $$("#scope button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.scope));
  $$("#toc button").forEach((b) => b.classList.toggle("on", state.scope === "chapter" && +b.dataset.i === state.chapter));
  const body = $("#scope-body");
  if (state.scope === "all") {
    body.innerHTML = `<p class="muted small" style="margin:12px 0 0">Dịch cả ${f.pages} trang.</p>`;
  } else if (state.scope === "range") {
    body.innerHTML = `
      <div class="range-inputs">
        <span>Từ trang</span><input class="input" type="number" min="1" max="${f.pages}" id="from" value="${state.from}">
        <span>đến</span><input class="input" type="number" min="1" max="${f.pages}" id="to" value="${state.to}">
        <span class="muted small">/ ${f.pages}</span>
      </div>
      <div class="field" style="margin:12px 0 0">
        <input class="input" id="spec" placeholder="Hoặc nhập nhiều khoảng, ví dụ: 1-10, 15, 20-25" value="${esc(state.spec)}">
      </div>`;
    const sync = () => {
      state.from = Math.max(1, Math.min(f.pages, +$("#from").value || 1));
      state.to = Math.max(1, Math.min(f.pages, +$("#to").value || 1));
      state.spec = $("#spec").value;
      drawSummary();
    };
    ["from", "to", "spec"].forEach((id) => $("#" + id).addEventListener("input", sync));
  } else {
    body.innerHTML = `<div class="field" style="margin:12px 0 0"><select class="input" id="chapter">
      ${f.toc.map((c, i) => `<option value="${i}" ${i === state.chapter ? "selected" : ""}>${c.level > 1 ? "— " : ""}${esc(c.title)} (tr. ${c.start}–${c.end})</option>`).join("")}
      </select></div>`;
    $("#chapter").onchange = (e) => { state.chapter = +e.target.value; drawScope(); };
  }
  drawSummary();
}

function drawSummary() {
  const n = countPages(currentSpec(), state.file.pages);
  const el = $("#summary");
  if (Number.isNaN(n) || n === 0) { el.innerHTML = `<span class="chip red">Khoảng trang không hợp lệ</span>`; $("#start").disabled = true; return; }
  $("#start").disabled = false;
  el.innerHTML = `<b>${n}</b> trang sẽ được dịch <span class="muted small">· ${esc(engineLabel(state.settings))}</span>`;
}

async function startJob() {
  const btn = $("#start");
  btn.disabled = true;
  try {
    const job = await api("/api/jobs", { body: { path: state.file.path, name: state.file.name, pages: currentSpec(), options: state.options } });
    state.jobId = job.id; store.set("jobId", job.id);
    renderJob(job);
    refreshNavCount();
  } catch (e) { toast(e.message, true); btn.disabled = false; }
}

/* ---------------------------- Màn hình job ---------------------------- */
function renderJob(job) {
  $("#top-actions").innerHTML = `<button class="btn ghost" id="new-doc">${icon("plus")} Dịch tài liệu khác</button>`;
  $("#new-doc").onclick = () => { state.jobId = null; store.set("jobId", null); state.file = null; stopPolling(); renderDoc(); };
  $("#view").innerHTML = `<div class="panel" id="job-panel"></div><div id="job-result"></div>`;
  state.resultTab = null;
  state.comparePage = 0;
  state.liveCtl = null;
  drawJob(job, true);
  if (ACTIVE.includes(job.status)) startPolling(job.id);
}

function pagesOfScope(job) {
  const m = job.scope.match(/Trang (.+)/);
  if (!m) return null;
  const pages = [];
  for (const part of m[1].split(",")) {
    const [a, b] = part.trim().split("–").map(Number);
    for (let p = a; p <= (b || a); p++) pages.push(p);
  }
  return pages;
}

let lastStatus = null;
function drawJob(job, full = false) {
  const panel = $("#job-panel");
  if (!panel) return;
  const active = ACTIVE.includes(job.status);
  const assembling = job.status === "assembling";
  const pct = jobPct(job);
  const pages = pagesOfScope(job);
  const strip = pages && pages.length <= 400
    ? `<div class="pages-strip" title="Mỗi ô là một trang">${pages.map((p) => `<span class="${p < job.current_page || job.status === "done" ? "done" : p === job.current_page && active ? "now" : ""}" title="Trang ${p}"></span>`).join("")}</div>`
    : "";
  panel.innerHTML = `
    <div class="panel-body">
      <div class="progress-head">
        <div class="file-icon">PDF</div>
        <div style="flex:1;min-width:0">
          <h2>${esc(job.file_name)}</h2>
          <div class="muted small">${esc(job.scope)} · ${esc(job.engine || engineLabel(state.settings))} · bắt đầu ${fmtDate(job.created)}</div>
        </div>
        ${statusChip(job.status)}
      </div>
      ${job.status === "waiting_agent" ? agentBox(job) : `
      <div class="bar ${pct === null ? "indeterminate" : ""}"><div style="width:${pct ?? 0}%"></div></div>
      <div class="row small"><span>${esc(job.phase)}</span><span class="muted" style="margin-left:auto">${pct !== null && (job.total || assembling) ? pct + "%" : ""}</span></div>
      <div class="stats">
        ${assembling
          ? `<div class="stat"><div class="k" title="Trang đã đặt chữ xong và mở xem được bên dưới (cập nhật theo lô 25 trang)">Trang xem được</div><div class="v">${job.pdf_pages_ready || 0}<span class="muted" style="font-size:13px"> / ${(job.page_list || []).length || "…"}</span></div></div>`
          : `<div class="stat"><div class="k">Đoạn đã dịch</div><div class="v">${job.done}<span class="muted" style="font-size:13px"> / ${job.total || "…"}</span></div></div>`}
        <div class="stat"><div class="k">Trang hiện tại</div><div class="v">${job.current_page || "—"}</div></div>
        <div class="stat"><div class="k">Cần kiểm tra</div><div class="v" style="color:${job.flagged ? "var(--amber)" : "inherit"}">${job.flagged}</div></div>
        <div class="stat"><div class="k">${active ? "Còn lại" : "Từ bộ nhớ đệm"}</div><div class="v" style="font-size:${active ? 16 : 19}px">${active ? fmtEta(job.eta) : job.cached}</div></div>
      </div>
      ${strip}
      ${active && job.last_text ? `<div class="live">${esc(job.last_text)}${job.last_text.length >= 280 ? "…" : ""}</div>` : ""}
      ${job.status === "error" ? `<div class="notice red" style="margin-top:16px">${icon("alert")}<div>${esc(job.error)}</div></div>` : ""}`}
    </div>
    <div class="summary-line">
      <div class="grow muted small">${job.output_path ? "Lưu tại: " + esc(job.output_path) : ""}</div>
      ${active ? `<button class="btn danger" id="cancel">${icon("stop")} Huỷ</button>` : ""}
      ${["error", "cancelled"].includes(job.status) ? `<button class="btn primary" id="resume">${icon("refresh")} Tiếp tục dịch</button>` : ""}
      ${job.status === "done" ? `
        <button class="btn" id="open-folder">${icon("folder")} Mở thư mục</button>
        ${job.outputs?.md ? `<a class="btn${job.outputs?.pdf ? "" : " primary"}" href="/api/jobs/${job.id}/download?fmt=md">${icon("download")} Tải Markdown</a>` : ""}
        ${job.outputs?.pdf ? `<a class="btn primary" href="/api/jobs/${job.id}/download?fmt=pdf">${icon("download")} Tải PDF</a>` : ""}` : ""}
    </div>`;
  $("#cancel")?.addEventListener("click", async () => { await api(`/api/jobs/${job.id}/cancel`, { body: {} }); toast("Đang huỷ…"); });
  $("#resume")?.addEventListener("click", async () => {
    try { const j = await api(`/api/jobs/${job.id}/resume`, { body: {} }); drawJob(j, true); startPolling(j.id); }
    catch (e) { toast(e.message, true); }
  });
  $("#open-folder")?.addEventListener("click", () => api(`/api/jobs/${job.id}/open-folder`, { body: {} }).catch((e) => toast(e.message, true)));
  $("#assemble")?.addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    try { const j = await api(`/api/jobs/${job.id}/assemble`, { body: {} }); drawJob(j, true); startPolling(j.id); refreshNavCount(); }
    catch (err) { toast(err.message, true); e.currentTarget.disabled = false; }
  });
  $$("[data-copy]").forEach((b) => (b.onclick = () => copyText(b.dataset.copy)));
  if (job.status === "done" && (full || lastStatus !== "done")) drawResult(job);
  else if (active) drawLive(job);
  else if (job.status !== "done") $("#job-result").innerHTML = "";
  lastStatus = job.status;
}

function agentBox(job) {
  const n = job.agent_parts || 1;
  const sep = job.job_dir.includes("\\") ? "\\" : "/";
  const prompts = Array.from({ length: n }, (_, k) =>
    `Đọc file ${job.job_dir}${sep}AGENT_TASK${n > 1 ? "_" + (k + 1) : ""}.md và làm theo hướng dẫn trong đó.`);
  return `
    <div class="agent-box">
      ${job.error ? `<div class="notice red" style="margin-bottom:12px">${icon("alert")}<div>${esc(job.error)}</div></div>` : ""}
      <b>Tài liệu đã được tách thành ${job.total} đoạn${n > 1 ? `, chia làm ${n} phần để ${n} agent dịch cùng lúc` : ""}.</b>
      <ol class="small" style="margin:8px 0 0;padding-left:18px;color:var(--ink-2)">
        <li>${n > 1 ? `Mở ${n} phiên Claude Code (hoặc agent khác), mỗi phiên gửi một câu lệnh:` : "Mở Claude Code (hoặc agent khác) và gửi câu lệnh sau:"}</li>
      </ol>
      ${prompts.map((pr, k) => `<div class="copyline">${n > 1 ? `<span class="chip teal">Phần ${k + 1}</span>` : ""}<span class="code">${esc(pr)}</span><button class="btn" data-copy="${esc(pr)}">${icon("copy")} Chép</button></div>`).join("")}
      <ol class="small" start="2" style="margin:10px 0 0;padding-left:18px;color:var(--ink-2)">
        <li>Khi ${n > 1 ? "các agent" : "agent"} dịch xong (tạo file <code>translations${n > 1 ? "_*" : ""}.jsonl</code>), bấm “Ghép bản dịch”. Ghép giữa chừng cũng được: đoạn chưa dịch sẽ giữ tiếng Anh.</li>
      </ol>
      <div class="row" style="margin-top:12px">
        <button class="btn primary" id="assemble">${icon("merge")} Ghép bản dịch</button>
        <button class="btn" id="open-folder">${icon("folder")} Mở thư mục job</button>
      </div>
    </div>`;
}

async function drawResult(job) {
  const box = $("#job-result");
  state.liveCtl = null;
  const reviewCount = job.review?.length || 0;
  const hasPdf = !!job.outputs?.pdf, hasMd = !!job.outputs?.md;
  if (!["compare", "preview", "review", "md"].includes(state.resultTab)
      || (state.resultTab === "compare" && !hasPdf) || (["preview", "md"].includes(state.resultTab) && !hasMd)) {
    state.resultTab = hasPdf ? "compare" : "preview";
  }
  state.comparePage = state.comparePage || 0;
  box.innerHTML = `
    <div class="panel" style="margin-top:20px">
      <div class="result-tabs" id="tabs">
        ${hasPdf ? `<button data-t="compare">So sánh trang</button>` : ""}
        ${hasMd ? `<button data-t="preview">Xem trước Markdown</button>` : ""}
        <button data-t="review">Cần kiểm tra ${reviewCount ? `<span class="chip amber">${reviewCount}</span>` : ""}</button>
        ${hasMd ? `<button data-t="md">Mã Markdown</button>` : ""}
        <span style="margin-left:auto"></span>
        ${hasMd ? `<button class="btn ghost" id="copy-md" style="margin-bottom:6px">${icon("copy")} Chép Markdown</button>` : ""}
      </div>
      <div id="tab-body"></div>
    </div>`;
  $$("#tabs [data-t]").forEach((b) => (b.onclick = () => { state.resultTab = b.dataset.t; showTab(job); }));
  $("#copy-md")?.addEventListener("click", async () => copyText(await api(`/api/jobs/${job.id}/markdown`)));
  showTab(job);
}

async function showTab(job) {
  $$("#tabs [data-t]").forEach((b) => b.classList.toggle("on", b.dataset.t === state.resultTab));
  const body = $("#tab-body");
  if (state.resultTab === "compare") {
    renderCompare(body, job, (job.page_list || []).length || 1);
  } else if (state.resultTab === "preview") {
    let html = await api(`/api/jobs/${job.id}/preview`);
    html = html.replace(/<!-- Trang (\d+) -->/g, '<div class="pgmark"><span class="pg">tr. $1</span></div>')
      .replace(/<!-- ⚠ Cần kiểm tra: (.*?) -->/g, (_, t) => `<div class="flag">⚠ ${t}</div>`);
    body.innerHTML = `<div class="preview-wrap"><article class="page-sheet">${html}</article></div>`;
  } else if (state.resultTab === "md") {
    body.innerHTML = `<pre class="md">${esc(await api(`/api/jobs/${job.id}/markdown`))}</pre>`;
  } else {
    const items = job.review || [];
    body.innerHTML = items.length
      ? `<div class="review-list">${items.map((r) => `
          <div class="review-item">
            <div class="muted small">Trang ${r.page}</div>
            <div class="src">${esc(r.src)}</div>
            <div class="dst">${esc(r.dst) || '<span class="muted">(chưa dịch)</span>'}</div>
            <div class="fl">${r.flags.map((f) => `<span class="chip amber">${esc(f)}</span>`).join("")}</div>
          </div>`).join("")}</div>`
      : `<div class="empty-state">${icon("check")}<div>Không có đoạn nào bị đánh dấu.</div></div>`;
  }
}

function renderCompare(body, job, total) {
  // Vẽ khung một lần; đổi trang thì chỉ đổi ảnh, thêm trang (khi job đang chạy) thì chỉ cập nhật thanh điều hướng.
  body.innerHTML = `
    <div class="compare-bar"></div>
    <div class="compare">
      <figure><figcaption>Bản gốc</figcaption><img class="cmp-src" alt=""></figure>
      <figure><figcaption>Bản dịch</figcaption><img class="cmp-dst" alt=""></figure>
    </div>`;
  const ctl = { total };
  const bar = () => {
    const i = state.comparePage;
    const page = (job.page_list || [])[i] ?? i + 1;
    $(".compare-bar", body).innerHTML = `
      <button class="btn" data-go="-1" ${i === 0 ? "disabled" : ""}>‹ Trang trước</button>
      <span>Trang <b>${page}</b> <span class="muted">(${i + 1}/${ctl.total})</span></span>
      <button class="btn" data-go="1" ${i >= ctl.total - 1 ? "disabled" : ""}>Trang sau ›</button>`;
    $$("[data-go]", body).forEach((b) => (b.onclick = () => { state.comparePage += +b.dataset.go; show(); }));
  };
  const show = () => {
    const i = state.comparePage = Math.max(0, Math.min(ctl.total - 1, state.comparePage || 0));
    const page = (job.page_list || [])[i] ?? i + 1;
    const src = $(".cmp-src", body), dst = $(".cmp-dst", body);
    src.alt = `Trang gốc ${page}`; dst.alt = `Trang dịch ${page}`;
    src.src = `/api/jobs/${job.id}/page?side=src&i=${i}`;
    dst.src = `/api/jobs/${job.id}/page?side=dst&i=${i}&v=${job.status}`;
    bar();
  };
  ctl.setTotal = (n) => { ctl.total = n; bar(); };
  show();
  return ctl;
}

/* Xem các trang đã dịch xong trong lúc job còn chạy */
function drawLive(job) {
  const box = $("#job-result");
  const ready = job.pdf_pages_ready || 0;
  if (!ready) { box.innerHTML = ""; state.liveCtl = null; return; }
  if (!$("#live-body") || !state.liveCtl) {
    box.innerHTML = `
      <div class="panel" style="margin-top:20px">
        <div class="panel-head"><h2>Trang đã dịch xong</h2>
          <span class="muted small" id="live-count"></span></div>
        <div id="live-body"></div>
      </div>`;
    state.liveCtl = renderCompare($("#live-body"), job, ready);
  } else if (state.liveCtl.total !== ready) {
    state.liveCtl.setTotal(ready);
  }
  $("#live-count").textContent = `${ready} trang — xem được ngay, không cần chờ dịch hết`;
}

function startPolling(jobId) {
  stopPolling();
  state.pollTimer = setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      drawJob(job);
      if (!ACTIVE.includes(job.status)) {
        stopPolling();
        refreshNavCount();
        if (job.status === "done") toast(job.phase || "Đã dịch xong");
        else if (job.status === "waiting_agent" && job.error) toast(job.error, true);
      }
    } catch { stopPolling(); }
  }, 1000);
}
function stopPolling() { clearInterval(state.pollTimer); state.pollTimer = null; }

/* ============================ Dịch nhanh ============================ */
function renderQuick() {
  const saved = store.get("quickText", "");
  const o = state.options || defaultOptions();
  $("#view").innerHTML = `
    <div class="quick">
      <div>
        <div class="pane-head"><span class="label">Tiếng Anh</span><span class="muted small" id="wc"></span></div>
        <textarea class="input" id="src" placeholder="Dán đoạn văn tiếng Anh vào đây. Dòng trống để ngăn cách các đoạn.">${esc(saved)}</textarea>
      </div>
      <div>
        <div class="pane-head"><span class="label">Tiếng Việt</span>
          <span class="row"><button class="btn ghost" id="copy-out" hidden>${icon("copy")} Chép</button></span></div>
        <div class="out empty" id="out">Bản dịch sẽ hiện ở đây.</div>
      </div>
    </div>
    <div class="row" style="margin-top:14px">
      <button class="btn primary lg" id="go">${icon("bolt")} Dịch</button>
      <button class="btn ghost" id="clear">Xoá</button>
      <label class="toggle" style="margin-left:12px;padding:0"><input type="checkbox" id="q-bi" ${o.bilingual_terms ? "checked" : ""}><span class="sw"></span><span><b>Ghi kèm thuật ngữ tiếng Anh</b></span></label>
      <span class="muted small" id="q-status" style="margin-left:auto"></span>
    </div>
    <div id="q-flags" style="margin-top:14px"></div>`;
  const src = $("#src");
  const wc = () => ($("#wc").textContent = src.value.trim() ? `${src.value.trim().split(/\s+/).length} từ` : "");
  src.addEventListener("input", () => { wc(); store.set("quickText", src.value); });
  wc();
  $("#clear").onclick = () => { src.value = ""; store.set("quickText", ""); wc(); $("#out").className = "out empty"; $("#out").textContent = "Bản dịch sẽ hiện ở đây."; $("#q-flags").innerHTML = ""; };
  $("#go").onclick = runQuick;
  src.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) runQuick(); });
  if (state.settings.engine === "agent")
    $("#q-status").innerHTML = `<span class="chip amber">Dịch nhanh cần model — chế độ agent không hỗ trợ</span>`;
}

async function runQuick() {
  const text = $("#src").value;
  if (!text.trim()) return;
  const btn = $("#go"), out = $("#out"), status = $("#q-status");
  btn.disabled = true;
  out.className = "out empty"; out.textContent = "Đang dịch…";
  $("#q-flags").innerHTML = "";
  try {
    const job = await api("/api/jobs/text", { body: { text, options: { bilingual_terms: $("#q-bi").checked, keep_source: false } } });
    const t0 = Date.now();
    for (;;) {
      await new Promise((r) => setTimeout(r, 600));
      const j = await api(`/api/jobs/${job.id}`);
      status.textContent = j.total ? `${j.done}/${j.total} đoạn` : "Đang xử lý…";
      if (j.status === "done") {
        out.className = "out"; out.textContent = (j.result || "").trim();
        $("#copy-out").hidden = false;
        $("#copy-out").onclick = () => copyText(out.textContent);
        status.textContent = `Xong trong ${((Date.now() - t0) / 1000).toFixed(1)} giây`;
        const flagged = (j.review || []);
        if (flagged.length) $("#q-flags").innerHTML = `<div class="notice amber">${icon("alert")}<div>${flagged.map((r) => r.flags.map(esc).join("; ")).join("<br>")}</div></div>`;
        break;
      }
      if (["error", "cancelled", "waiting_agent"].includes(j.status)) {
        out.className = "out empty";
        out.textContent = j.status === "waiting_agent" ? "Chế độ agent không dùng được cho dịch nhanh. Hãy chọn model trong Cài đặt." : (j.error || "Có lỗi");
        status.textContent = "";
        break;
      }
    }
  } catch (e) { toast(e.message, true); out.textContent = ""; }
  btn.disabled = false;
}

/* ============================ Lịch sử ============================ */
async function renderHistory() {
  $("#top-actions").innerHTML = `<button class="btn" id="open-out">${icon("folder")} Mở thư mục kết quả</button>`;
  $("#open-out").onclick = () => api("/api/open-output-folder", { body: {} }).catch((e) => toast(e.message, true));
  const { jobs } = await api("/api/jobs");
  if (!jobs.length) {
    $("#view").innerHTML = `<div class="panel"><div class="empty-state">Chưa có tài liệu nào được dịch.<br><br><a class="btn primary" href="#/tai-lieu">${icon("file")} Dịch tài liệu đầu tiên</a></div></div>`;
    return;
  }
  $("#view").innerHTML = `
    <div class="panel"><div class="table-scroll"><table class="data">
      <thead><tr><th>Tài liệu</th><th>Phạm vi</th><th>Trạng thái</th><th>Cần kiểm tra</th><th>Thời gian</th></tr></thead>
      <tbody>${jobs.map((j) => `
        <tr class="click" data-id="${j.id}">
          <td><b>${esc(j.file_name)}</b><div class="muted small">${esc(j.engine || "")}</div></td>
          <td>${esc(j.scope)}</td>
          <td>${statusChip(j.status)}${ACTIVE.includes(j.status) && jobPct(j) !== null ? ` <span class="muted small">${jobPct(j)}%</span>` : ""}</td>
          <td>${j.status === "done" ? (j.flagged ? `<span class="chip amber">${j.flagged} đoạn</span>` : `<span class="chip green">0</span>`) : ""}</td>
          <td class="muted small">${fmtDate(j.created)}</td>
        </tr>`).join("")}</tbody>
    </table></div></div>`;
  $$("tr[data-id]").forEach((tr) => (tr.onclick = () => { state.jobId = tr.dataset.id; store.set("jobId", state.jobId); location.hash = "#/tai-lieu"; }));
}

async function refreshNavCount() {
  try {
    const { jobs } = await api("/api/jobs");
    const n = jobs.filter((j) => ACTIVE.includes(j.status)).length;
    const el = $("#nav-running");
    el.hidden = !n; el.textContent = n;
  } catch { /* */ }
}

/* ============================ Thuật ngữ ============================ */
async function renderTerms() {
  $("#top-actions").innerHTML = `<button class="btn primary" id="add-term">${icon("plus")} Thêm thuật ngữ</button>`;
  $("#view").innerHTML = `
    <div class="panel" id="term-form" hidden style="margin-bottom:16px"></div>
    <div class="panel">
      <div class="panel-head">
        <input class="input" id="q" placeholder="Tìm tiếng Anh, tiếng Việt hoặc viết tắt…" style="max-width:380px">
        <span class="muted small" id="term-count" style="margin-left:auto"></span>
      </div>
      <div class="table-scroll" id="term-table"></div>
    </div>`;
  $("#add-term").onclick = () => termForm({});
  let timer;
  $("#q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(loadTerms, 180); });
  loadTerms();
}

let termCache = [];
async function loadTerms() {
  const q = $("#q")?.value || "";
  const data = await api("/api/terms?q=" + encodeURIComponent(q));
  termCache = data.terms;
  $("#term-count").textContent = `${data.terms.length} / ${data.total} thuật ngữ`;
  $("#term-table").innerHTML = data.terms.length ? `
    <table class="data"><thead><tr><th>Tiếng Anh</th><th>Tiếng Việt</th><th>Viết tắt</th><th>Lĩnh vực</th><th>Trạng thái</th><th>Ghi chú</th></tr></thead>
    <tbody>${data.terms.map((t, i) => `
      <tr class="click" data-i="${i}">
        <td><b>${esc(t.en)}</b>${t.variants ? `<div class="muted small">${esc(t.variants.replaceAll("|", ", "))}</div>` : ""}</td>
        <td>${esc(t.vi)}</td>
        <td>${esc(t.abbr)}</td>
        <td class="muted">${esc(t.domain)}</td>
        <td>${t.status === "approved" ? `<span class="chip green">Đã duyệt</span>` : `<span class="chip">Nháp</span>`}${t.ambiguous ? ` <span class="chip amber">Đa nghĩa</span>` : ""}${t.source === "user" ? ` <span class="chip teal">Của bạn</span>` : ""}</td>
        <td class="muted small">${esc(t.note)}</td>
      </tr>`).join("")}</tbody></table>`
    : `<div class="empty-state">Không tìm thấy thuật ngữ.</div>`;
  $$("#term-table tr[data-i]").forEach((tr) => (tr.onclick = () => termForm(termCache[+tr.dataset.i])));
}

function termForm(t) {
  const box = $("#term-form");
  box.hidden = false;
  box.innerHTML = `
    <div class="panel-head"><h2>${t.en ? "Sửa thuật ngữ" : "Thêm thuật ngữ"}</h2>
      <button class="btn ghost" id="tf-close" style="margin-left:auto">${icon("x")}</button></div>
    <div class="panel-body">
      <div class="row" style="align-items:flex-start">
        <div class="field grow"><label>Tiếng Anh *</label><input class="input" id="tf-en" value="${esc(t.en || "")}"></div>
        <div class="field grow"><label>Tiếng Việt *</label><input class="input" id="tf-vi" value="${esc(t.vi || "")}"></div>
        <div class="field" style="width:130px"><label>Viết tắt</label><input class="input" id="tf-abbr" value="${esc(t.abbr || "")}"></div>
      </div>
      <div class="row" style="align-items:flex-start">
        <div class="field grow"><label>Biến thể</label><input class="input" id="tf-var" placeholder="ví dụ: oedema|edemas" value="${esc(t.variants || "")}"><span class="hint">Cách viết khác, ngăn bằng dấu |. Số nhiều thông dụng được tự nhận.</span></div>
        <div class="field" style="width:180px"><label>Lĩnh vực</label><input class="input" id="tf-dom" value="${esc(t.domain || "")}"></div>
        <div class="field grow"><label>Ghi chú</label><input class="input" id="tf-note" value="${esc(t.note || "")}"></div>
      </div>
      <div class="row">
        ${toggle("tf-approved", "Đã duyệt", "Thuật ngữ đã duyệt được kiểm bắt buộc khi dịch", t.status === "approved")}
        ${toggle("tf-amb", "Đa nghĩa", "Chỉ gợi ý cho model, không bắt buộc", t.ambiguous)}
        <button class="btn primary" id="tf-save" style="margin-left:auto">${icon("check")} Lưu</button>
      </div>
    </div>`;
  $("#tf-close").onclick = () => (box.hidden = true);
  $("#tf-save").onclick = async () => {
    try {
      await api("/api/terms", { body: {
        en: $("#tf-en").value, vi: $("#tf-vi").value, abbr: $("#tf-abbr").value, variants: $("#tf-var").value,
        domain: $("#tf-dom").value, note: $("#tf-note").value,
        status: $('[name="tf-approved"]').checked ? "approved" : "draft", ambiguous: $('[name="tf-amb"]').checked,
      } });
      toast("Đã lưu thuật ngữ");
      box.hidden = true;
      loadTerms();
    } catch (e) { toast(e.message, true); }
  };
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ============================ Cài đặt ============================ */
function renderSettings() {
  const s = { ...state.settings };
  const presets = state.presets;
  $("#view").innerHTML = `
    <div class="settings">
      <div>
        <div class="panel">
          <div class="panel-head"><h2>Nguồn dịch</h2></div>
          <div class="panel-body">
            <div class="presets" id="presets">${Object.entries(presets).map(([k, p]) => `
              <button class="preset" data-k="${k}"><b>${esc(p.label.split(" (")[0])}</b><small>${esc((p.label.match(/\((.*)\)/) || [, ""])[1] || (p.needs_key ? "Cần API key" : ""))}</small></button>`).join("")}</div>
            <div id="engine-fields"></div>
          </div>
        </div>
        <div class="panel" style="margin-top:20px">
          <div class="panel-head"><h2>Mặc định khi dịch</h2></div>
          <div class="panel-body" style="padding-top:6px;padding-bottom:6px">
            ${toggle("bilingual_terms", "Ghi kèm thuật ngữ tiếng Anh", "Lần đầu gặp trong mỗi mục: “xương hàm dưới (mandible)”", s.bilingual_terms)}
            ${toggle("keep_source", "Kèm bản gốc dưới mỗi đoạn", "Tiện đối chiếu khi học", s.keep_source)}
            ${toggle("detect_tables", "Nhận diện bảng", "Dịch từng ô bảng, giữ nguyên khung bảng", s.detect_tables)}
            ${toggle("output_pdf", "Xuất PDF giữ bố cục, có hình", "Chữ Việt đặt vào đúng chỗ chữ gốc; hình giữ nguyên", s.output_pdf)}
            ${toggle("pdf_interleave", "Xen kẽ trang gốc trong PDF", "Mỗi trang dịch đi kèm trang gốc", s.pdf_interleave)}
            ${toggle("output_md", "Xuất Markdown kèm hình", "Hình được cắt ra thư mục riêng cạnh file .md", s.output_md)}
          </div>
          <div class="panel-body" style="border-top:1px solid var(--line)">
            <div class="field"><label>Thư mục lưu file kết quả</label>
              <div class="row"><input class="input grow" id="output_dir" value="${esc(s.output_dir)}"><button class="btn" id="open-out">${icon("folder")} Mở</button></div></div>
            <div class="row"><span class="muted small grow">Bộ nhớ đệm giúp dịch lại/tiếp tục mà không tốn thời gian.</span><button class="btn danger" id="clear-cache">Xoá bộ nhớ đệm</button></div>
          </div>
        </div>
        <div class="row" style="margin-top:18px"><button class="btn primary lg" id="save">${icon("check")} Lưu cài đặt</button></div>
      </div>
      <div class="panel guide"><div class="panel-body" id="guide"></div></div>
    </div>`;

  const draw = () => {
    $$("#presets .preset").forEach((b) => b.classList.toggle("on", b.dataset.k === s.preset));
    const p = presets[s.preset] || {};
    const f = $("#engine-fields");
    if (s.engine === "agent") {
      f.innerHTML = `<div class="notice">${icon("bolt")}<div>App sẽ tách tài liệu thành các đoạn và tạo hướng dẫn cho agent (Claude Code…). Agent dịch xong, bạn bấm “Ghép bản dịch” để xuất PDF và Markdown. Không cần cài model.</div></div>
        <div class="field" style="margin-top:16px;max-width:320px"><label>Số agent dịch song song</label>
          <input class="input" type="number" min="1" max="8" id="agent_parts" value="${s.agent_parts || 1}">
          <span class="hint">Chia tài liệu thành nhiều phần liền mạch, mỗi agent dịch một phần cùng lúc. Ngữ cảnh và thuật ngữ vẫn được giữ.</span></div>`;
      $("#agent_parts").addEventListener("input", (e) => (s.agent_parts = e.target.value));
    } else {
      f.innerHTML = `
        <div class="field"><label>Địa chỉ API</label><input class="input" id="base_url" value="${esc(s.base_url)}" placeholder="${esc(p.base_url || "https://…/v1")}"></div>
        ${p.needs_key || s.preset === "openai" ? `<div class="field"><label>API key</label><input class="input" type="password" id="api_key" placeholder="${state.settings.api_key_set ? "Đã lưu — để trống nếu không đổi" : "Dán API key"}"></div>` : ""}
        <div class="field"><label>Model</label>
          <div class="row"><input class="input grow" id="model" list="model-list" value="${esc(s.model)}" placeholder="ví dụ: qwen3:8b">
          <datalist id="model-list"></datalist>
          <button class="btn" id="load-models">${icon("refresh")} Lấy danh sách</button></div>
          <span class="hint" id="models-hint"></span></div>
        <div class="row" style="align-items:flex-start">
          <div class="field grow"><label>Số luồng song song</label><input class="input" type="number" min="1" max="8" id="workers" value="${s.workers}"><span class="hint">Ollama: bằng OLLAMA_NUM_PARALLEL (mặc định 1, xem hướng dẫn bên phải). API trên mạng: 2–4.</span></div>
          <div class="field grow"><label>Temperature</label><input class="input" type="number" step="0.1" min="0" max="1.5" id="temperature" value="${s.temperature}"><span class="hint">Thấp = bám sát bản gốc hơn. Nên 0.1–0.3.</span></div>
        </div>
        <button class="btn" id="test">${icon("bolt")} Kiểm tra kết nối</button>
        <div class="test-result" id="test-result"></div>`;
      ["base_url", "model", "workers", "temperature"].forEach((id) => $("#" + id).addEventListener("input", (e) => (s[id] = e.target.value)));
      $("#load-models").onclick = loadModels;
      $("#test").onclick = testEngine;
    }
    $("#guide").innerHTML = guideFor(s.preset);
  };

  const collect = () => {
    const v = { ...s };
    const key = $("#api_key");
    v.api_key = key ? key.value : "";
    ["bilingual_terms", "keep_source", "detect_tables", "output_pdf", "output_md", "pdf_interleave"].forEach((n) => (v[n] = $(`[name="${n}"]`).checked));
    v.output_dir = $("#output_dir").value;
    return v;
  };

  async function loadModels() {
    const hint = $("#models-hint");
    hint.textContent = "Đang lấy danh sách…";
    try {
      const { models } = await api("/api/engine/models", { body: { values: collect() } });
      $("#model-list").innerHTML = models.map((m) => `<option value="${esc(m)}">`).join("");
      hint.textContent = models.length ? `Có ${models.length} model: ${models.slice(0, 8).join(", ")}${models.length > 8 ? "…" : ""}` : "Chưa có model nào. Xem hướng dẫn bên phải.";
    } catch (e) { hint.textContent = e.message; }
  }

  async function testEngine() {
    const box = $("#test-result");
    box.innerHTML = `<div class="notice">${icon("refresh")}<div>Đang dịch thử một câu…</div></div>`;
    const r = await api("/api/engine/test", { body: { values: collect() } });
    box.innerHTML = r.ok
      ? `<div class="notice">${icon("check")}<div><b>${esc(r.message)}</b><div class="small">${esc(r.source)}</div><div class="sample">${esc(r.result || "")}</div></div></div>`
      : `<div class="notice red">${icon("alert")}<div>${esc(r.message)}</div></div>`;
  }

  $$("#presets .preset").forEach((b) => (b.onclick = () => {
    const p = presets[b.dataset.k];
    s.preset = b.dataset.k; s.engine = p.engine;
    if (p.base_url || b.dataset.k === "openai") s.base_url = p.base_url || s.base_url;
    if (p.engine === "ollama") s.workers = 1;
    draw();
  }));
  $("#open-out").onclick = () => api("/api/open-output-folder", { body: {} }).catch((e) => toast(e.message, true));
  $("#clear-cache").onclick = async () => { const r = await api("/api/cache/clear", { body: {} }); toast(`Đã xoá ${r.removed} bản dịch trong bộ nhớ đệm`); };
  $("#save").onclick = async () => {
    try {
      const { settings } = await api("/api/settings", { method: "PUT", body: { values: collect() } });
      state.settings = settings;
      state.options = null;
      toast("Đã lưu cài đặt");
      refreshEngineStatus();
    } catch (e) { toast(e.message, true); }
  };
  draw();
}

function guideFor(preset) {
  const guides = {
    ollama: `
      <h3>Dùng Ollama (miễn phí, chạy trên máy)</h3>
      <ol>
        <li>Tải và cài Ollama tại <b>ollama.com</b>.</li>
        <li>Mở Terminal (macOS) hoặc Command Prompt (Windows), tải một model, ví dụ:<br><span class="code">ollama pull qwen3:8b</span></li>
        <li>Bấm “Lấy danh sách”, chọn model rồi “Kiểm tra kết nối”.</li>
      </ol>
      <h3>Chọn model theo máy</h3>
      <ul>
        <li>GPU 6–8 GB: model 4–8B (qwen3:8b, gemma3:4b…)</li>
        <li>GPU 12–16 GB hoặc Mac 16 GB: model 12–14B (gemma3:12b, qwen3:14b…)</li>
        <li>GPU 24 GB hoặc Mac 32 GB+: model 27–32B</li>
      </ul>
      <p class="small muted">Model càng lớn dịch càng tốt nhưng càng chậm. Nên thử vài model trên cùng một đoạn trong “Dịch nhanh” để chọn.</p>
      <h3>Dịch nhanh hơn: cho Ollama xử lý song song</h3>
      <p class="small">Nếu GPU còn dư bộ nhớ, đặt biến môi trường <span class="code">OLLAMA_NUM_PARALLEL=2</span> (hoặc 3–4) rồi khởi động lại Ollama, sau đó tăng “Số luồng song song” ở đây lên cùng số đó. Hết bộ nhớ GPU thì model chạy chậm hẳn — khi đó giảm lại.</p>
      <ul class="small"><li>Windows: Settings → System → About → Advanced system settings → Environment Variables → New.</li>
      <li>macOS: <span class="code">launchctl setenv OLLAMA_NUM_PARALLEL 2</span> rồi mở lại Ollama.</li></ul>`,
    lmstudio: `
      <h3>Dùng LM Studio</h3>
      <ol>
        <li>Cài LM Studio, tải một model trong tab Discover.</li>
        <li>Vào tab Developer, bật <b>Start Server</b> (mặc định cổng 1234).</li>
        <li>Bấm “Lấy danh sách” để chọn model đang nạp.</li>
      </ol>`,
    gemini: `
      <h3>Dùng Google Gemini API</h3>
      <ol>
        <li>Lấy API key tại <b>aistudio.google.com</b>.</li>
        <li>Dán key, bấm “Lấy danh sách” rồi chọn một model Flash.</li>
      </ol>
      <div class="notice amber" style="margin-top:12px">${icon("alert")}<div class="small">Gói miễn phí có giới hạn số lượt gọi và Google có thể dùng nội dung gửi lên để cải thiện sản phẩm.</div></div>`,
    openai: `
      <h3>API tương thích OpenAI</h3>
      <p class="small">Dùng cho mọi dịch vụ có endpoint <span class="code">/chat/completions</span>. Nhập địa chỉ gốc (thường kết thúc bằng <span class="code">/v1</span>), API key và tên model.</p>`,
    agent: `
      <h3>Chế độ agent</h3>
      <ol>
        <li>Chọn tài liệu và bấm “Bắt đầu dịch”: app tách đoạn, tạo file <span class="code">AGENT_TASK.md</span>.</li>
        <li>Chép câu lệnh app đưa ra, gửi cho Claude Code (hoặc agent khác) của bạn.</li>
        <li>Agent dịch xong thì bấm “Ghép bản dịch”.</li>
      </ol>
      <p class="small muted">Chất lượng tốt nhất; dùng hạn mức gói agent của chính bạn.</p>`,
  };
  return guides[preset] || "";
}

/* ============================ Khởi động ============================ */
(async function init() {
  try {
    const [{ settings, presets }, health] = await Promise.all([api("/api/settings"), api("/api/health")]);
    state.settings = settings;
    state.presets = presets;
    $("#version").textContent = "Phiên bản " + health.version;
  } catch (e) {
    $("#view").innerHTML = `<div class="notice red">${icon("alert")}<div>Không kết nối được tới app: ${esc(e.message)}</div></div>`;
    return;
  }
  route();
  refreshEngineStatus();
  refreshNavCount();
})();
