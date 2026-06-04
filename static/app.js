const API_KEY_STORAGE = "spk_api_key";

const messagesEl = document.getElementById("messages");
const fileListEl = document.getElementById("fileList");
const fileInput = document.getElementById("fileInput");
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

let authRequired = false;

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
  fileInput.disabled = true;
  sendBtn.disabled = true;
}

function hideAuthGate() {
  authGate.hidden = true;
  fileInput.disabled = false;
  sendBtn.disabled = false;
}

function updateConfigStatus(health) {
  configStatus.hidden = false;
  const issues = [];
  if (!health.llm_configured) issues.push("Anthropic API key missing on server");
  if (!health.embeddings_configured) issues.push("Embedding API key missing on server");

  if (issues.length) {
    configStatus.className = "config-status warn";
    configStatus.textContent =
      "Server not fully configured — uploads and chat will fail until API keys are set. " +
      issues.join(". ");
    return;
  }
  configStatus.className = "config-status ok";
  configStatus.textContent = "Server ready for uploads and chat.";
}

function addMessage(role, text, sources = []) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  const p = document.createElement("p");
  p.textContent = text;
  div.appendChild(p);
  if (sources.length) {
    const s = document.createElement("div");
    s.className = "sources";
    s.textContent = "Sources: " + sources.join(", ");
    div.appendChild(s);
  }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
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

function showWarnings(warnings, context = "Upload") {
  if (!warnings?.length) return;
  const text = warnings.join(" ");
  addMessage("notice", `${context}: ${text}`);
  showNotice(`${context} notice: ${text}`, "info");
}

function setLoading(on) {
  if (authRequired && !getApiKey()) return;
  sendBtn.disabled = on;
  fileInput.disabled = on;
}

async function loadLimits() {
  try {
    const res = await fetch("/health");
    const data = await res.json();
    if (!res.ok) return;

    authRequired = data.auth_required;
    updateConfigStatus(data);

    if (authRequired && !getApiKey()) {
      showAuthGate();
    } else {
      hideAuthGate();
    }

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

async function refreshFiles() {
  if (authRequired && !getApiKey()) return;
  const res = await apiFetch("/files");
  const data = await res.json();
  fileListEl.innerHTML = "";
  if (!data.files.length) {
    fileListEl.innerHTML = "<li>No files indexed yet</li>";
    return;
  }
  data.files.forEach((name) => {
    const li = document.createElement("li");
    li.textContent = name;
    fileListEl.appendChild(li);
  });
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

authSaveBtn.addEventListener("click", async () => {
  const key = apiKeyInput.value.trim();
  if (!key) return;
  setApiKey(key);
  apiKeyInput.value = "";
  hideAuthGate();
  await loadLimits();
  await refreshFiles();
});

fileInput.addEventListener("change", async () => {
  if (!fileInput.files.length) return;
  if (authRequired && !getApiKey()) {
    showAuthGate();
    return;
  }
  setLoading(true);
  try {
    await uploadFiles(fileInput.files);
  } finally {
    fileInput.value = "";
    setLoading(false);
  }
});

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = questionEl.value.trim();
  if (!question) return;
  if (authRequired && !getApiKey()) {
    showAuthGate();
    return;
  }

  addMessage("user", question);
  questionEl.value = "";
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
});

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

loadLimits().then(() => refreshFiles());
