const SESSIONS_STORAGE = "spk_sessions";

const messagesEl = document.getElementById("messages");
const chatScroll = document.getElementById("chatScroll");
const welcomeEl = document.getElementById("welcome");
const uploadListEl = document.getElementById("uploadList");
const fileInput = document.getElementById("fileInput");
const fileInputDocs = document.getElementById("fileInputDocs");
const chatForm = document.getElementById("chatForm");
const questionEl = document.getElementById("question");
const sendBtn = document.getElementById("sendBtn");
const noticeBar = document.getElementById("noticeBar");
const activeJobsEl = document.getElementById("activeJobs");
const uploadStatusEl = document.getElementById("uploadStatus");
const limitsListEl = document.getElementById("limitsList");
const configStatus = document.getElementById("configStatus");
const versionBadge = document.getElementById("versionBadge");
const sessionListEl = document.getElementById("sessionList");
const sessionSearch = document.getElementById("sessionSearch");
const newChatBtn = document.getElementById("newChatBtn");
const sidebar = document.getElementById("sidebar");
const sidebarToggle = document.getElementById("sidebarToggle");
const sidebarShow = document.getElementById("sidebarShow");
const promptGrid = document.getElementById("promptGrid");
const aboutBtn = document.getElementById("aboutBtn");
const helpBtn = document.getElementById("helpBtn");
const viewChat = document.getElementById("view-chat");
const dropOverlay = document.getElementById("dropOverlay");
const aboutModal = document.getElementById("aboutModal");
const helpModal = document.getElementById("helpModal");
const loginScreen = document.getElementById("loginScreen");
const loginForm = document.getElementById("loginForm");
const loginEmail = document.getElementById("loginEmail");
const loginSubmit = document.getElementById("loginSubmit");
const loginError = document.getElementById("loginError");

let userRole = "admin";
let isQuerying = false;
let activeUploads = 0;

/* ---------- Fetch helper ---------- */

async function apiFetch(url, options = {}) {
  return fetch(url, { credentials: "same-origin", ...options });
}

/* ---------- Session history (stored in this browser) ---------- */

let sessions = loadSessions();
let currentSessionId = null;

function loadSessions() {
  try {
    return JSON.parse(localStorage.getItem(SESSIONS_STORAGE)) || [];
  } catch {
    return [];
  }
}

function saveSessions() {
  // Keep the most recent 50 sessions to stay within localStorage limits.
  sessions.sort((a, b) => b.updated - a.updated);
  sessions = sessions.slice(0, 50);
  try {
    localStorage.setItem(SESSIONS_STORAGE, JSON.stringify(sessions));
  } catch {
    /* storage full — drop oldest and retry once */
    sessions = sessions.slice(0, 10);
    try { localStorage.setItem(SESSIONS_STORAGE, JSON.stringify(sessions)); } catch {}
  }
}

function currentSession() {
  return sessions.find((s) => s.id === currentSessionId) || null;
}

function ensureSession() {
  let s = currentSession();
  if (s) return s;
  s = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
    title: "New conversation",
    created: Date.now(),
    updated: Date.now(),
    messages: [],
    documents: [],
  };
  sessions.unshift(s);
  currentSessionId = s.id;
  syncPublications();
  return s;
}

function trackSessionDocument(filename) {
  const s = ensureSession();
  if (!s.documents) s.documents = [];
  if (!s.documents.includes(filename)) {
    s.documents.push(filename);
    s.updated = Date.now();
    saveSessions();
    renderSessionList();
  }
}

function sessionFocusSources() {
  const s = currentSession();
  return s?.documents?.length ? [...s.documents] : null;
}

function sessionHistory() {
  const s = currentSession();
  if (!s?.messages?.length) return null;
  return s.messages
    .filter((m) => m.role === "user" || m.role === "assistant")
    .slice(-8)
    .map((m) => ({ role: m.role, content: m.text }));
}

function newConversation() {
  currentSessionId = null;
  messagesEl.innerHTML = "";
  welcomeEl.hidden = false;
  hideNotice();
  clearUploadChips();
  renderSessionList();
  syncPublications();
}

