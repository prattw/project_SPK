// Regression tests for the Coding Agent UI wiring in static/app.js:
// toggle mutual-exclusivity, the /health -> toggle-visibility wiring, and
// rendering generated files as downloadable chips under an assistant answer.
//
// Run with:  cd tests/js && npm install && npm test

const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(REPO_ROOT, "static", "index.html"), "utf8");

const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8000/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
});
const { window } = dom;

window.fetch = () => Promise.reject(new Error("no network in test harness"));
window.navigator.clipboard = { writeText: () => Promise.resolve() };
// jsdom's window.localStorage is a real, origin-scoped Storage instance with a
// getter-only accessor (assigning a plain stub object to it silently no-ops),
// so use it directly rather than trying to replace it.

const appJs = fs.readFileSync(path.join(REPO_ROOT, "static", "app.js"), "utf8");
try {
  window.eval(appJs);
} catch (err) {
  console.error("FAILED to load app.js in jsdom:", err);
  process.exit(1);
}

const { document } = window;
let failures = 0;

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    failures += 1;
  } else {
    console.log("OK:", msg);
  }
}

// --- Checking "Agent mode" turns off "Coding agent" and vice versa.
const agentToggle = document.getElementById("agentToggle");
const codingAgentToggle = document.getElementById("codingAgentToggle");
assert(!!agentToggle && !!codingAgentToggle, "both toggle checkboxes exist in index.html");

agentToggle.checked = true;
agentToggle.dispatchEvent(new window.Event("change"));
assert(agentToggle.checked === true, "agent toggle stays checked after its own change event");
codingAgentToggle.checked = true;
codingAgentToggle.dispatchEvent(new window.Event("change"));
assert(codingAgentToggle.checked === true, "coding agent toggle checked");
assert(agentToggle.checked === false, "checking coding agent unchecks agent mode");

agentToggle.checked = true;
agentToggle.dispatchEvent(new window.Event("change"));
assert(codingAgentToggle.checked === false, "checking agent mode unchecks coding agent");

// --- No token in storage: downloads/agent-code-files links pass through unchanged.
assert(
  window.withToken("/agent/code/files/job1/x.py") === "/agent/code/files/job1/x.py",
  "withToken leaves URL unchanged with no stored token"
);

// --- With a valid (non-expired) token, withToken appends it only to
// download-style URLs (/download/... and /agent/code/files/...), not others.
window.localStorage.setItem("spk_auth_token", "tok123");
window.localStorage.setItem("spk_auth_expires", String(Math.floor(Date.now() / 1000) + 3600));
assert(
  window.withToken("/agent/code/files/job1/x.py") === "/agent/code/files/job1/x.py?token=tok123",
  "withToken appends token to /agent/code/files/ URLs"
);
assert(
  window.withToken("/download/report.docx") === "/download/report.docx?token=tok123",
  "withToken still appends token to /download/ URLs"
);
assert(
  window.withToken("/some/other/path") === "/some/other/path",
  "withToken leaves non-download URLs unchanged even with a token"
);

// --- renderMessage renders generated coding-agent files as download chips.
// renderMessage appends straight to the real #messages element; read it back from there.
window.renderMessage(
  "assistant",
  "Wrote fizzbuzz.py — run it with `python3 fizzbuzz.py`.",
  [],
  [],
  [],
  [{ path: "fizzbuzz.py", bytes: 123 }, { path: "src/util.py", bytes: 50 }],
  "job-abc"
);
const messagesEl = document.getElementById("messages");
const filesWrap = messagesEl.querySelector(".agent-files");
assert(!!filesWrap, "an .agent-files block is rendered when files + jobId are present");
const chips = filesWrap ? filesWrap.querySelectorAll(".agent-file-chip") : [];
assert(chips.length === 2, `two file chips rendered (got ${chips.length})`);
assert(
  chips[0]?.getAttribute("href") === "/agent/code/files/job-abc/fizzbuzz.py?token=tok123",
  `first chip href is correct (got ${chips[0]?.getAttribute("href")})`
);
assert(
  chips[1]?.getAttribute("href") === "/agent/code/files/job-abc/src/util.py?token=tok123",
  `nested-path chip href preserves the slash (got ${chips[1]?.getAttribute("href")})`
);
assert(chips[0]?.getAttribute("download") === "fizzbuzz.py", "chip has a download attribute");

// --- A plain agent-mode answer (no files/jobId) renders no file chips.
window.renderMessage("assistant", "Just an answer, no files.", [], [], []);
assert(
  messagesEl.querySelectorAll(".agent-files").length === 1,
  "no extra .agent-files block added when files are absent"
);

// --- Coding-agent tool names get a friendly label in the step trace, not a
// raw function-name fallback (AGENT_TOOL_LABELS is a top-level `const`, so it
// isn't itself a window property — exercise it indirectly via renderAgentSteps).
const codingSteps = ["write_file", "read_file", "list_files", "run_command"].map((tool) => ({
  tool,
  summary: "ok",
  ok: true,
}));
const stepsEl = window.renderAgentSteps(codingSteps);
const stepTexts = Array.from(stepsEl.querySelectorAll("li")).map((li) => li.textContent);
for (const tool of ["write_file", "read_file", "list_files", "run_command"]) {
  assert(
    !stepTexts.some((t) => t.startsWith(`${tool} —`)),
    `${tool} has a friendly AGENT_TOOL_LABELS entry (not the raw tool name)`
  );
}

if (failures > 0) {
  console.error(`\n${failures} TEST(S) FAILED`);
  process.exit(1);
}
console.log("\nALL TESTS PASSED");
