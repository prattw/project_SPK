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

/* ---------- Auth token (24-hour roster sign-in) ---------- */

const AUTH_TOKEN_KEY = "spk_auth_token";
const AUTH_EXPIRES_KEY = "spk_auth_expires";

function authToken() {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const expires = Number(localStorage.getItem(AUTH_EXPIRES_KEY) || 0);
  if (!token || !expires || Date.now() / 1000 >= expires) return null;
  return token;
}

function clearAuthToken() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_EXPIRES_KEY);
}

function requireSignIn() {
  clearAuthToken();
  loginScreen.hidden = false;
  updateLoginButton();
  loginEmail.focus();
}

function withToken(url) {
  // Plain <a href> downloads can't send headers — pass the token in the query.
  const token = authToken();
  if (!token || typeof url !== "string" || !url.startsWith("/download/")) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}

/* ---------- Fetch helper ---------- */

async function apiFetch(url, options = {}) {
  const token = authToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(url, { credentials: "same-origin", ...options, headers });
  if (res.status === 401) requireSignIn();
  return res;
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
  if (!s) return null;
  const names = new Set(s.documents || []);
  for (const m of s.messages || []) {
    if (m.role === "file" && m.text) names.add(m.text);
  }
  return names.size ? [...names] : null;
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
  document.getElementById("view-email").hidden = name !== "email";
  if (name === "uploads") refreshUploads();
  if (name === "library") refreshLibraryLinks();
  if (name === "email") loadEmailStatus();
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
            `<a href="${escapeHtml(withToken(item.url))}"${documentLinkAttrs(item.url)}>${escapeHtml(item.label)}</a>`
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

function reportClientError(message, context) {
  // Fire-and-forget: usage analytics must never disrupt the user experience.
  try {
    const s = currentSession();
    apiFetch("/log/client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: String(message).slice(0, 1000),
        context: context ? String(context).slice(0, 200) : null,
        session_id: s?.id || null,
      }),
    }).catch(() => {});
  } catch {
    /* ignore */
  }
}

