const API_KEY_STORAGE = "spk_api_key";
const SESSIONS_STORAGE = "spk_sessions";

const messagesEl = document.getElementById("messages");
const chatScroll = document.getElementById("chatScroll");
const welcomeEl = document.getElementById("welcome");
const fileListEl = document.getElementById("fileList");
const fileInput = document.getElementById("fileInput");
const fileInputDocs = document.getElementById("fileInputDocs");
const chatForm = document.getElementById("chatForm");
const questionEl = document.getElementById("question");
const sendBtn = document.getElementById("sendBtn");
const clearBtn = document.getElementById("clearBtn");
const noticeBar = document.getElementById("noticeBar");
const activeJobsEl = document.getElementById("activeJobs");
const limitsListEl = document.getElementById("limitsList");
const authGate = document.getElementById("authGate");
const apiKeyInput = document.getElementById("apiKeyInput");
const authSaveBtn = document.getElementById("authSaveBtn");
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
const aboutModal = document.getElementById("aboutModal");
const helpModal = document.getElementById("helpModal");

let authRequired = false;

/* ---------- API key handling ---------- */

function getApiKey() {
  return sessionStorage.getItem(API_KEY_STORAGE) || "";
}

function setApiKey(key) {
  if (key) sessionStorage.setItem(API_KEY_STORAGE, key);
  else sessionStorage.removeItem(API_KEY_STORAGE);
}

function apiHeaders(extra = {}) {
  const headers = { ...extra };
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  return headers;
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: apiHeaders(options.headers || {}),
  });
  if (res.status === 401) {
    setApiKey("");
    showAuthGate();
    throw new Error("Invalid or missing access key.");
  }
  return res;
}

function showAuthGate() {
  authGate.hidden = false;
  apiKeyInput.focus();
}

function hideAuthGate() {
  authGate.hidden = true;
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
  };
  sessions.unshift(s);
  currentSessionId = s.id;
  return s;
}

function newConversation() {
  currentSessionId = null;
  messagesEl.innerHTML = "";
  welcomeEl.hidden = false;
  hideNotice();
  renderSessionList();
}

