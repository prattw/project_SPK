// Regression tests for the chat markdown/code-block renderer in
// static/app.js. These load the real app.js inside a jsdom window built from
// the real static/index.html, so they exercise exactly what ships — not a
// reimplementation.
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

// app.js's trailing decideInitialScreen() calls fetch("/health") on load;
// stub it so loading the script doesn't attempt a real network call.
window.fetch = () => Promise.reject(new Error("no network in test harness"));
window.navigator.clipboard = { writeText: () => Promise.resolve() };
window.localStorage = window.localStorage || {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};

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

// --- A fenced python code block renders as a real <pre><code> with
// Copy/Save buttons, and surrounding prose still renders as markdown.
const sample = [
  "Here is a script:",
  "",
  "```python",
  "def greet(name):",
  '    print(f"Hello, {name}!")',
  "",
  'greet("world")',
  "```",
  "",
  "Save it as `greet.py` and run `python greet.py`.",
].join("\n");

const container = document.createElement("div");
window.renderRichMarkdownInto(container, sample);

assert(container.querySelectorAll(".code-block").length === 1, "exactly one .code-block rendered");

const langLabel = container.querySelector(".code-block-lang");
assert(langLabel?.textContent === "python", `language label is 'python' (got '${langLabel?.textContent}')`);

const codeEl = container.querySelector(".code-block pre code");
const expectedCode = 'def greet(name):\n    print(f"Hello, {name}!")\n\ngreet("world")';
assert(
  codeEl?.textContent === expectedCode,
  `code content preserved exactly incl. indentation/blank line (got: ${JSON.stringify(codeEl?.textContent)})`
);

assert(!!container.querySelector(".code-copy-btn"), "Copy button present");
assert(!!container.querySelector(".code-download-btn"), "Save file button present");

const paragraphs = Array.from(container.querySelectorAll("p")).map((p) => p.textContent);
assert(paragraphs.some((p) => p.includes("Here is a script")), "leading prose paragraph rendered");
assert(
  paragraphs.some((p) => p.includes("Save it as") && p.includes("greet.py")),
  "trailing prose paragraph rendered"
);
assert(
  container.querySelectorAll("p code").length === 2,
  `both inline-code spans in the trailing paragraph rendered (found ${container.querySelectorAll("p code").length})`
);

// --- Language-to-extension mapping used by the Save button.
assert(window.codeFileExtension("python") === "py", "python -> py");
assert(window.codeFileExtension("bash") === "sh", "bash -> sh");
assert(window.codeFileExtension("powershell") === "ps1", "powershell -> ps1");
assert(window.codeFileExtension("") === "txt", "empty lang -> txt");
assert(window.codeFileExtension("sOmEwEiRdLang") === "someweirdlang", "unknown lang -> lowercased as-is");

// --- Multiple adjacent fenced blocks in one message.
const multi = "```js\nconsole.log(1);\n```\ntext between\n```bash\necho hi\n```";
const container2 = document.createElement("div");
window.renderRichMarkdownInto(container2, multi);
assert(
  container2.querySelectorAll(".code-block").length === 2,
  `two code blocks rendered from adjacent fences (got ${container2.querySelectorAll(".code-block").length})`
);

// --- Plain text with no code fences still renders exactly as before
// (regression guard on the pre-existing markdown renderer).
const plainContainer = document.createElement("div");
window.renderRichMarkdownInto(plainContainer, "# Heading\n\n- item one\n- item two\n\nSome **bold** text.");
assert(plainContainer.querySelector("h3")?.textContent === "Heading", "heading still renders");
assert(plainContainer.querySelectorAll("li").length === 2, "list items still render");
assert(plainContainer.querySelector("strong")?.textContent === "bold", "bold text still renders");
assert(plainContainer.querySelectorAll(".code-block").length === 0, "no stray code-block for plain text");

if (failures > 0) {
  console.error(`\n${failures} TEST(S) FAILED`);
  process.exit(1);
}
console.log("\nALL TESTS PASSED");