function addMessage(role, text, sources = [], citations = []) {
  welcomeEl.hidden = true;
  renderMessage(role, text, sources, citations);
  if (role === "error") reportClientError(text, "chat");

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

/* ---------- Waiting facts (shown while a query is in flight) ---------- */

// Facts about Sacramento and California's Central Valley, rotated once a
// minute while the user waits. Drawn without repeats until all 100 are used.
const WAITING_FACTS = [
  "Sacramento became California's permanent state capital in 1854.",
  "Sacramento sits at the confluence of the Sacramento and American Rivers.",
  "Sacramento is nicknamed the City of Trees for its dense urban canopy.",
  "The Gold Rush began in 1848 at Sutter's Mill, about 40 miles east of Sacramento.",
  "Old Sacramento preserves one of the largest collections of Gold Rush-era buildings in the West.",
  "The Pony Express had its western terminus in Sacramento.",
  "Construction of the first transcontinental railroad began in Sacramento in 1863.",
  "The California State Railroad Museum in Old Sacramento is one of the largest railroad museums in North America.",
  "After the floods of the 1860s, Sacramento raised its downtown streets by as much as 14 feet.",
  "Underground tours still explore the buried original street level beneath Old Sacramento.",
  "The gold-colored Tower Bridge opened in 1935, linking Sacramento and West Sacramento.",
  "California's Capitol building in Sacramento was completed in 1874 after 14 years of construction.",
  "Capitol Park in Sacramento holds trees from around the world, planted since the 1870s.",
  "Sacramento is regularly ranked among the most ethnically diverse major cities in America.",
  "The Sacramento Kings play at Golden 1 Center, one of the world's greenest arenas.",
  "Golden 1 Center was the first indoor arena to earn LEED Platinum certification.",
  "Sacramento hosts the California State Fair at Cal Expo every summer.",
  "Sacramento is known as America's Farm-to-Fork Capital.",
  "Sacramento's Farm-to-Fork Festival draws huge crowds to Capitol Mall each September.",
  "The American River Parkway offers more than 30 miles of riverside trails through Sacramento.",
  "Sutter's Fort, built in 1839, was one of the earliest non-Indigenous settlements in the Central Valley.",
  "The Delta King, a 1920s riverboat, is permanently docked in Old Sacramento as a hotel.",
  "The Crocker Art Museum, founded in 1885, is the longest continuously operating art museum in the West.",
  "Sacramento summers are nearly cloudless, making it one of the sunniest cities in the country from July through September.",
  "The evening Delta breeze cools Sacramento with air drawn in from the Sacramento-San Joaquin Delta.",
  "The rose garden in Sacramento's McKinley Park has bloomed since 1928.",
  "Sacramento's numbered-and-lettered street grid was laid out in 1848.",
  "The Sacramento River is California's longest river, flowing roughly 400 miles.",
  "The Sacramento Zoo opened in William Land Park in 1927.",
  "Both Lake Tahoe and San Francisco are about a two-hour drive from Sacramento.",
  "SMUD, Sacramento's community-owned electric utility, is one of the largest in the nation.",
  "Sacramento's Fabulous Forties neighborhood is famous for its 1920s-1940s homes.",
  "Author Joan Didion grew up in Sacramento.",
  "The Sacramento Bee has been publishing since 1857.",
  "Second Saturday art walks light up Sacramento's Midtown galleries every month.",
  "Tower Records began in Sacramento in 1960, named after the Tower Theatre.",
  "The Sacramento River Cats play Triple-A baseball at Sutter Health Park in West Sacramento.",
  "Sutter Health Park became the temporary home of Major League Baseball's Athletics in 2025.",
  "William Land Park spans more than 160 acres with a zoo, a golf course, and Fairytale Town.",
  "Fairytale Town, a storybook park for children, opened in Sacramento in 1959.",
  "Sacramento's Memorial Auditorium has hosted events since 1927.",
  "Sacramento sits only about 30 feet above sea level.",
  "Sacramento is one of the most flood-prone major U.S. cities, protected by an extensive levee network.",
  "The Yolo Bypass can carry several times the Sacramento River's flow during major storms and doubles as wildlife habitat.",
  "The U.S. Army Corps of Engineers built Folsom Dam on the American River, completed in 1956.",
  "The Sacramento Weir, built in 1916, diverts floodwaters away from the city into the Yolo Bypass.",
  "California's Central Valley stretches about 450 miles from Redding to Bakersfield.",
  "The Central Valley averages 40 to 60 miles in width.",
  "The Central Valley comprises the Sacramento Valley in the north and the San Joaquin Valley in the south.",
  "The Central Valley produces about a quarter of America's food on roughly 1% of the nation's farmland.",
  "More than 250 different crops are grown in the Central Valley.",
  "The Central Valley produces the vast majority of the world's almonds.",
  "Around 90% of America's processing tomatoes are grown in the Central Valley.",
  "Sacramento Valley rice fields supply much of the sushi rice eaten in the United States.",
  "The Sutter Buttes near Yuba City are often called the world's smallest mountain range.",
  "Tulare Lake in the Central Valley was once the largest freshwater lake west of the Mississippi.",
  "Tulare Lake briefly reappeared in the wet winter of 2023, flooding thousands of acres.",
  "The Central Valley Project moves water through roughly 500 miles of canals and aqueducts.",
  "The California Aqueduct carries water more than 400 miles from the Delta toward Southern California.",
  "Shasta Lake, anchoring the Central Valley Project, is California's largest reservoir.",
  "The Sacramento-San Joaquin Delta is part of the largest estuary on the U.S. West Coast.",
  "Delta water supplies roughly 27 million Californians.",
  "The Central Valley floor was historically a vast seasonal wetland roamed by tule elk and pronghorn.",
  "Tule fog, the valley's dense winter ground fog, is named for the tule reeds of its wetlands.",
  "Millions of migratory birds funnel through Central Valley wetlands each winter along the Pacific Flyway.",
  "Snow geese gather by the hundreds of thousands at Sacramento Valley wildlife refuges each winter.",
  "The San Joaquin River is California's second-longest river at about 366 miles.",
  "Fresno is the largest city located entirely within the San Joaquin Valley.",
  "Kern County, at the valley's southern end, produces most of California's oil.",
  "The Bakersfield Sound of Buck Owens and Merle Haggard reshaped country music in the 1950s and 60s.",
  "The Central Valley is so flat that elevation changes only a few hundred feet over hundreds of miles.",
  "Interstate 5 and Highway 99 run nearly the full length of the Central Valley.",
  "UC Davis, just west of Sacramento, is a world leader in agricultural and veterinary science.",
  "Davis installed the first dedicated bike lanes in the United States in 1967.",
  "The Central Valley's Mediterranean climate delivers hot, dry summers and cool, wet winters.",
  "Central Valley soils rank among the most fertile on Earth, built from eons of Sierra Nevada sediment.",
  "The Central Valley is a geologic trough filled with sediment several miles deep in places.",
  "Groundwater supplies a large share of the Central Valley's water in drought years.",
  "Groundwater pumping has sunk parts of the San Joaquin Valley nearly 30 feet since the 1920s.",
  "The Great Flood of 1862 turned the Central Valley into an inland sea roughly 300 miles long.",
  "Governor Leland Stanford reportedly traveled to his 1862 inauguration by rowboat through flooded Sacramento streets.",
  "John Sutter's 1839 Mexican land grant of about 48,000 acres became New Helvetia, the seed of Sacramento.",
  "Sacramento, incorporated in 1850, is one of California's oldest incorporated cities.",
  "The Big Four who financed the transcontinental railroad were Sacramento merchants.",
  "The transcontinental railroad's western construction began near Front and K Streets in Sacramento.",
  "Sacramento's historic Chinatown was among the earliest Chinese communities in California.",
  "February's almond bloom across the valley is the largest managed pollination event on Earth.",
  "Nearly all U.S. commercial raisins come from the San Joaquin Valley around Fresno.",
  "Lodi, in the northern San Joaquin Valley, is known as a Zinfandel wine capital.",
  "Chico's Bidwell Park is one of the largest municipal parks in the United States.",
  "Wild deer and turkeys roam the Effie Yeaw Nature Center along the American River in Sacramento.",
  "The California State Capitol dome was modeled in part on the U.S. Capitol.",
  "A 43-mile deepwater ship channel links West Sacramento's port to San Francisco Bay.",
  "Flooded winter rice fields near Sacramento create temporary wetlands visible from space.",
  "Sacramento is nicknamed Camellia City and held an annual Camellia Festival for decades.",
  "The Sacramento Valley grows virtually all of California's rice crop.",
  "Caltrans, headquartered in Sacramento, maintains more than 50,000 lane miles of state highways.",
  "The Sacramento region is home to more than 200,000 acres of protected wildlife habitat.",
  "Sacramento's Land Park neighborhood surrounds one of the city's oldest public golf courses.",
  "The Sacramento District of the U.S. Army Corps of Engineers manages flood risk for much of the Central Valley.",
];

let factBag = [];

function nextWaitingFact() {
  if (!factBag.length) {
    factBag = [...WAITING_FACTS];
    for (let i = factBag.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [factBag[i], factBag[j]] = [factBag[j], factBag[i]];
    }
  }
  return factBag.pop();
}

let waitingEl = null;
let waitingTimer = null;

function showWaitingFacts() {
  hideWaitingFacts();
  waitingEl = document.createElement("div");
  waitingEl.className = "msg waiting";
  waitingEl.innerHTML = `
    <p class="waiting-status">Working on your answer&hellip; large files can take 3&ndash;15 minutes.</p>
    <p class="waiting-fact"></p>
  `;
  messagesEl.appendChild(waitingEl);
  const factEl = waitingEl.querySelector(".waiting-fact");
  const showFact = () => {
    factEl.textContent = `While you wait — did you know? ${nextWaitingFact()}`;
  };
  showFact();
  chatScroll.scrollTop = chatScroll.scrollHeight;
  waitingTimer = setInterval(showFact, 60000);
}

function hideWaitingFacts() {
  if (waitingTimer) {
    clearInterval(waitingTimer);
    waitingTimer = null;
  }
  if (waitingEl) {
    waitingEl.remove();
    waitingEl = null;
  }
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
      <a href="${escapeHtml(withToken(href))}"${documentLinkAttrs(href)}>${escapeHtml(label)}</a>
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

function libraryDocLabel(doc) {
  return doc.doc_number || doc.display_title || doc.title || doc.source;
}

function libraryDocSecondary(doc) {
  const label = libraryDocLabel(doc);
  if (doc.title && doc.title !== label) return doc.title;
  const stem = (doc.source || "").replace(/\.[^.]+$/, "");
  if (stem && stem !== label) return stem.replace(/_/g, " ");
  return "";
}

function libraryDocListed(doc, libraryText) {
  const hay = libraryText.toLowerCase();
  const checks = [
    doc.source,
    doc.doc_number,
    doc.display_title,
    doc.title,
    (doc.source || "").replace(/\.[^.]+$/, ""),
  ].filter(Boolean);
  return checks.some((value) => hay.includes(String(value).toLowerCase()));
}

function renderLibraryItem(doc) {
  const label = escapeHtml(libraryDocLabel(doc));
  const href = escapeHtml(withToken(doc.url || "#"));
  const secondary = libraryDocSecondary(doc);
  const secondaryHtml = secondary ? `, ${escapeHtml(secondary)}` : "";
  const date = formatDocDate(doc.updated_at || doc.indexed_at);
  const dateHtml =
    date && date !== "—" ? `, published: ${escapeHtml(date)}` : ", published: —";
  return `<div class="library-item"><a href="${href}"${documentLinkAttrs(doc.url || "")}>${label}</a>${secondaryHtml}<span class="lib-updated">${dateHtml}</span></div>`;
}

async function refreshLibraryLinks() {
  const libraryListEl = document.getElementById("libraryList");
  const recentEl = document.getElementById("libraryRecent");
  if (!libraryListEl || !recentEl) return;

  try {
    const res = await apiFetch("/files");
    const data = await readJsonResponse(res);
    if (!res.ok || !data.documents?.length) {
      recentEl.hidden = true;
      recentEl.innerHTML = "";
      return;
    }

    const bySource = new Map(data.documents.map((d) => [d.source, d]));
    const byDocNumber = new Map(
      data.documents.filter((d) => d.doc_number).map((d) => [d.doc_number, d])
    );

    document.querySelectorAll("#libraryList .library-item a").forEach((link) => {
      const label = link.textContent.trim();
      const doc = bySource.get(label) || byDocNumber.get(label);
      if (!doc?.url) return;

      link.href = withToken(doc.url);
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

    const libraryText = libraryListEl.innerHTML;
    const extras = data.documents
      .filter((d) => (d.upload_origin || "").toLowerCase() === "library")
      .filter((d) => !libraryDocListed(d, libraryText))
      .sort((a, b) => {
        const ta = Date.parse(a.indexed_at || "") || 0;
        const tb = Date.parse(b.indexed_at || "") || 0;
        return tb - ta;
      });

    if (!extras.length) {
      recentEl.hidden = true;
      recentEl.innerHTML = "";
      return;
    }

    recentEl.hidden = false;
    recentEl.innerHTML =
      `<h3 class="library-recent-title">Recently indexed (searchable)</h3>` +
      `<div class="library-list library-list-recent">${extras.map(renderLibraryItem).join("")}</div>`;
  } catch {
    recentEl.hidden = true;
    recentEl.innerHTML = "";
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

async function pollQueryJob(jobId) {
  const interval = 2000;

  for (;;) {
    let res;
    let job;
    try {
      res = await apiFetch(`/jobs/${jobId}`);
      job = await readJsonResponse(res);
    } catch (err) {
      return { ok: false, message: err.message || "Could not check query status." };
    }
    if (!res.ok) {
      return { ok: false, message: job.detail || "Could not check query status." };
    }
    if (job.status === "queued" || job.status === "running") {
      await new Promise((r) => setTimeout(r, interval));
      continue;
    }
    if (job.status === "done" && job.result) {
      return { ok: true, data: job.result, elapsed_ms: job.elapsed_ms };
    }
    return { ok: false, message: job.message || "Query failed." };
  }
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
    const token = authToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.addEventListener("load", () => {
      if (xhr.status === 401) requireSignIn();
    });
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
  showWaitingFacts();

  // Search the full Document Library plus all user uploads, but prioritize files
  // attached to this session so follow-ups still see uploaded chapter text.
  const payload = { question, include_library: true };
  const s = currentSession();
  if (s?.id) payload.session_id = s.id;
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
    let data;
    try {
      data = await readJsonResponse(res);
    } catch {
      hideWaitingFacts();
      addMessage(
        "error",
        "The server returned an unexpected response when starting your query. Please try again."
      );
      return;
    }
    if (!res.ok) {
      hideWaitingFacts();
      addMessage("error", data.detail || "Query failed");
      return;
    }

    const result = await pollQueryJob(data.job_id);
    hideWaitingFacts();
    if (!result.ok) {
      addMessage("error", result.message || "Query failed");
      return;
    }
    addMessage(
      "assistant",
      result.data.answer,
      result.data.sources || [],
      result.data.citations || []
    );
    showWarnings(result.data.context_warnings, "This answer");
  } catch (err) {
    addMessage("error", err.message || "Network error — is the server running?");
  } finally {
    hideWaitingFacts();
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

/* ---------- Email Assistant (Outlook) ---------- */

const emailTextEl = document.getElementById("emailText");
const emailMsgInput = document.getElementById("emailMsgInput");
const emailAnalyzeBtn = document.getElementById("emailAnalyzeBtn");
const emailDraftBtn = document.getElementById("emailDraftBtn");
const emailClearBtn = document.getElementById("emailClearBtn");
const emailToneEl = document.getElementById("emailTone");
const emailInstructionsEl = document.getElementById("emailInstructions");
const emailUseLibraryEl = document.getElementById("emailUseLibrary");
const emailStatusEl = document.getElementById("emailStatus");
const emailResultsEl = document.getElementById("emailResults");
const emailMailboxEl = document.getElementById("emailMailboxStatus");
const emailInputNoteEl = document.getElementById("emailInputNote");

let emailStatusLoaded = false;
let emailBusy = false;

/** FastAPI sends a string detail for our errors and a list for validation errors. */
function emailErrorText(detail, fallback) {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => (d && typeof d === "object" ? d.msg || "" : String(d))).filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  if (detail && typeof detail === "object" && typeof detail.msg === "string") return detail.msg;
  return fallback;
}

function setEmailStatus(message, kind = "info") {
  if (!emailStatusEl) return;
  if (!message) {
    emailStatusEl.hidden = true;
    emailStatusEl.textContent = "";
    return;
  }
  emailStatusEl.hidden = false;
  emailStatusEl.className = `email-status email-status-${kind}`;
  emailStatusEl.textContent = message;
}

function setEmailBusy(busy, label) {
  emailBusy = busy;
  [emailAnalyzeBtn, emailDraftBtn, emailMsgInput].forEach((el) => {
    if (el) el.disabled = busy;
  });
  if (busy) setEmailStatus(label || "Working...", "busy");
}

function emailRedactionNote(info) {
  const redactions = (info && info.redactions) || {};
  const entries = Object.entries(redactions).filter(([, n]) => n > 0);
  if (!entries.length) return "";
  const labels = { SSN: "SSN", "DOD-ID": "DoD ID", DOB: "date of birth", CARD: "card number" };
  return (
    "Redacted before sending to the AI: " +
    entries.map(([key, n]) => `${n} ${labels[key] || key}`).join(", ") +
    "."
  );
}

function renderEmailMeta(info) {
  if (!info) return "";
  const rows = [
    ["Subject", info.subject],
    ["From", info.sender],
    ["Sent", info.sent],
    ["Messages in thread", info.turn_count ? String(info.turn_count) : ""],
    ["Participants", (info.participants || []).join(", ")],
    ["Attachments", (info.attachments || []).join(", ")],
  ].filter(([, value]) => value);

  const note = emailRedactionNote(info);
  return (
    `<div class="email-card email-card-meta">` +
    `<h3 class="email-card-title">Email</h3>` +
    rows
      .map(
        ([label, value]) =>
          `<div class="email-meta-row"><span class="email-meta-label">${escapeHtml(label)}</span>` +
          `<span class="email-meta-value">${escapeHtml(value)}</span></div>`
      )
      .join("") +
    (note ? `<p class="email-redaction">${escapeHtml(note)}</p>` : "") +
    `</div>`
  );
}

function renderEmailList(title, items) {
  if (!items || !items.length) return "";
  return (
    `<h4 class="email-sub">${escapeHtml(title)}</h4><ul class="email-ul">` +
    items.map((item) => `<li>${escapeHtml(item)}</li>`).join("") +
    `</ul>`
  );
}

function renderEmailActionItems(items) {
  if (!items || !items.length) return "";
  return (
    `<h4 class="email-sub">Action items</h4><ul class="email-ul email-ul-actions">` +
    items
      .map((item) => {
        const owner = item.owner && item.owner !== "unclear" ? item.owner : "owner unclear";
        const due = item.due && item.due !== "none stated" ? item.due : "no date stated";
        return (
          `<li>${escapeHtml(item.action)}` +
          `<span class="email-action-meta">${escapeHtml(owner)} &middot; ${escapeHtml(due)}</span></li>`
        );
      })
      .join("") +
    `</ul>`
  );
}

function renderEmailAnalysis(analysis) {
  const priority = (analysis.priority || "medium").toLowerCase();
  const badges = [
    `<span class="email-badge email-badge-${escapeHtml(priority)}">${escapeHtml(priority)} priority</span>`,
    analysis.category ? `<span class="email-badge">${escapeHtml(analysis.category)}</span>` : "",
    `<span class="email-badge ${analysis.reply_needed ? "email-badge-reply" : ""}">${
      analysis.reply_needed ? "reply needed" : "no reply needed"
    }</span>`,
  ].join("");

  const reasons = [analysis.priority_reason, analysis.reply_needed_reason]
    .filter(Boolean)
    .map((r) => `<p class="email-reason">${escapeHtml(r)}</p>`)
    .join("");

  return (
    `<div class="email-card">` +
    `<h3 class="email-card-title">Summary &amp; triage</h3>` +
    `<div class="email-badges">${badges}</div>` +
    (analysis.summary ? `<p class="email-summary">${escapeHtml(analysis.summary)}</p>` : "") +
    reasons +
    renderEmailList("Key points", analysis.key_points) +
    renderEmailActionItems(analysis.action_items) +
    renderEmailList("Deadlines", analysis.deadlines) +
    renderEmailList("Open questions", analysis.open_questions) +
    (analysis.suggested_next_step
      ? `<h4 class="email-sub">Suggested next step</h4><p class="email-next">${escapeHtml(
          analysis.suggested_next_step
        )}</p>`
      : "") +
    `</div>`
  );
}

function renderEmailDraft(draft) {
  const citations = (draft.citations || [])
    .map((c) => {
      const label = c.doc_number || c.source || "";
      const pages = c.page_start ? `, p. ${c.page_start}` : "";
      if (!label) return "";
      return c.url
        ? `<li><a href="${escapeHtml(withToken(c.url))}" target="_blank" rel="noopener">${escapeHtml(
            label
          )}${escapeHtml(pages)}</a></li>`
        : `<li>${escapeHtml(label + pages)}</li>`;
    })
    .filter(Boolean)
    .join("");

  return (
    `<div class="email-card email-card-draft">` +
    `<div class="email-card-head">` +
    `<h3 class="email-card-title">Draft reply</h3>` +
    `<button type="button" class="email-copy-btn" id="emailCopyBtn">Copy draft</button>` +
    `</div>` +
    (draft.subject ? `<div class="email-draft-subject">${escapeHtml(draft.subject)}</div>` : "") +
    `<div class="email-draft-body" id="emailDraftBody">${escapeHtml(draft.body || "")}</div>` +
    (citations
      ? `<h4 class="email-sub">Document Library sources cited</h4><ul class="email-ul">${citations}</ul>`
      : "") +
    (draft.library_error
      ? `<p class="email-draft-warning">${escapeHtml(draft.library_error)}</p>`
      : draft.used_library
        ? ""
        : `<p class="email-reason">Not grounded in the Document Library — turn on "Cite the Document Library" under Reply options if the reply needs to quote USACE policy.</p>`) +
    `<p class="email-draft-warning">Review and edit this draft before sending. Project SPK cannot send email — copy it into Outlook yourself.</p>` +
    `</div>`
  );
}

function wireEmailCopyButton() {
  const btn = document.getElementById("emailCopyBtn");
  const body = document.getElementById("emailDraftBody");
  if (!btn || !body) return;
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(body.textContent || "");
      btn.textContent = "Copied";
      setTimeout(() => (btn.textContent = "Copy draft"), 1500);
    } catch {
      // Clipboard access can be blocked; select the text so Ctrl+C still works.
      const range = document.createRange();
      range.selectNodeContents(body);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      btn.textContent = "Press Ctrl+C";
      setTimeout(() => (btn.textContent = "Copy draft"), 2500);
    }
  });
}

async function loadEmailStatus() {
  if (emailStatusLoaded) {
    maybeAutostartSweep();
    return;
  }
  try {
    const res = await apiFetch("/email/status");
    const data = await readJsonResponse(res);
    if (!res.ok) return;
    emailStatusLoaded = true;
    applySweepConfig(data);

    if (emailToneEl && !emailToneEl.options.length) {
      (data.tones || []).forEach((tone) => {
        const option = document.createElement("option");
        option.value = tone.key;
        option.textContent = `${tone.key} — ${tone.description}`;
        emailToneEl.appendChild(option);
      });
    }

    if (emailMsgInput && data.msg_upload_supported === false) {
      const label = emailMsgInput.closest("label");
      if (label) label.hidden = true;
    }

    const mailbox = data.mailbox || {};
    if (emailMailboxEl) {
      const alternatives = mailbox.alternatives || [];
      const graph =
        mailbox.connector === "graph"
          ? mailbox
          : alternatives.find((alt) => alt.connector === "graph");
      const requirements = (graph && graph.requirements) || [];
      emailMailboxEl.hidden = false;
      emailMailboxEl.innerHTML =
        `<div class="email-mailbox-line"><strong>Mailbox connection:</strong> ${escapeHtml(
          mailbox.description || "Not connected."
        )}</div>` +
        (requirements.length
          ? `<details class="email-mailbox-details"><summary>What direct Outlook mailbox access still needs (${requirements.length})</summary>` +
            `<ul class="email-ul">${requirements.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>` +
            `</details>`
          : "");
    }

    if (data.enabled === false) {
      setEmailStatus(
        "The email assistant is disabled on this deployment. Set EMAIL_ASSISTANT_ENABLED=true to turn it on.",
        "error"
      );
      setSweepStatus(
        "The email assistant is disabled on this deployment. Set EMAIL_ASSISTANT_ENABLED=true to turn it on.",
        "error"
      );
      [emailAnalyzeBtn, emailDraftBtn, emailSweepBtn, emailSweepFilesInput].forEach(
        (el) => el && (el.disabled = true)
      );
      return;
    }

    maybeAutostartSweep();
  } catch {
    /* status is informational — the actions report their own errors */
  }
}

function emailPayloadText() {
  const text = (emailTextEl?.value || "").trim();
  if (!text) {
    setEmailStatus("Paste an email thread first, or upload a .msg file.", "error");
    emailTextEl?.focus();
    return null;
  }
  return text;
}

async function runEmailAnalyze() {
  if (emailBusy) return;
  const text = emailPayloadText();
  if (!text) return;

  setEmailBusy(true, "Reading the thread and triaging...");
  try {
    const res = await apiFetch("/email/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, session_id: currentSessionId }),
    });
    const data = await readJsonResponse(res);
    if (!res.ok) {
      throw new Error(emailErrorText(data.detail, "Could not analyze that email."));
    }
    emailResultsEl.innerHTML = renderEmailMeta(data.email) + renderEmailAnalysis(data.analysis);
    setEmailStatus("");
  } catch (err) {
    setEmailStatus(err.message || "Could not analyze that email.", "error");
  } finally {
    setEmailBusy(false);
  }
}

async function runEmailDraft() {
  if (emailBusy) return;
  const text = emailPayloadText();
  if (!text) return;

  const useLibrary = !!emailUseLibraryEl?.checked;
  setEmailBusy(
    true,
    useLibrary
      ? "Drafting a reply and retrieving the controlling USACE policy — this takes longer..."
      : "Drafting a reply..."
  );
  try {
    const res = await apiFetch("/email/draft-reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        instructions: emailInstructionsEl?.value || "",
        tone: emailToneEl?.value || "professional",
        use_library: useLibrary,
        session_id: currentSessionId,
      }),
    });
    const data = await readJsonResponse(res);
    if (!res.ok) {
      throw new Error(emailErrorText(data.detail, "Could not draft a reply."));
    }
    emailResultsEl.innerHTML = renderEmailMeta(data.email) + renderEmailDraft(data.draft);
    wireEmailCopyButton();
    setEmailStatus("");
  } catch (err) {
    setEmailStatus(err.message || "Could not draft a reply.", "error");
  } finally {
    setEmailBusy(false);
  }
}