function openSession(id) {
  const s = sessions.find((x) => x.id === id);
  if (!s) return;
  currentSessionId = id;
  messagesEl.innerHTML = "";
  welcomeEl.hidden = s.messages.length > 0;
  s.messages.forEach((m) => renderMessage(m.role, m.text, m.sources || []));
  showView("chat");
  renderSessionList();
  chatScroll.scrollTop = chatScroll.scrollHeight;
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
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "session-item" + (s.id === currentSessionId ? " active" : "");
    btn.innerHTML = `<span class="dot"></span><span class="label"></span>`;
    btn.querySelector(".label").textContent = s.title;
    btn.title = `${s.title}\n${formatSessionTime(s.updated)}`;
    btn.addEventListener("click", () => openSession(s.id));
    sessionListEl.appendChild(btn);
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
  document.getElementById("view-documents").hidden = name !== "documents";
  if (name === "documents") refreshFiles();
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
    .replace(/`([^`\n]+)`/g, "<code>$1</code>");
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
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);

    if (h) {
      closeList();
      const level = Math.min(h[1].length + 2, 5);
      out.push(`<h${level}>${inlineMd(h[2])}</h${level}>`);
    } else if (ul) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inlineMd(ul[1])}</li>`);
    } else if (ol) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inlineMd(ol[1])}</li>`);
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

function renderMessage(role, text, sources = []) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
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
  if (sources.length) {
    const s = document.createElement("div");
    s.className = "sources";
    s.textContent = "Sources: " + sources.join(", ");
    div.appendChild(s);
  }
  messagesEl.appendChild(div);
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

function addMessage(role, text, sources = []) {
  welcomeEl.hidden = true;
  renderMessage(role, text, sources);

  const s = ensureSession();
  s.messages.push({ role, text, sources });
  if (role === "user" && s.title === "New conversation") {
    s.title = text.length > 60 ? text.slice(0, 57) + "..." : text;
  }
  s.updated = Date.now();
  saveSessions();
  renderSessionList();
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

function setLoading(on) {
  sendBtn.disabled = on;
  fileInput.disabled = on;
  fileInputDocs.disabled = on;
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

    if (authRequired && !getApiKey()) showAuthGate();
    else hideAuthGate();

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

async function refreshFiles() {
  if (authRequired && !getApiKey()) return;
  try {
    const res = await apiFetch("/files");
    const data = await res.json();
    fileListEl.innerHTML = "";
    if (!data.files.length) {
      fileListEl.innerHTML = "<li>No files indexed yet — upload documents to get started.</li>";
      return;
    }
    data.files.forEach((name) => {
      const li = document.createElement("li");
      li.textContent = name;
      fileListEl.appendChild(li);
    });
  } catch {
    /* auth gate already shown by apiFetch */
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

async function pollJob(jobId, filename, pagesTotal) {
  const interval = 3000;
  showNotice(
    `Indexing ${filename} (${pagesTotal.toLocaleString()} pages). Large PDFs may take 10–30+ minutes. Wait until indexing completes before relying on answers.`,
    "info"
  );
  showActiveJob(filename, 0, pagesTotal);

  for (;;) {
    const res = await apiFetch(`/jobs/${jobId}`);
    const job = await res.json();
    if (!res.ok) {
      hideActiveJob();
      hideNotice();
      addMessage("error", job.detail || "Could not check indexing status.");
      return;
    }
    if (job.status === "running" || job.status === "queued") {
      showActiveJob(filename, job.pages_done, job.pages_total);
      await new Promise((r) => setTimeout(r, interval));
      continue;
    }
    hideActiveJob();
    if (job.status === "done") {
      hideNotice();
      addMessage(
        "assistant",
        `${filename} is ready — ${job.chunks_indexed.toLocaleString()} searchable sections from ${job.pages_done.toLocaleString()} pages.`
      );
      showWarnings(job.warnings, "Indexing");
      await refreshFiles();
      return;
    }
    hideNotice();
    addMessage("error", job.message || `${filename}: indexing failed.`);
    showNotice(job.message || "Indexing failed.", "error");
    return;
  }
}

async function uploadFiles(files) {
  for (const file of files) {
    addMessage("assistant", `Uploading ${file.name}…`);
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await apiFetch("/upload", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) {
        addMessage("error", data.detail || "Upload failed");
        showNotice(data.detail || "Upload failed", "error");
        continue;
      }
      if (data.status === "processing" && data.job_id) {
        addMessage("assistant", data.message);
        await pollJob(data.job_id, data.filename, data.pages_total || 0);
        continue;
      }
      addMessage("assistant", `${data.filename} indexed (${data.chunks_indexed} chunks).`);
      showWarnings(data.warnings, "Indexing");
    } catch (err) {
      addMessage("error", err.message);
    }
  }
  await refreshFiles();
}

async function handleFileInput(input) {
  if (!input.files.length) return;
  if (authRequired && !getApiKey()) {
    showAuthGate();
    return;
  }
  showView("chat");
  setLoading(true);
  try {
    await uploadFiles(input.files);
  } finally {
    input.value = "";
    setLoading(false);
  }
}

fileInput.addEventListener("change", () => handleFileInput(fileInput));
fileInputDocs.addEventListener("change", () => handleFileInput(fileInputDocs));

/* ---------- Chat ---------- */

async function askQuestion(question) {
  if (authRequired && !getApiKey()) {
    showAuthGate();
    return;
  }

  addMessage("user", question);
  setLoading(true);
  hideNotice();

  try {
    const res = await apiFetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    if (!res.ok) {
      addMessage("error", data.detail || "Query failed");
      return;
    }
    addMessage("assistant", data.answer, data.sources || []);
    showWarnings(data.context_warnings, "This answer");
  } catch (err) {
    addMessage("error", err.message || "Network error — is the server running?");
  } finally {
    setLoading(false);
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
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

/* ---------- Auth gate ---------- */

authSaveBtn.addEventListener("click", async () => {
  const key = apiKeyInput.value.trim();
  if (!key) return;
  setApiKey(key);
  apiKeyInput.value = "";
  hideAuthGate();
  await loadLimits();
  await refreshFiles();
});

apiKeyInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") authSaveBtn.click();
});

/* ---------- Clear index ---------- */

clearBtn.addEventListener("click", async () => {
  if (!confirm("Clear all indexed documents?")) return;
  try {
    await apiFetch("/reset", { method: "POST" });
    hideNotice();
    hideActiveJob();
    addMessage("assistant", "Index cleared. Upload files to start again.");
    await refreshFiles();
  } catch (err) {
    addMessage("error", err.message);
  }
});

/* ---------- Init ---------- */

renderSessionList();
loadLimits().then(() => refreshFiles());
