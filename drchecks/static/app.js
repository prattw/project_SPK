const list = document.querySelector("#comment-list");
const importStatus = document.querySelector("#import-status");
const draftStatus = document.querySelector("#draft-status");
const publications = document.querySelector("#publications");
let selectedId = null;

function field(id) {
  return document.querySelector("#" + id);
}

function fill(record, pubs) {
  selectedId = record && record.id !== "new" ? record.id : null;
  field("discipline").value = record?.discipline || "";
  field("document").value = record?.document || "";
  field("section").value = record?.section || "";
  field("status").value = record?.status || "";
  field("comment").value = record?.comment || "";
  field("response").value = record?.response || "";
  field("backcheck").value = record?.backcheck || "";
  field("note").value = "";
  field("draft").value = "";
  showPublications(pubs || []);
}

function showPublications(items) {
  publications.innerHTML = "";
  for (const item of items) {
    const row = document.createElement("p");
    row.className = "pub " + (item.status || "");
    row.textContent = item.detail;
    publications.appendChild(row);
  }
}

async function refreshList() {
  const response = await fetch("/comments");
  const data = await response.json();
  list.innerHTML = "";
  for (const record of data.comments) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = (record.id || "comment") + " — " + (record.comment || "").slice(0, 80);
    if (record.id === selectedId) button.className = "active";
    button.addEventListener("click", () => openComment(record.id));
    item.appendChild(button);
    list.appendChild(item);
  }
}

async function openComment(id) {
  const response = await fetch("/comments/" + encodeURIComponent(id));
  const data = await response.json();
  fill(data.comment, data.publications);
  await refreshList();
}

document.querySelector("#import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = field("xml-file").files[0];
  if (!file) {
    importStatus.textContent = "Choose the XML export first.";
    return;
  }
  const body = new FormData();
  body.append("file", file);
  importStatus.textContent = "Importing…";
  const response = await fetch("/import", { method: "POST", body });
  const data = await response.json();
  if (!response.ok) {
    importStatus.textContent = data.detail || "Import failed.";
    return;
  }
  importStatus.textContent = "Imported " + data.imported + " comments from " + data.source_file + ".";
  await refreshList();
  if (data.comments && data.comments[0]) await openComment(data.comments[0].id);
});

document.querySelector("#new-comment").addEventListener("click", () => {
  selectedId = null;
  fill({ id: "new", status: "New" }, []);
  document.querySelectorAll("#comment-list button").forEach((button) => {
    button.className = "";
  });
});

document.querySelectorAll("[data-kind]").forEach((button) => {
  button.addEventListener("click", async () => {
    const kind = button.dataset.kind;
    const payload = {
      kind,
      note: field("note").value,
      discipline: field("discipline").value,
      document: field("document").value,
      section: field("section").value,
      comment: field("comment").value,
    };
    draftStatus.textContent = "Drafting on this Mac…";
    const url = selectedId ? "/comments/" + encodeURIComponent(selectedId) + "/draft" : "/comments/new";
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(kind === "comment" && !selectedId ? payload : payload),
    });
    const data = await response.json();
    if (!response.ok) {
      draftStatus.textContent = data.detail || "Draft failed.";
      return;
    }
    field("draft").value = data.draft;
    showPublications(data.publications || []);
    draftStatus.textContent = "Paste this into ProjNet. It is a draft, not a filing.";
  });
});

document.querySelector("#copy").addEventListener("click", async () => {
  const text = field("draft").value;
  if (!text) return;
  await navigator.clipboard.writeText(text);
  draftStatus.textContent = "Copied.";
});

refreshList();