async function handleEmailMsgUpload(input) {
  const file = input.files?.[0];
  if (!file) return;

  setEmailBusy(true, `Reading ${file.name}...`);
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await apiFetch("/email/parse-msg", { method: "POST", body: form });
    const data = await readJsonResponse(res);
    if (!res.ok) {
      throw new Error(emailErrorText(data.detail, "Could not read that .msg file."));
    }
    emailTextEl.value = data.text || "";
    emailResultsEl.innerHTML = renderEmailMeta(data.email);
    const note = [`Loaded ${file.name}.`, emailRedactionNote(data.email)].filter(Boolean).join(" ");
    emailInputNoteEl.hidden = false;
    emailInputNoteEl.textContent = `${note} Choose "Summarize & triage" or "Draft a reply".`;
    setEmailStatus("");
  } catch (err) {
    setEmailStatus(err.message || "Could not read that .msg file.", "error");
  } finally {
    setEmailBusy(false);
    input.value = "";
  }
}

/* ---------- Autonomous sweep ---------- */

const emailSweepBtn = document.getElementById("emailSweepBtn");
const emailSweepFilesInput = document.getElementById("emailSweepFiles");
const emailSweepWindowEl = document.getElementById("emailSweepWindow");
const emailSweepSourceEl = document.getElementById("emailSweepSource");
const emailSweepStatusEl = document.getElementById("emailSweepStatus");
const emailSweepProgressEl = document.getElementById("emailSweepProgress");
const emailSweepBarFillEl = document.getElementById("emailSweepBarFill");
const emailSweepProgressTextEl = document.getElementById("emailSweepProgressText");
const emailSweepDigestEl = document.getElementById("emailSweepDigest");
const emailSweepResultsEl = document.getElementById("emailSweepResults");
const emailModelNoticeEl = document.getElementById("emailModelNotice");