function openSession(id) {
  const s = sessions.find((x) => x.id === id);
  if (!s) return;
  currentSessionId = id;
  messagesEl.innerHTML = "";
  clearUploadChips();
  welcomeEl.hidden = s.messages.length > 0;
  s.messages.forEach((m) => renderMessage(m.role, m.text, m.sources || [], m.citations || []));
  showView("chat");
  renderSessionList();
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

function deleteSession(id) {
  const s = sessions.find((x) => x.id === id);
  if (!s) return;
  if (!confirm(`Delete "${s.title}"? This cannot be undone.`)) return;
  sessions = sessions.filter((x) => x.id !== id);
  saveSessions();
  if (currentSessionId === id) {
    newConversation();
  } else {
    renderSessionList();
  }
}

function sessionGroupLabel(updated) {
  const days = (Date.now() - updated) / 86400000;
  if (days <= 7) return "Recent";
  if (days <= 30) return "Past 30 Days";
  return "Older";
}

function formatSessionTime(ts) {
  const d = new Date(ts);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  if (sameDay) {
    return "Today at " + d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function renderSessionList() {
  const filter = (sessionSearch.value || "").trim().toLowerCase();
  sessionListEl.innerHTML = "";
  const ordered = [...sessions].sort((a, b) => b.updated - a.updated);
  let lastGroup = null;

  ordered.forEach((s) => {
    if (filter && !s.title.toLowerCase().includes(filter)) return;
    const group = sessionGroupLabel(s.updated);
    if (group !== lastGroup) {
      const g = document.createElement("div");
      g.className = "session-group";
      g.textContent = group;
      sessionListEl.appendChild(g);
      lastGroup = group;
    }
    const row = document.createElement("div");
    row.className = "session-row" + (s.id === currentSessionId ? " active" : "");

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "session-item";
    btn.innerHTML = `<span class="dot"></span><span class="label"></span>`;
    btn.querySelector(".label").textContent = s.title;
    btn.title = `${s.title}\n${formatSessionTime(s.updated)}`;
    btn.addEventListener("click", () => openSession(s.id));

    row.appendChild(btn);
    if (s.documents?.length) {
      const docs = document.createElement("div");
      docs.className = "session-docs";
      docs.textContent = `${s.documents.length} file(s): ${s.documents.slice(0, 2).join(", ")}${s.documents.length > 2 ? "…" : ""}`;
      row.appendChild(docs);
    }

    const del = document.createElement("button");
    del.type = "button";
    del.className = "session-delete";
    del.title = "Delete conversation";
    del.setAttribute("aria-label", `Delete ${s.title}`);
    del.innerHTML = "&times;";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s.id);
    });

    row.appendChild(del);
    sessionListEl.appendChild(row);
  });

  if (!sessionListEl.children.length) {
    const empty = document.createElement("div");
    empty.className = "session-group";
    empty.textContent = filter ? "No matching conversations" : "No conversations yet";
    sessionListEl.appendChild(empty);
  }
}

/* ---------- Views (tabs) ---------- */

function showView(name) {
  document.querySelectorAll(".tab[data-view]").forEach((t) => {
    t.classList.toggle("active", t.dataset.view === name);
  });
  document.getElementById("view-chat").hidden = name !== "chat";
  document.getElementById("view-library").hidden = name !== "library";
  document.getElementById("view-uploads").hidden = name !== "uploads";
  if (name === "uploads") refreshUploads();
  if (name === "library") refreshLibraryLinks();
}

document.querySelectorAll(".tab[data-view]").forEach((t) => {
  t.addEventListener("click", () => showView(t.dataset.view));
});