const SWEEP_POLL_MS = 2000;

// Artifact bodies (.eml/.ics/.md) stay in memory and are saved by the browser on
// demand. Nothing is written to disk on the server, so email content lives only
// as long as this page does.
let sweepArtifacts = [];
let sweepConfig = { windowHours: 72, canReadMailbox: false, autostart: false, maxMessages: 40 };
let sweepRunning = false;
let sweepAutostarted = false;

function setSweepStatus(message, kind = "info") {
  if (!emailSweepStatusEl) return;
  if (!message) {
    emailSweepStatusEl.hidden = true;
    emailSweepStatusEl.textContent = "";
    return;
  }
  emailSweepStatusEl.hidden = false;
  emailSweepStatusEl.className = `email-status email-status-${kind}`;
  emailSweepStatusEl.textContent = message;
}

function setSweepRunning(running) {
  sweepRunning = running;
  if (emailSweepBtn) {
    emailSweepBtn.disabled = running || !sweepConfig.canReadMailbox;
    emailSweepBtn.textContent = running ? "Sweeping…" : "Run the sweep";
  }
  if (emailSweepFilesInput) emailSweepFilesInput.disabled = running;
  if (emailSweepProgressEl) emailSweepProgressEl.hidden = !running;
}

function renderSweepProgress(job) {
  if (!emailSweepProgressEl) return;
  const total = job.files_total || 0;
  const done = job.files_done || 0;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  emailSweepProgressEl.hidden = false;
  if (emailSweepBarFillEl) emailSweepBarFillEl.style.width = `${pct}%`;
  if (emailSweepProgressTextEl) {
    const detail = job.filename ? ` — ${job.filename}` : "";
    emailSweepProgressTextEl.textContent = `${job.message || "Working…"}${detail}`;
  }
}

// A .ics file is only useful to Outlook, so there is nothing worth copying to the
// clipboard for one — the reply and the note are text a person actually pastes.
const SWEEP_ARTIFACT_ACTIONS = {
  reply: { save: "Open in Outlook", copy: "Copy reply" },
  invite: { save: "Add to calendar", copy: "" },
  note: { save: "Save note", copy: "Copy note" },
};

function sweepArtifactButtons(artifacts, itemIndex) {
  const buttons = (artifacts || [])
    .map((artifact) => {
      const actions = SWEEP_ARTIFACT_ACTIONS[artifact.kind];
      if (!actions) return "";
      const index = sweepArtifacts.length;
      sweepArtifacts.push(artifact);
      const extension = (artifact.filename.match(/\.[a-z]+$/i) || [""])[0];
      return (
        `<span class="email-artifact-pair">` +
        `<button type="button" class="email-artifact-btn" data-sweep-download="${index}" ` +
        `title="${escapeHtml(artifact.filename)}">${escapeHtml(actions.save)}` +
        `<span class="email-artifact-ext">${escapeHtml(extension)}</span></button>` +
        (actions.copy
          ? `<button type="button" class="email-artifact-btn email-artifact-btn-quiet" ` +
            `data-sweep-copy="${index}">${escapeHtml(actions.copy)}</button>`
          : "") +
        `</span>`
      );
    })
    .filter(Boolean)
    .join("");
  return buttons ? `<div class="email-artifact-row" data-item="${itemIndex}">${buttons}</div>` : "";
}