/* ---------- Messages ---------- */

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inlineMd(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|\s)\*([^*\n]+)\*(?=\s|[.,;:!?)]|$)/g, "$1<em>$2</em>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function renderMarkdown(text) {
  const lines = escapeHtml(text).split("\n");
  const out = [];
  let list = null; // "ul" | "ol" | null

  const closeList = () => {
    if (list) {
      out.push(`</${list}>`);
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    const ol = line.match(/^\s*(\d+)[.)]\s+(.*)$/);

    if (h) {
      closeList();
      const level = Math.min(h[1].length + 2, 5);
      out.push(`<h${level}>${inlineMd(h[2])}</h${level}>`);
    } else if (ul) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inlineMd(ul[1])}</li>`);
    } else if (ol) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      // Keep the model's own numbering even when paragraphs split the list.
      out.push(`<li value="${ol[1]}">${inlineMd(ol[2])}</li>`);
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList();
      out.push(`<p>${inlineMd(line)}</p>`);
    }
  }
  closeList();
  return out.join("");
}

function renderMessage(role, text, sources = [], citations = []) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (role === "file") {
    // text holds the filename; render an attachment chip in the thread.
    div.innerHTML =
      '<span class="file-chip">' +
      '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">' +
      '<path fill="currentColor" d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm0 1.5L18.5 8H14V3.5z"/></svg>' +
      `<span class="file-chip-name">${escapeHtml(text)}</span></span>`;
    messagesEl.appendChild(div);
    chatScroll.scrollTop = chatScroll.scrollHeight;
    return;
  }
  if (role === "assistant") {
    const md = document.createElement("div");
    md.className = "md";
    md.innerHTML = renderMarkdown(text);
    div.appendChild(md);
  } else {
    const p = document.createElement("p");
    p.textContent = text;
    div.appendChild(p);
  }
  if (citations?.length) {
    const c = document.createElement("div");
    c.className = "citation-list";
    c.innerHTML =
      "Citations: " +
      citations
        .slice(0, 12)
        .map(
          (item) =>
            `<a href="${escapeHtml(item.url)}"${documentLinkAttrs(item.url)}>${escapeHtml(item.label)}</a>`
        )
        .join(" · ");
    div.appendChild(c);
  } else if (sources.length) {
    const s = document.createElement("div");
    s.className = "sources";
    s.textContent = "Sources: " + sources.join(", ");
    div.appendChild(s);
  }
  messagesEl.appendChild(div);
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

function addMessage(role, text, sources = [], citations = []) {
  welcomeEl.hidden = true;
  renderMessage(role, text, sources, citations);

  const s = ensureSession();
  s.messages.push({ role, text, sources, citations });
  if (role === "user" && s.title === "New conversation") {
    s.title = text.length > 60 ? text.slice(0, 57) + "..." : text;
  }
  s.updated = Date.now();
  saveSessions();
  renderSessionList();
}

function addFileMessage(filename) {
  welcomeEl.hidden = true;
  renderMessage("file", filename);
  const s = ensureSession();
  // Avoid duplicate file bubbles if the same file settles twice.
  const already = s.messages.some((m) => m.role === "file" && m.text === filename);
  if (!already) {
    s.messages.push({ role: "file", text: filename });
    s.updated = Date.now();
    saveSessions();
    renderSessionList();
  }
}

function showNotice(text, type = "info") {
  noticeBar.hidden = false;
  noticeBar.className = `notice-bar ${type}`;
  noticeBar.textContent = text;
}

function hideNotice() {
  noticeBar.hidden = true;
  noticeBar.textContent = "";
}

function showWarnings(warnings, context = "Upload") {
  if (!warnings?.length) return;
  const text = warnings.join(" ");
  addMessage("notice", `${context}: ${text}`);
}

function refreshSendButton() {
  // Send + file pickers are disabled while a query is in flight or any file
  // is still uploading/indexing. Driven only by isQuerying + activeUploads so
  // the button reliably re-enables the moment uploads finish.
  const busy = isQuerying || activeUploads > 0;
  sendBtn.disabled = busy;
  if (fileInput) fileInput.disabled = busy;
  if (fileInputDocs) fileInputDocs.disabled = busy;
}

function setLoading(on) {
  isQuerying = on;
  refreshSendButton();
}

/* ---------- In-composer upload status chips ---------- */

function clearUploadChips() {
  uploadStatusEl.innerHTML = "";
  uploadStatusEl.hidden = true;
}

function addUploadChip(filename) {
  uploadStatusEl.hidden = false;
  const chip = document.createElement("div");
  chip.className = "upload-chip is-indeterminate";
  chip.innerHTML = `
    <div class="upload-chip-head">
      <span class="upload-chip-name">${escapeHtml(filename)}</span>
      <span class="upload-chip-check" aria-label="Upload complete" title="Upload complete">&#10003;</span>
    </div>
    <div class="upload-chip-bar"><div class="upload-chip-fill"></div></div>
    <div class="upload-chip-status">Preparing…</div>
  `;
  uploadStatusEl.appendChild(chip);
  return chip;
}

function setUploadProgress(chip, pct, label) {
  if (!chip) return;
  const fill = chip.querySelector(".upload-chip-fill");
  const status = chip.querySelector(".upload-chip-status");
  if (typeof pct === "number" && pct >= 0) {
    chip.classList.remove("is-indeterminate");
    fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  } else {
    // Unknown progress — show an animated indeterminate bar.
    chip.classList.add("is-indeterminate");
  }
  if (label) status.textContent = label;
}

function markUploadDone(chip, label = "Ready") {
  if (!chip) return;
  chip.classList.remove("is-indeterminate", "is-error");
  chip.classList.add("is-done");
  chip.querySelector(".upload-chip-fill").style.width = "100%";
  chip.querySelector(".upload-chip-status").textContent = label;
}

function markUploadError(chip, message) {
  if (!chip) return;
  chip.classList.remove("is-indeterminate", "is-done");
  chip.classList.add("is-error");
  chip.querySelector(".upload-chip-fill").style.width = "100%";
  chip.querySelector(".upload-chip-status").textContent = message || "Upload failed.";
}

/* ---------- Server status / limits ---------- */

function updateConfigStatus(health) {
  const issues = [];
  if (!health.llm_configured) issues.push("OpenAI API key missing on server");
  if (!health.embeddings_configured) issues.push("OpenAI embedding key missing on server");

  if (issues.length) {
    configStatus.hidden = false;
    configStatus.className = "config-status warn";
    configStatus.textContent =
      "Server not fully configured — uploads and chat will fail until API keys are set. " +
      issues.join(". ");
    return;
  }
  configStatus.hidden = true;
}

async function loadLimits() {
  try {
    const res = await fetch("/health");
    const data = await res.json();
    if (!res.ok) return;

    authRequired = data.auth_required;
    updateConfigStatus(data);
    if (data.version) versionBadge.textContent = `BETA v${data.version}`;

    const L = data.context_limits;
    const ctxK = Math.round(L.max_context_chars / 1000);
    limitsListEl.innerHTML = `
      <li>Max upload: <strong>${L.max_upload_mb} MB</strong></li>
      <li>Max PDF pages indexed: <strong>${L.max_pdf_pages.toLocaleString()}</strong></li>
      <li>Background indexing: PDFs with <strong>${L.pdf_background_page_threshold}+</strong> pages</li>
      <li>Max chunks per file: <strong>${L.max_chunks_per_file.toLocaleString()}</strong></li>
      <li>Context per answer: ~<strong>${ctxK}k</strong> characters from retrieved sections</li>
      <li>Max sections per file in one answer: <strong>${L.max_chunks_per_source}</strong></li>
    `;
  } catch {
    limitsListEl.innerHTML = "<li>Could not load limits (is the server running?)</li>";
  }
}

/* ---------- Files / indexing ---------- */

function isLocalDownloadUrl(url) {
  return typeof url === "string" && url.startsWith("/download/");
}

function documentLinkAttrs(url) {
  if (isLocalDownloadUrl(url)) {
    return ' download';
  }
  return ' target="_blank" rel="noopener"';
}

function formatDocDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return "—";
  }
}

function renderDocGrid(container, docs, emptyMessage, options = {}) {
  const { deletable = false } = options;
  container.innerHTML = "";
  if (!docs.length) {
    container.innerHTML = `<div class="file-grid-empty">${emptyMessage}</div>`;
    return;
  }

  container.classList.toggle("file-grid-deletable", deletable);
  const header = document.createElement("div");
  header.className = "file-grid-header";
  header.innerHTML = deletable
    ? "<span>Document</span><span>Type / Number</span><span>Updated</span><span></span>"
    : "<span>Document</span><span>Type / Number</span><span>Updated</span>";
  container.appendChild(header);

  docs.forEach((doc) => {
    const row = document.createElement("div");
    row.className = "doc-card";
    const label = doc.doc_number || doc.source;
    const href = doc.url || "#";
    const typeLine = [doc.doc_type, doc.doc_number].filter(Boolean).join(" · ") || "Uploaded file";
    const deleteBtn = deletable
      ? `<button type="button" class="doc-delete" title="Remove from uploads" data-source="${escapeHtml(doc.source)}">×</button>`
      : "";
    row.innerHTML = `
      <a href="${escapeHtml(href)}"${documentLinkAttrs(href)}>${escapeHtml(label)}</a>
      <span class="doc-type">${escapeHtml(typeLine)}</span>
      <span class="doc-date">${escapeHtml(formatDocDate(doc.updated_at || doc.indexed_at))}</span>
      ${deleteBtn}
    `;
    if (deletable) {
      row.querySelector(".doc-delete")?.addEventListener("click", () => deleteUpload(doc.source));
    }
    container.appendChild(row);
  });
}

async function deleteUpload(source) {
  if (!source || !confirm(`Remove "${source}" from your uploads and the search index?`)) return;
  try {
    const res = await apiFetch(`/files/${encodeURIComponent(source)}`, { method: "DELETE" });
    const data = await readJsonResponse(res);
    if (!res.ok) throw new Error(data.detail || "Delete failed.");
    showNotice(data.message || "File removed.", "info");
    await refreshUploads();
  } catch (err) {
    showNotice(err.message || "Could not delete file.", "error");
  }
}

async function refreshLibraryLinks() {
  try {
    const res = await apiFetch("/files");
    const data = await readJsonResponse(res);
    if (!res.ok || !data.documents?.length) return;

    const bySource = new Map(data.documents.map((d) => [d.source, d]));
    const byDocNumber = new Map(
      data.documents.filter((d) => d.doc_number).map((d) => [d.doc_number, d])
    );

    document.querySelectorAll("#libraryList .library-item a").forEach((link) => {
      const label = link.textContent.trim();
      const doc = bySource.get(label) || byDocNumber.get(label);
      if (!doc?.url) return;

      link.href = doc.url;
      if (isLocalDownloadUrl(doc.url)) {
        link.setAttribute("download", "");
        link.removeAttribute("target");
        link.removeAttribute("rel");
      } else {
        link.removeAttribute("download");
        link.setAttribute("target", "_blank");
        link.setAttribute("rel", "noopener");
      }
    });
  } catch {
    /* non-blocking */
  }
}

async function refreshUploads() {
  try {
    const res = await apiFetch("/files");
    const data = await readJsonResponse(res);
    if (!res.ok) {
      throw new Error(data.detail || "Could not load uploaded documents.");
    }
    const docs = data.documents?.length
      ? data.documents
      : (data.files || []).map((name) => ({ source: name, upload_origin: "library" }));

    const uploads = docs.filter((d) => d.upload_origin === "user");

    renderDocGrid(
      uploadListEl,
      uploads,
      "No user uploads yet — upload specs, submittals, or project files to get started.",
      { deletable: userRole === "admin" || userRole === "user" }
    );
  } catch (err) {
    const msg = escapeHtml(err.message || "Could not load uploaded documents.");
    uploadListEl.innerHTML = `<div class="file-grid-empty">${msg}</div>`;
  }
}

async function syncPublications() {
  try {
    const res = await apiFetch("/sync/publications", { method: "POST" });
    const data = await res.json();
    if (!res.ok) return;
    if (data.new_publications?.length) {
      const names = data.new_publications
        .slice(0, 3)
        .map((p) => p.doc_number || p.title)
        .join(", ");
      showNotice(
        `USACE publication check: ${data.new_publications.length} newly listed item(s) found (${names}${data.new_publications.length > 3 ? "…" : ""}).`,
        "info"
      );
    }
  } catch {
    /* non-blocking background sync */
  }
}

function showActiveJob(filename, pagesDone, pagesTotal) {
  activeJobsEl.hidden = false;
  const pct = pagesTotal ? Math.round((pagesDone / pagesTotal) * 100) : 0;
  activeJobsEl.innerHTML = `
    <strong>Indexing</strong> ${filename}<br />
    ${pagesDone.toLocaleString()} / ${pagesTotal.toLocaleString()} pages (${pct}%)
  `;
}

function hideActiveJob() {
  activeJobsEl.hidden = true;
  activeJobsEl.innerHTML = "";
}

async function pollJob(jobId, filename, pagesTotal, onProgress) {
  const interval = 3000;

  for (;;) {
    let res;
    let job;
    try {
      res = await apiFetch(`/jobs/${jobId}`);
      job = await res.json();
    } catch (err) {
      return { ok: false, message: err.message || "Could not check indexing status." };
    }
    if (!res.ok) {
      return { ok: false, message: job.detail || "Could not check indexing status." };
    }
    if (job.status === "running" || job.status === "queued") {
      const total = job.pages_total || pagesTotal || 0;
      const done = job.pages_done || 0;
      const pct = total ? Math.round((done / total) * 100) : -1;
      if (onProgress) {
        onProgress(
          pct,
          total
            ? `Indexing ${done.toLocaleString()} / ${total.toLocaleString()} pages (${pct}%)`
            : "Indexing on server…"
        );
      }
      await new Promise((r) => setTimeout(r, interval));
      continue;
    }
    if (job.status === "done") {
      showWarnings(job.warnings, "Indexing");
      return {
        ok: true,
        message: `Ready — ${job.chunks_indexed.toLocaleString()} sections from ${job.pages_done.toLocaleString()} pages`,
      };
    }
    return { ok: false, message: job.message || `${filename}: indexing failed.` };
  }
}

async function readJsonResponse(res) {
  const text = await res.text();
  if (!text) {
    throw new Error(`Server returned an empty response (HTTP ${res.status}).`);
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(
      `Server returned an unexpected response (HTTP ${res.status}). ` +
        "If you just uploaded a file, restart ./start.sh and try again."
    );
  }
}

function uploadOne(file, sessionId, onProgress) {
  // XHR (not fetch) so we can report real upload transfer progress.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/upload");
    xhr.withCredentials = true;
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    });
    xhr.addEventListener("load", () => {
      let data;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        reject(new Error(`Server returned an unexpected response (HTTP ${xhr.status}).`));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.detail || "Upload failed."));
    });
    xhr.addEventListener("error", () => reject(new Error("Network error during upload.")));
    const form = new FormData();
    form.append("file", file);
    form.append("session_id", sessionId);
    xhr.send(form);
  });
}

async function uploadFiles(files) {
  const session = ensureSession();
  for (const file of files) {
    const chip = addUploadChip(file.name);
    activeUploads++;
    refreshSendButton();
    try {
      setUploadProgress(chip, 0, "Uploading… 0%");
      const data = await uploadOne(file, session.id, (pct) => {
        setUploadProgress(
          chip,
          pct,
          pct >= 100 ? "Processing on server…" : `Uploading… ${pct}%`
        );
      });

      if (data.status === "processing" && data.job_id) {
        setUploadProgress(chip, -1, "Indexing on server…");
        const result = await pollJob(
          data.job_id,
          data.filename,
          data.pages_total || 0,
          (pct, label) => setUploadProgress(chip, pct, label)
        );
        if (result.ok) {
          trackSessionDocument(data.filename);
          addFileMessage(data.filename);
          markUploadDone(chip, result.message);
        } else {
          markUploadError(chip, result.message);
        }
      } else {
        trackSessionDocument(data.filename);
        addFileMessage(data.filename);
        showWarnings(data.warnings, "Indexing");
        const sections = data.chunks_indexed
          ? `Ready — ${Number(data.chunks_indexed).toLocaleString()} sections`
          : "Ready";
        markUploadDone(chip, sections);
      }
    } catch (err) {
      markUploadError(chip, err.message);
    } finally {
      activeUploads = Math.max(0, activeUploads - 1);
      refreshSendButton();
    }
  }
  await refreshUploads();
}

async function handleFiles(files) {
  if (!files || !files.length) return;
  showView("chat");
  // uploadFiles manages activeUploads + refreshSendButton per file, so the
  // send button re-enables as soon as the last upload settles. We intentionally
  // do NOT touch isQuerying here (that flag is only for in-flight queries).
  await uploadFiles(files);
  // Reflect the newly shared upload(s) in the User Uploads tab immediately.
  refreshUploads();
}

async function handleFileInput(input) {
  if (!input.files.length) return;
  await handleFiles(input.files);
  input.value = "";
}

fileInput.addEventListener("change", () => handleFileInput(fileInput));
fileInputDocs.addEventListener("change", () => handleFileInput(fileInputDocs));

/* ---------- Drag and drop uploads ---------- */

let dragDepth = 0;

function hasFileDrag(dt) {
  return dt && [...dt.types].includes("Files");
}

viewChat.addEventListener("dragenter", (e) => {
  if (!hasFileDrag(e.dataTransfer)) return;
  e.preventDefault();
  dragDepth++;
  viewChat.classList.add("drop-active");
  dropOverlay.hidden = false;
});

viewChat.addEventListener("dragover", (e) => {
  if (!hasFileDrag(e.dataTransfer)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
});

viewChat.addEventListener("dragleave", (e) => {
  if (!hasFileDrag(e.dataTransfer)) return;
  e.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) {
    viewChat.classList.remove("drop-active");
    dropOverlay.hidden = true;
  }
});

viewChat.addEventListener("drop", (e) => {
  if (!hasFileDrag(e.dataTransfer)) return;
  e.preventDefault();
  dragDepth = 0;
  viewChat.classList.remove("drop-active");
  dropOverlay.hidden = true;
  handleFiles(e.dataTransfer.files);
});

// Stop Finder from pasting a file path into the question box when dropped on the textarea.
questionEl.addEventListener("dragover", (e) => {
  if (hasFileDrag(e.dataTransfer)) e.preventDefault();
});
questionEl.addEventListener("drop", (e) => {
  if (!hasFileDrag(e.dataTransfer)) return;
  e.preventDefault();
  e.stopPropagation();
  dragDepth = 0;
  viewChat.classList.remove("drop-active");
  dropOverlay.hidden = true;
  handleFiles(e.dataTransfer.files);
});

/* ---------- Chat ---------- */

async function askQuestion(question) {
  ensureSession();
  addMessage("user", question);
  setLoading(true);
  hideNotice();

  // Search the full Document Library plus all user uploads, but prioritize files
  // attached to this session so follow-ups still see uploaded chapter text.
  const payload = { question, include_library: true };
  const focus = sessionFocusSources();
  if (focus?.length) payload.focus_sources = focus;
  const history = sessionHistory();
  if (history?.length) payload.history = history.slice(0, -1);

  try {
    const res = await apiFetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      addMessage("error", data.detail || "Query failed");
      return;
    }
    addMessage("assistant", data.answer, data.sources || [], data.citations || []);
    showWarnings(data.context_warnings, "This answer");
  } catch (err) {
    addMessage("error", err.message || "Network error — is the server running?");
  } finally {
    setLoading(false);
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  if (activeUploads > 0 || isQuerying) return; // block while files upload/index
  const question = questionEl.value.trim();
  if (!question) return;
  questionEl.value = "";
  questionEl.style.height = "auto";
  askQuestion(question);
});

questionEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

questionEl.addEventListener("input", () => {
  questionEl.style.height = "auto";
  questionEl.style.height = Math.min(questionEl.scrollHeight, 140) + "px";
});

// Starter questions are generated from topics x phrasings so the six cards
// vary in novel ways on (almost) every visit. ~40 topics x 8 templates gives
// hundreds of distinct questions, so the exact same set of six recurring is
// statistically rare.
const QUESTION_TOPICS = [
  "value engineering on USACE projects",
  "cost engineering and estimating",
  "submittal review and approval procedures",
  "design quality control plans",
  "construction quality management",
  "antiterrorism and force protection for buildings",
  "dam safety and risk management",
  "levee and floodwall design",
  "geotechnical engineering for water resources projects",
  "environmental compliance and NEPA",
  "sustainability and energy efficiency in facilities",
  "fire protection and life safety",
  "SCIF design and accreditation",
  "BIM and CAD standards",
  "document naming and numbering",
  "contract modifications and change orders",
  "real estate acquisition",
  "hydraulic and hydrologic design",
  "structural design criteria",
  "concrete materials and testing",
  "commissioning of building systems",
  "warranty requirements for construction",
  "project risk management",
  "occupational safety and health under EM 385-1-1",
  "use of Unified Facilities Criteria (UFC)",
  "use of Unified Facilities Guide Specifications (UFGS)",
  "engineering considerations during construction",
  "inspection and acceptance testing",
  "stormwater management and low-impact development",
  "seismic design requirements",
  "corrosion prevention and control",
  "accessibility (ABA) compliance",
  "interior design and signage standards",
  "roofing and waterproofing systems",
  "HVAC and mechanical systems design",
  "electrical power and lighting design",
  "military construction (MILCON) programming",
  "operations and maintenance manuals",
];

const QUESTION_TEMPLATES = [
  (t) => `What are the requirements for ${t}?`,
  (t) => `Summarize USACE guidance on ${t}.`,
  (t) => `Which USACE publications govern ${t}?`,
  (t) => `What are the key policies and procedures for ${t}?`,
  (t) => `Explain the roles and responsibilities for ${t}.`,
  (t) => `What standards and criteria apply to ${t}?`,
  (t) => `Give an overview of ${t} and the controlling regulations.`,
  (t) => `What guidance covers ${t}?`,
];

// A few high-value "signature" questions that occasionally appear verbatim.
const CURATED_QUESTIONS = [
  "What are the USACE policy and publication types, purposes, and hierarchy? Provide a list in the PAL library with a count of each and the total, sorted most to least.",
  "What are the document naming and numbering standards for USACE regulations and policies? List all document series codes with examples.",
  "What are the key differences between cost engineering requirements for Civil Works versus Military Programs?",
  "Do Civil Works projects require the use of UFC criteria and UFGS guide specifications?",
];

function shuffleInPlace(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function generateQuestions(count = 6) {
  const topics = shuffleInPlace(QUESTION_TOPICS.slice());
  const templates = shuffleInPlace(QUESTION_TEMPLATES.slice());
  const out = [];
  for (let i = 0; i < topics.length && out.length < count; i++) {
    // Distinct topic each card; cycle templates so phrasings differ too.
    out.push(templates[i % templates.length](topics[i]));
  }
  // ~50% of loads, swap one card for a curated signature question.
  if (out.length && Math.random() < 0.5) {
    const idx = Math.floor(Math.random() * out.length);
    out[idx] = CURATED_QUESTIONS[Math.floor(Math.random() * CURATED_QUESTIONS.length)];
  }
  return shuffleInPlace(out).slice(0, count);
}

function renderPromptCards(count = 6) {
  if (!promptGrid) return;
  promptGrid.innerHTML = "";
  for (const text of generateQuestions(count)) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "prompt-card";
    btn.textContent = text;
    promptGrid.appendChild(btn);
  }
}

promptGrid.addEventListener("click", (e) => {
  const card = e.target.closest(".prompt-card");
  if (!card) return;
  askQuestion(card.textContent.trim());
});

/* ---------- Sidebar ---------- */

newChatBtn.addEventListener("click", () => {
  newConversation();
  showView("chat");
});

sessionSearch.addEventListener("input", renderSessionList);

sidebarToggle.addEventListener("click", () => {
  sidebar.hidden = true;
  sidebarShow.hidden = false;
});

sidebarShow.addEventListener("click", () => {
  sidebar.hidden = false;
  sidebarShow.hidden = true;
});

/* ---------- Modals ---------- */

function openModal(modal) { modal.hidden = false; }
function closeModal(modal) { modal.hidden = true; }

aboutBtn.addEventListener("click", () => openModal(aboutModal));
helpBtn.addEventListener("click", () => openModal(helpModal));

[aboutModal, helpModal].forEach((modal) => {
  modal.addEventListener("click", (e) => {
    if (e.target === modal || e.target.closest("[data-close]")) closeModal(modal);
  });
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeModal(aboutModal);
    closeModal(helpModal);
  }
});

/* ---------- Init ---------- */

async function initApp() {
  await loadLimits();
  await refreshUploads();
  await refreshLibraryLinks();
}

async function bootstrap() {
  renderSessionList();
  renderPromptCards();
  await initApp();
}

/* ---------- Email login gate ---------- */
const USER_EMAIL_KEY = "spk_user_email";

// A valid sign-in requires a well-formed address on the @usace.army.mil domain.
function isValidUsaceEmail(value) {
  return /^[^\s@]+@usace\.army\.mil$/i.test((value || "").trim());
}

function updateLoginButton() {
  const ok = isValidUsaceEmail(loginEmail.value);
  loginSubmit.disabled = !ok;
  if (ok && !loginError.hidden) loginError.hidden = true;
}

function enterApp() {
  loginScreen.hidden = true;
  bootstrap();
}

loginEmail.addEventListener("input", updateLoginButton);
loginForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const email = loginEmail.value.trim();
  if (!isValidUsaceEmail(email)) {
    loginError.hidden = false;
    loginEmail.focus();
    return;
  }
  localStorage.setItem(USER_EMAIL_KEY, email.toLowerCase());
  enterApp();
});

// First screen: show the login gate unless a valid USACE email is already stored.
const savedUserEmail = localStorage.getItem(USER_EMAIL_KEY);
if (savedUserEmail && isValidUsaceEmail(savedUserEmail)) {
  enterApp();
} else {
  loginScreen.hidden = false;
  updateLoginButton();
  loginEmail.focus();
}