function renderSweepMeeting(meeting) {
  if (!meeting) return "";
  const when = meeting.start
    ? new Date(meeting.start).toLocaleString([], {
        weekday: "short",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "";
  const rows = [
    ["When", when || "no time proposed in the email — you pick one"],
    ["Where", meeting.location],
    ["Invite", (meeting.attendees || []).join(", ")],
  ].filter(([, value]) => value);

  return (
    `<div class="email-block email-block-meeting">` +
    `<h4 class="email-sub">${escapeHtml(
      meeting.attendees && meeting.attendees.length ? "Meeting invite" : "Appointment"
    )}: ${escapeHtml(meeting.title || "")}</h4>` +
    rows
      .map(
        ([label, value]) =>
          `<div class="email-meta-row"><span class="email-meta-label">${escapeHtml(label)}</span>` +
          `<span class="email-meta-value">${escapeHtml(value)}</span></div>`
      )
      .join("") +
    (meeting.reason ? `<p class="email-reason">${escapeHtml(meeting.reason)}</p>` : "") +
    renderEmailList("Agenda", meeting.agenda) +
    (meeting.time_known
      ? ""
      : `<p class="email-draft-warning">No specific time was stated, so there is no calendar file to add — schedule it yourself using the agenda above.</p>`) +
    `</div>`
  );
}

function renderSweepDraft(draft) {
  if (!draft) return "";
  return (
    `<div class="email-block email-block-draft">` +
    `<h4 class="email-sub">Draft reply</h4>` +
    (draft.subject ? `<div class="email-draft-subject">${escapeHtml(draft.subject)}</div>` : "") +
    `<div class="email-draft-body">${escapeHtml(draft.body || "")}</div>` +
    (draft.library_error ? `<p class="email-draft-warning">${escapeHtml(draft.library_error)}</p>` : "") +
    `</div>`
  );
}

function renderSweepNote(note) {
  if (!note) return "";
  return (
    `<div class="email-block email-block-note">` +
    `<h4 class="email-sub">Note for the record: ${escapeHtml(note.title || "")}</h4>` +
    (note.body ? `<p class="email-summary">${escapeHtml(note.body)}</p>` : "") +
    renderEmailList("Decisions", note.decisions) +
    renderEmailList("Follow up on", note.followups) +
    `</div>`
  );
}

function sweepReceivedLabel(info) {
  if (info.received_at) {
    const when = new Date(info.received_at);
    if (!Number.isNaN(when.getTime())) {
      return when.toLocaleString([], {
        weekday: "short",
        hour: "numeric",
        minute: "2-digit",
      });
    }
  }
  return info.sent || "";
}

function renderSweepItem(item, index) {
  const info = item.email || {};
  const analysis = item.analysis || {};
  const priority = (analysis.priority || "medium").toLowerCase();
  const subject = info.subject || "(no subject)";

  if (item.error && !analysis.summary) {
    return (
      `<article class="email-item email-item-failed">` +
      `<h4 class="email-item-subject">${escapeHtml(subject)}</h4>` +
      `<p class="email-draft-warning">${escapeHtml(item.error)}</p>` +
      `</article>`
    );
  }

  const badges = [
    `<span class="email-badge email-badge-${escapeHtml(priority)}">${escapeHtml(priority)}</span>`,
    analysis.category ? `<span class="email-badge">${escapeHtml(analysis.category)}</span>` : "",
    analysis.reply_needed ? `<span class="email-badge email-badge-reply">reply needed</span>` : "",
    item.meeting ? `<span class="email-badge email-badge-meeting">meeting</span>` : "",
  ]
    .filter(Boolean)
    .join("");

  // High-priority mail opens expanded; everything else stays collapsed so the
  // window reads as a list you scan rather than a wall of text.
  const open = priority === "high" ? " open" : "";
  return (
    `<details class="email-item email-item-${escapeHtml(priority)}"${open}>` +
    `<summary class="email-item-head">` +
    `<span class="email-item-main">` +
    `<span class="email-item-subject">${escapeHtml(subject)}</span>` +
    `<span class="email-item-from">${escapeHtml(info.sender || "unknown sender")}` +
    (sweepReceivedLabel(info) ? ` · ${escapeHtml(sweepReceivedLabel(info))}` : "") +
    `</span></span>` +
    `<span class="email-item-badges">${badges}</span>` +
    `</summary>` +
    `<div class="email-item-body">` +
    (analysis.summary ? `<p class="email-summary">${escapeHtml(analysis.summary)}</p>` : "") +
    (analysis.priority_reason ? `<p class="email-reason">${escapeHtml(analysis.priority_reason)}</p>` : "") +
    renderEmailActionItems(analysis.action_items) +
    renderEmailList("Deadlines", analysis.deadlines) +
    renderEmailList("Open questions", analysis.open_questions) +
    (analysis.suggested_next_step
      ? `<h4 class="email-sub">Suggested next step</h4><p class="email-next">${escapeHtml(
          analysis.suggested_next_step
        )}</p>`
      : "") +
    renderSweepDraft(item.draft) +
    renderSweepMeeting(item.meeting) +
    renderSweepNote(item.note) +
    sweepArtifactButtons(item.artifacts, index) +
    (item.error ? `<p class="email-draft-warning">${escapeHtml(item.error)}</p>` : "") +
    (emailRedactionNote(info) ? `<p class="email-redaction">${escapeHtml(emailRedactionNote(info))}</p>` : "") +
    `</div></details>`
  );
}

function renderSweepDigest(report) {
  const digest = report.digest || {};
  const counts = digest.priority_counts || {};
  const tiles = [
    ["High priority", counts.high || 0, "high"],
    ["Replies drafted", digest.replies_drafted || 0, ""],
    ["Invites ready", digest.invites_built || 0, ""],
    ["Notes written", digest.notes_written || 0, ""],
  ];

  const deadlines = (digest.deadlines || [])
    .map(
      (d) =>
        `<li><strong>${escapeHtml(d.deadline)}</strong> — ${escapeHtml(d.subject)}</li>`
    )
    .join("");
  const owed = (digest.needs_reply || [])
    .map((r) => `<li>${escapeHtml(r.subject)} — ${escapeHtml(r.sender || "")}</li>`)
    .join("");

  return (
    `<div class="email-digest-tiles">` +
    tiles
      .map(
        ([label, value, tone]) =>
          `<div class="email-digest-tile${tone ? ` email-digest-tile-${tone}` : ""}">` +
          `<span class="email-digest-value">${value}</span>` +
          `<span class="email-digest-label">${escapeHtml(label)}</span></div>`
      )
      .join("") +
    `</div>` +
    `<p class="email-digest-line">Read ${report.messages_analyzed} message(s) received in the last ` +
    `${report.window_hours} hours.` +
    (report.messages_failed ? ` ${report.messages_failed} could not be analyzed.` : "") +
    `</p>` +
    (deadlines ? `<div class="email-digest-col"><h4 class="email-sub">Dates across the window</h4><ul class="email-ul">${deadlines}</ul></div>` : "") +
    (owed ? `<div class="email-digest-col"><h4 class="email-sub">Waiting on a reply from you</h4><ul class="email-ul">${owed}</ul></div>` : "") +
    ((report.warnings || []).length
      ? `<ul class="email-ul email-digest-warnings">${report.warnings
          .map((w) => `<li>${escapeHtml(w)}</li>`)
          .join("")}</ul>`
      : "")
  );
}

function renderSweepReport(report) {
  sweepArtifacts = [];
  if (!report || report.empty || !(report.items || []).length) {
    if (emailSweepDigestEl) emailSweepDigestEl.hidden = true;
    if (emailSweepResultsEl) emailSweepResultsEl.innerHTML = "";
    return;
  }
  if (emailSweepResultsEl) {
    emailSweepResultsEl.innerHTML = report.items
      .map((item, index) => renderSweepItem(item, index))
      .join("");
  }
  if (emailSweepDigestEl) {
    emailSweepDigestEl.hidden = false;
    emailSweepDigestEl.innerHTML = renderSweepDigest(report);
  }
}

function downloadSweepArtifact(artifact) {
  const blob = new Blob([artifact.content], { type: `${artifact.mime || "text/plain"};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = artifact.filename || "project-spk.txt";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function copySweepArtifact(artifact, button) {
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(artifact.content || "");
    button.textContent = "Copied";
  } catch {
    button.textContent = "Copy blocked";
  }
  setTimeout(() => (button.textContent = original), 1500);
}

function sweepRequirementsText(detail) {
  if (typeof detail === "string") return detail;
  if (!detail || typeof detail !== "object") return "";
  const parts = [detail.message || ""];
  (detail.requirements || []).forEach((req) => parts.push(`• ${req}`));
  (detail.warnings || []).forEach((warn) => parts.push(`• ${warn}`));
  return parts.filter(Boolean).join("\n");
}

async function pollSweepJob(jobId) {
  while (sweepRunning) {
    await new Promise((resolve) => setTimeout(resolve, SWEEP_POLL_MS));
    let job;
    try {
      const res = await apiFetch(`/jobs/${encodeURIComponent(jobId)}`);
      job = await readJsonResponse(res);
      if (!res.ok) throw new Error(emailErrorText(job.detail, "Lost track of the sweep."));
    } catch (err) {
      setSweepStatus(err.message || "Lost track of the sweep.", "error");
      return;
    }

    renderSweepProgress(job);
    if (job.status === "done") {
      renderSweepReport(job.sweep_report);
      setSweepStatus(job.message || "Sweep complete.", "ok");
      return;
    }
    if (job.status === "error") {
      const requirements = (job.sweep_report && job.sweep_report.requirements) || [];
      setSweepStatus(
        [job.message, ...requirements.map((r) => `• ${r}`)].filter(Boolean).join("\n"),
        "error"
      );
      return;
    }
  }
}

async function startSweep(url, body) {
  if (sweepRunning) return;
  setSweepStatus("");
  if (emailSweepBarFillEl) emailSweepBarFillEl.style.width = "0%";
  if (emailSweepProgressTextEl) emailSweepProgressTextEl.textContent = "Collecting recent email…";
  setSweepRunning(true);
  try {
    const res = await apiFetch(url, body);
    const data = await readJsonResponse(res);
    if (!res.ok) {
      throw new Error(sweepRequirementsText(data.detail) || "Could not start the sweep.");
    }
    await pollSweepJob(data.job_id);
  } catch (err) {
    setSweepStatus(err.message || "Could not start the sweep.", "error");
  } finally {
    setSweepRunning(false);
  }
}

function runMailboxSweep() {
  return startSweep("/email/sweep", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      use_library: !!emailUseLibraryEl?.checked,
      tone: emailToneEl?.value || "professional",
      session_id: currentSessionId,
    }),
  });
}

function runUploadSweep(files) {
  const form = new FormData();
  Array.from(files)
    .slice(0, sweepConfig.maxMessages)
    .forEach((file) => form.append("files", file));
  form.append("tone", emailToneEl?.value || "professional");
  form.append("use_library", String(!!emailUseLibraryEl?.checked));
  if (currentSessionId) form.append("session_id", currentSessionId);
  return startSweep("/email/sweep/upload", { method: "POST", body: form });
}

function describeSweepSource(data) {
  const sweep = data.sweep || {};
  const mailbox = data.mailbox || {};
  if (!emailSweepSourceEl) return;
  emailSweepSourceEl.hidden = false;
  if (sweep.can_read_mailbox) {
    // Kept short on purpose: the Mailbox connection block above already carries
    // the full description, so this only names the source.
    const where =
      mailbox.connector === "local_folder"
        ? `exported email files in <code>${escapeHtml(mailbox.folder || "")}</code>`
        : "your Outlook mailbox";
    emailSweepSourceEl.innerHTML = `<strong>Reading from:</strong> ${where}`;
    return;
  }
  emailSweepSourceEl.innerHTML =
    `<strong>No mailbox connection.</strong> Project SPK cannot reach your mail on its own yet, ` +
    `so pick the messages yourself: in Outlook, select the last few days, drag them into a ` +
    `folder on your desktop to save them as <code>.msg</code> files, then choose them with ` +
    `"Choose email files". Everything else works the same.`;
}

function describeModelEndpoint(model) {
  if (!emailModelNoticeEl || !model) return;
  emailModelNoticeEl.hidden = false;
  if (model.public_openai) {
    emailModelNoticeEl.className = "email-model-notice email-model-notice-warn";
    emailModelNoticeEl.innerHTML =
      `<strong>Email is processed by ${escapeHtml(String(model.endpoint_host))}</strong> ` +
      `(model ${escapeHtml(String(model.model))}), a commercial API on the public internet. ` +
      `Keep CUI and PII out of it until a self-hosted model is configured.`;
  } else {
    emailModelNoticeEl.className = "email-model-notice email-model-notice-ok";
    emailModelNoticeEl.innerHTML =
      `<strong>Email is processed by ${escapeHtml(String(model.endpoint_host))}</strong> ` +
      `(model ${escapeHtml(String(model.model))}), a self-hosted endpoint. Content does not ` +
      `go to a commercial AI service.`;
  }
}

function applySweepConfig(data) {
  const sweep = data.sweep || {};
  sweepConfig = {
    windowHours: sweep.window_hours || 72,
    canReadMailbox: !!sweep.can_read_mailbox,
    autostart: !!sweep.autostart,
    maxMessages: sweep.max_messages || 40,
  };
  if (emailSweepWindowEl) emailSweepWindowEl.textContent = String(sweepConfig.windowHours);
  if (emailSweepBtn) {
    emailSweepBtn.disabled = !sweepConfig.canReadMailbox;
    emailSweepBtn.title = sweepConfig.canReadMailbox
      ? `Read everything received in the last ${sweepConfig.windowHours} hours`
      : "Project SPK has no mail source it can read on its own — choose email files instead";
  }
  if (sweep.enabled === false) {
    document.getElementById("emailSweep")?.setAttribute("hidden", "hidden");
  }
  describeSweepSource(data);
  describeModelEndpoint(data.model);
}

/** Start a sweep on first open, when the source needs nothing from the user. */
function maybeAutostartSweep() {
  if (sweepAutostarted || sweepRunning) return;
  if (!sweepConfig.autostart || !sweepConfig.canReadMailbox) return;
  sweepAutostarted = true;
  runMailboxSweep();
}

function initEmailSweep() {
  emailSweepBtn?.addEventListener("click", () => runMailboxSweep());
  emailSweepFilesInput?.addEventListener("change", () => {
    const files = emailSweepFilesInput.files;
    if (files && files.length) runUploadSweep(files);
    emailSweepFilesInput.value = "";
  });

  emailSweepResultsEl?.addEventListener("click", (event) => {
    const downloadBtn = event.target.closest("[data-sweep-download]");
    if (downloadBtn) {
      const artifact = sweepArtifacts[Number(downloadBtn.dataset.sweepDownload)];
      if (artifact) downloadSweepArtifact(artifact);
      return;
    }
    const copyBtn = event.target.closest("[data-sweep-copy]");
    if (copyBtn) {
      const artifact = sweepArtifacts[Number(copyBtn.dataset.sweepCopy)];
      if (artifact) copySweepArtifact(artifact, copyBtn);
    }
  });
}

function initEmailAssistant() {
  emailAnalyzeBtn?.addEventListener("click", runEmailAnalyze);
  emailDraftBtn?.addEventListener("click", runEmailDraft);
  emailMsgInput?.addEventListener("change", () => handleEmailMsgUpload(emailMsgInput));
  emailClearBtn?.addEventListener("click", () => {
    if (emailTextEl) emailTextEl.value = "";
    if (emailInstructionsEl) emailInstructionsEl.value = "";
    if (emailResultsEl) emailResultsEl.innerHTML = "";
    if (emailInputNoteEl) emailInputNoteEl.hidden = true;
    setEmailStatus("");
    emailTextEl?.focus();
  });
  initEmailSweep();
}

/* ---------- Init ---------- */

async function initApp() {
  await loadLimits();
  initEmailAssistant();
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

function showLoginError(message) {
  loginError.textContent = message || "Enter a valid @usace.army.mil email address.";
  loginError.hidden = false;
}

loginEmail.addEventListener("input", updateLoginButton);
loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = loginEmail.value.trim();
  if (!isValidUsaceEmail(email)) {
    showLoginError("Enter a valid @usace.army.mil email address.");
    loginEmail.focus();
    return;
  }
  loginSubmit.disabled = true;
  try {
    const res = await fetch("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await res.json();
    if (!res.ok) {
      showLoginError(data.detail || "Sign-in failed. Try again.");
      loginEmail.focus();
      return;
    }
    localStorage.setItem(USER_EMAIL_KEY, data.email);
    localStorage.setItem(AUTH_TOKEN_KEY, data.token);
    localStorage.setItem(AUTH_EXPIRES_KEY, String(data.expires_at));
    enterApp();
  } catch {
    showLoginError("Could not reach the server. Try again.");
  } finally {
    loginSubmit.disabled = false;
    updateLoginButton();
  }
});

// First screen: require a valid, unexpired sign-in token (24-hour sessions).
if (authToken()) {
  enterApp();
} else {
  clearAuthToken();
  loginScreen.hidden = false;
  updateLoginButton();
  loginEmail.focus();
}
