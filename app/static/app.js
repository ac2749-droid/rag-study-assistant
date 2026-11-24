const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const MEMORY_TURNS = 6;   // how many earlier exchanges are sent along with each question

const ICONS = {
  file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6"],
  trash: ["M3 6h18", "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2", "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6", "M10 11v6", "M14 11v6"],
  plus: ["M12 5v14", "M5 12h14"],
  send: ["M12 19V5", "M5 12l7-7 7 7"],
  copy: ["M11 9h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2z", "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"],
  check: ["M20 6L9 17l-5-5"],
  book: ["M4 19.5A2.5 2.5 0 0 1 6.5 17H20", "M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"],
  briefcase: ["M4 7h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z", "M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"],
  menu: ["M3 6h18", "M3 12h18", "M3 18h18"],
  x: ["M18 6L6 18", "M6 6l12 12"],
  refresh: ["M3 12a9 9 0 0 1 15-6.7L21 8", "M21 3v5h-5", "M21 12a9 9 0 0 1-15 6.7L3 16", "M3 21v-5h5"],
};

let history = [];         // [{question, answer}] for this conversation; cleared by "New chat"
let docs = [];            // [{source, chunks, pages}] as reported by the server
let hasDocs = false;
let busy = false;
let uploading = false;
let removing = false;
let pendingRemove = null; // file name waiting for "Are you sure?"
let confirmAll = false;

/* ---------- helpers ---------- */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name, size = 18) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const d of ICONS[name]) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

function brandMark(size = 30) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 28 28");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const rect = document.createElementNS(SVG_NS, "rect");
  for (const [k, v] of Object.entries({ width: 28, height: 28, rx: 7, fill: "currentColor" })) rect.setAttribute(k, v);
  const path = document.createElementNS(SVG_NS, "path");
  for (const [k, v] of Object.entries({ d: "M9 9h10M9 14h10M9 19h6", stroke: "white", "stroke-width": 2, "stroke-linecap": "round" })) path.setAttribute(k, v);
  svg.append(rect, path);
  return svg;
}

function withIcon(button, name, label, size = 17) {
  button.replaceChildren(icon(name, size), document.createTextNode(label));
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...(options.headers || {}) };
  // Anything that changes data carries this header: a page on another website can't add it, which is
  // what stops other sites from sending requests on your behalf.
  if (method !== "GET" && method !== "HEAD") headers["X-Requested-With"] = "XMLHttpRequest";
  let response;
  try {
    response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  } catch {
    throw new Error("Can't reach the server. Is it still running?");
  }
  let data = null;
  try { data = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/")) sessionEnded();
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : "Something went wrong. Please check your input and try again.");
  }
  return data;
}

function toast(message, kind = "success") {
  const box = el("div", "toast" + (kind === "error" ? " error" : ""));
  box.append(el("div", "msg-text", message));
  const close = el("button", "icon-btn");
  close.type = "button";
  close.setAttribute("aria-label", "Dismiss");
  close.classList.add("toast-close");
  close.append(icon("x", 14));
  const dismiss = () => box.remove();
  close.addEventListener("click", dismiss);
  box.append(close);
  $("toasts").append(box);
  setTimeout(dismiss, kind === "error" ? 9000 : 4500);
}

const isMobile = () => window.matchMedia("(max-width: 860px)").matches;
function setDrawer(open) {
  $("sidebar").classList.toggle("open", open);
  $("scrim").classList.toggle("show", open);
}

function scrollToEnd() {
  const box = $("chatScroll");
  box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
}

function updateControls() {
  $("send").disabled = busy || !hasDocs || !$("question").value.trim();
  $("question").placeholder = hasDocs ? "Ask a question about your documents..." : "Add a document to start asking questions";
  $("question").disabled = !hasDocs;
  $("addBtn").disabled = uploading;
  for (const button of document.querySelectorAll(".example")) button.disabled = busy;
}

/* ---------- documents ---------- */
function renderDocs() {
  $("docCount").textContent = hasDocs ? String(docs.length) : "";
  $("emptyDocs").hidden = hasDocs || uploading;
  const list = $("docs");
  list.replaceChildren();
  for (const doc of docs) {
    const item = el("li", "doc");
    if (pendingRemove === doc.source) {
      item.classList.add("confirm");
      const info = el("div", "doc-info");
      info.append(el("div", "doc-name", "Remove this file?"), el("div", "doc-meta ellipsis", doc.source));
      const yes = el("button", "btn danger sm", "Remove");
      yes.type = "button";
      yes.disabled = removing;
      yes.addEventListener("click", () => removeDoc(doc.source));
      const no = el("button", "btn sm", "Cancel");
      no.type = "button";
      no.disabled = removing;
      no.addEventListener("click", () => { pendingRemove = null; renderDocs(); });
      item.append(info, yes, no);
    } else {
      const badge = el("span", "file-icon");
      badge.append(icon("file", 17));
      const info = el("div", "doc-info");
      const name = el("div", "doc-name", doc.source);
      name.title = doc.source;
      const meta = el("div", "doc-meta", doc.pages + (doc.pages === 1 ? " page" : " pages"));
      if (!doc.complete) {  // indexing stopped early (for example a daily limit): only part is searchable
        meta.textContent = "Incomplete: " + doc.chunks.toLocaleString() + " of " + doc.expected_chunks.toLocaleString() + " sections";
        meta.classList.add("warn");
        meta.title = "Only part of this file was indexed, so answers may miss things. Add the same file again to finish it.";
      }
      info.append(name, meta);
      const trash = el("button", "icon-btn danger");
      trash.type = "button";
      trash.title = "Remove";
      trash.setAttribute("aria-label", "Remove " + doc.source);
      trash.disabled = removing;
      trash.append(icon("trash", 16));
      trash.addEventListener("click", () => { pendingRemove = doc.source; confirmAll = false; renderDocs(); renderRemoveAll(); });
      item.append(badge, info, trash);
    }
    list.append(item);
  }
}

function renderRemoveAll() {
  const row = $("removeAllRow");
  row.replaceChildren();
  if (!hasDocs) return;
  if (confirmAll) {
    const box = el("div", "remove-all-confirm");
    box.append(el("span", null, "Remove all " + docs.length + (docs.length === 1 ? " document?" : " documents?")));
    const yes = el("button", "btn danger sm", "Remove all");
    yes.type = "button";
    yes.disabled = removing;
    yes.addEventListener("click", removeAll);
    const no = el("button", "btn sm", "Cancel");
    no.type = "button";
    no.disabled = removing;
    no.addEventListener("click", () => { confirmAll = false; renderRemoveAll(); });
    box.append(yes, no);
    row.append(box);
  } else {
    const link = el("button", "link-btn");
    link.type = "button";
    link.append(icon("trash", 15), document.createTextNode("Remove all documents"));
    link.addEventListener("click", () => { confirmAll = true; pendingRemove = null; renderDocs(); renderRemoveAll(); });
    row.append(link);
  }
}

async function refreshDocs() {
  docs = await api("/documents");
  hasDocs = docs.length > 0;
  if (pendingRemove && !docs.some((d) => d.source === pendingRemove)) pendingRemove = null;
  renderDocs();
  renderRemoveAll();
  renderWelcome();
  updateControls();
}

async function removeDoc(name) {
  removing = true;
  renderDocs();
  try {
    await api("/documents?source=" + encodeURIComponent(name), { method: "DELETE" });
    toast("Removed “" + name + "”");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    removing = false;
    pendingRemove = null;
    try { await refreshDocs(); } catch (err) { toast(err.message, "error"); }
  }
}

async function removeAll() {
  removing = true;
  renderRemoveAll();
  try {
    const result = await api("/documents/all", { method: "DELETE" });
    toast(result.removed_chunks ? "All documents removed" : "There was nothing to remove");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    removing = false;
    confirmAll = false;
    try { await refreshDocs(); } catch (err) { toast(err.message, "error"); }
  }
}

/* Documents are indexed in the background (big ones take minutes), so the page shows a progress bar per
   file and keeps checking until each one is finished. It also picks up jobs after a page refresh. */
const tracked = new Map();   // job id -> file name, for the jobs this page is watching
let activeJobs = [];         // the latest known state of those jobs
let reading = null;          // name of the file being read by the server right now
let pollTimer = null;

function formatEta(seconds) {
  if (seconds == null) return "";
  return seconds < 60 ? " · under a minute left" : " · about " + Math.ceil(seconds / 60) + " min left";
}

function renderJobs() {
  const box = $("jobs");
  box.replaceChildren();
  const card = (name, text, percent) => {
    const item = el("div", "job");
    const title = el("div", "job-name", name);
    title.title = name;
    const bar = el("div", "bar" + (percent == null ? " waiting" : ""));
    const fill = el("div");
    if (percent != null) fill.style.width = percent + "%";
    bar.append(fill);
    item.append(title, el("div", "job-meta", text), bar);
    box.append(item);
  };
  if (reading) card(reading, "Reading the file...", null);
  for (const job of activeJobs) {
    if (job.status === "queued") card(job.source, "Waiting for earlier files...", null);
    else if (job.done === 0) card(job.source, "Starting to index " + job.total.toLocaleString() + " sections...", null);
    else card(job.source, "Indexing " + job.done.toLocaleString() + " of " + job.total.toLocaleString() +
      " sections" + formatEta(job.eta_seconds), Math.round((job.done / job.total) * 100));
  }
}

function schedulePoll(delay) {
  if (pollTimer === null) pollTimer = setTimeout(pollJobs, delay);
}

async function pollJobs() {
  pollTimer = null;
  if (!tracked.size) { activeJobs = []; renderJobs(); return; }
  let all;
  try {
    all = await api("/jobs");
  } catch {
    schedulePoll(4000);  // a hiccup talking to the server: keep trying
    return;
  }
  const byId = new Map(all.map((job) => [job.id, job]));
  let finished = false;
  for (const [id, name] of [...tracked]) {
    const job = byId.get(id);
    if (!job) {
      toast("“" + name + "” was interrupted. Please add it again.", "error");
      tracked.delete(id);
    } else if (job.status === "done") {
      const skipped = job.empty_pages.length ? " (pages without readable text were skipped: " + job.empty_pages.join(", ") + ")" : "";
      toast("Added “" + job.source + "”" + skipped);
      tracked.delete(id);
      finished = true;
    } else if (job.status === "error") {
      toast("“" + job.source + "” could not be added. " + job.message, "error");
      tracked.delete(id);
      finished = true;
    }
  }
  activeJobs = all.filter((job) => tracked.has(job.id)).reverse();  // oldest first
  renderJobs();
  if (finished) {
    try { await refreshDocs(); } catch (err) { toast(err.message, "error"); }
  }
  if (tracked.size) schedulePoll(1500);
}

async function uploadFiles(fileList) {
  const files = [...fileList];
  if (!files.length || uploading) return;
  uploading = true;
  updateControls();
  const problems = [];
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      problems.push(file.name + ": not a PDF file");
      continue;
    }
    reading = file.name;
    renderJobs();
    try {
      const body = new FormData();
      body.append("file", file);
      const job = await api("/documents", { method: "POST", body });
      tracked.set(job.id, job.source);
      activeJobs = [...activeJobs, job];
    } catch (err) {
      problems.push(file.name + ": " + err.message);
    }
  }
  reading = null;
  uploading = false;
  renderJobs();
  updateControls();
  if (problems.length) toast(problems.join("\n"), "error");
  if (tracked.size) schedulePoll(0);
}

async function resumeJobs() {
  // After a page refresh, pick up files that are still being indexed.
  const all = await api("/jobs");
  for (const job of all) if (job.status === "queued" || job.status === "running") tracked.set(job.id, job.source);
  if (tracked.size) schedulePoll(0);
}

$("addBtn").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", () => {
  const files = [...$("file").files];
  $("file").value = "";  // so choosing the same file again still counts as a change
  uploadFiles(files);
});

// Drag and drop anywhere over the app (not just a small box).
let dragDepth = 0;
const body = $("body");
const carriesFiles = (event) => event.dataTransfer && [...event.dataTransfer.types].includes("Files");
body.addEventListener("dragenter", (event) => {
  if (!carriesFiles(event)) return;
  event.preventDefault();
  dragDepth += 1;
  body.classList.add("dragging");
});
body.addEventListener("dragover", (event) => { if (carriesFiles(event)) event.preventDefault(); });
body.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) body.classList.remove("dragging");
});
body.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  body.classList.remove("dragging");
  uploadFiles(event.dataTransfer.files);
});
// A file dropped outside the app would make the browser open it and leave the page.
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());

/* ---------- rendering answers ---------- */
// Turn the model's text into safe DOM: ```code blocks```, `inline code`, **bold**, bullets, dividers.
// Everything goes through textContent, so nothing in an answer can inject HTML.
function renderAnswer(container, text) {
  container.replaceChildren();
  const prepared = text
    .replace(/^#{1,6}\s*(.+)$/gm, "**$1**")
    .replace(/^[ \t]*[*-][ \t]+/gm, "• ");
  const parts = prepared.split(/```[^\n]*\n([\s\S]*?)```/g);
  parts.forEach((part, i) => {
    if (i % 2 === 1) {  // a fenced code block
      const pre = el("pre");
      pre.append(el("code", null, part.replace(/\s+$/, "")));
      container.append(pre);
      return;
    }
    // A line of only dashes is a divider. Trim the blank lines around it so it doesn't leave gaps.
    const sections = part.split(/^[ \t]*-{3,}[ \t]*$/gm);
    sections.forEach((section, j) => {
      if (j > 0) container.append(el("hr"));
      appendInline(container, sections.length > 1 ? section.replace(/^\n+|\n+$/g, "") : section);
    });
  });
}

// **bold** and `code` inside a line. Bold can contain code (the model often writes **`x`**),
// so bold content is processed again; a bold span can't contain "**", so this always ends.
function appendInline(parent, text) {
  for (const piece of text.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g)) {
    if (piece.startsWith("**") && piece.endsWith("**") && piece.length > 4) {
      const strong = el("strong");
      appendInline(strong, piece.slice(2, -2));
      parent.append(strong);
    } else if (piece.startsWith("`") && piece.endsWith("`") && piece.length > 2) {
      parent.append(el("code", null, piece.slice(1, -1)));
    } else if (piece) {
      parent.append(document.createTextNode(piece));
    }
  }
}

// One tag per file, listing its pages: "Manual  p. 3, 4"
function groupSources(sources) {
  const byFile = new Map();
  for (const s of sources) {
    if (!byFile.has(s.source)) byFile.set(s.source, new Set());
    byFile.get(s.source).add(s.page);
  }
  return [...byFile].map(([name, pages]) => ({ name, pages: [...pages].sort((a, b) => a - b) }));
}

/* ---------- welcome screen ---------- */
const USE_CASES = [
  { icon: "book", title: "Studying", blurb: "Lecture slides, textbooks, notes",
    examples: ["Summarize the key ideas", "What are the most important terms to know?"] },
  { icon: "briefcase", title: "Work", blurb: "Manuals, handbooks, policies, procedures",
    examples: ["What are the main steps or procedures described?", "Are there any warnings or safety notes?"] },
];

function renderWelcome() {
  const box = $("welcome");
  box.replaceChildren();
  box.append(el("h3", null, hasDocs ? "What would you like to know?" : "Ask questions about your documents"));
  box.append(el("p", null, hasDocs
    ? "Ask in plain English and get an answer with the pages it came from. Try an example or type your own question."
    : "Add a document, then ask questions in plain English. Every answer shows the pages it came from, so you can check it."));
  const cases = el("div", "cases");
  for (const useCase of USE_CASES) {
    const card = el("div", "case");
    const head = el("div", "case-head");
    const badge = el("span", "case-icon");
    badge.append(icon(useCase.icon, 20));
    const titles = el("div");
    titles.append(el("h4", null, useCase.title), el("p", null, useCase.blurb));
    head.append(badge, titles);
    card.append(head);
    if (hasDocs) {
      const list = el("div", "examples");
      for (const text of useCase.examples) {
        const button = el("button", "example", text);
        button.type = "button";
        button.disabled = busy;
        button.addEventListener("click", () => send(text));
        list.append(button);
      }
      card.append(list);
    }
    cases.append(card);
  }
  box.append(cases);
  if (!hasDocs) {
    const steps = el("div", "steps");
    const add = el("button", "btn primary");
    add.type = "button";
    add.disabled = uploading;
    withIcon(add, "plus", "Add your first document");
    add.addEventListener("click", () => $("file").click());
    steps.append(add);
    box.append(steps);
  }
}

/* ---------- conversation ---------- */
function startConversation() {
  $("welcome").hidden = true;
  $("newChat").hidden = false;
}

function addUserMessage(text) {
  const row = el("div", "msg user");
  row.append(el("div", "bubble", text));
  $("chat").append(row);
}

function botAvatar() {
  const avatar = el("span", "avatar");
  avatar.append(icon("file", 16));
  return avatar;
}

function addPendingReply() {
  const row = el("div", "msg bot");
  const bubble = el("div", "bubble");
  const dots = el("div", "typing");
  dots.setAttribute("role", "status");
  dots.setAttribute("aria-label", "Thinking");
  dots.append(el("span"), el("span"), el("span"));
  bubble.append(dots);
  row.append(botAvatar(), bubble);
  $("chat").append(row);
  return { row, bubble };
}

function fillReply(pending, data) {
  pending.bubble.replaceChildren();
  const answer = el("div", "answer");
  renderAnswer(answer, data.answer);
  pending.bubble.append(answer);

  const groups = groupSources(data.sources);
  const footer = el("div", "sources");
  if (groups.length) footer.append(el("span", "sources-label", "Sources"));
  for (const g of groups) {
    const chip = el("span", "chip");
    chip.append(icon("file", 13), el("span", null, g.name.replace(/\.pdf$/i, "") + "  ·  p. " + g.pages.join(", ")));
    chip.title = g.name + ", page " + g.pages.join(", ");
    footer.append(chip);
  }
  const actions = el("span", "actions");
  const copy = el("button", "btn sm");
  copy.type = "button";
  withIcon(copy, "copy", "Copy", 14);
  copy.addEventListener("click", async () => {
    let ok = true;
    try { await navigator.clipboard.writeText(data.answer); } catch { ok = false; }
    withIcon(copy, ok ? "check" : "x", ok ? "Copied" : "Copy failed", 14);
    setTimeout(() => withIcon(copy, "copy", "Copy", 14), 1600);
  });
  actions.append(copy);
  footer.append(actions);
  pending.bubble.append(footer);
}

function fillError(pending, message) {
  pending.row.classList.add("error");
  pending.bubble.replaceChildren(document.createTextNode(message));
}

async function send(text) {
  const question = (text !== undefined ? text : $("question").value).trim();
  if (!question || busy || !hasDocs) return;
  busy = true;
  startConversation();
  addUserMessage(question);
  $("question").value = "";   // the question now lives in the conversation, not the box
  autoGrow();
  const pending = addPendingReply();
  updateControls();
  scrollToEnd();
  try {
    const data = await api("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: history.slice(-MEMORY_TURNS) }),
    });
    fillReply(pending, data);
    history.push({ question, answer: data.answer });
    history = history.slice(-20);  // the server accepts at most 20; it only uses the latest few
  } catch (err) {
    fillError(pending, err.message);
  } finally {
    busy = false;
    updateControls();
    scrollToEnd();
    if (!$("question").disabled) $("question").focus();
  }
}

function newChat() {
  for (const node of [...$("chat").children]) if (node.id !== "welcome") node.remove();
  $("welcome").hidden = false;
  $("newChat").hidden = true;
  history = [];  // forget the conversation: the next question starts fresh
  $("chatScroll").scrollTo({ top: 0 });
}

/* ---------- composer ---------- */
function autoGrow() {
  const box = $("question");
  box.style.height = "auto";
  box.style.height = Math.min(box.scrollHeight, 160) + "px";
}

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  send();
});
$("question").addEventListener("input", () => { autoGrow(); updateControls(); });
$("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
});
$("newChat").addEventListener("click", newChat);
$("menuBtn").addEventListener("click", () => setDrawer(!$("sidebar").classList.contains("open")));
$("scrim").addEventListener("click", () => setDrawer(false));
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  setDrawer(false);
  if (pendingRemove || confirmAll) { pendingRemove = null; confirmAll = false; renderDocs(); renderRemoveAll(); }
});

/* ---------- accounts: sign-in, account menu, password, administration ---------- */
let currentUser = null;
let authMode = "login";  // "login", or "setup" when no account exists yet
let endingSession = false;

const ACTION_LABELS = {
  setup: "Created the administrator account", login: "Signed in", login_failed: "Failed sign-in attempt",
  logout: "Signed out", password_changed: "Changed password", user_created: "Created a user",
  user_updated: "Updated a user", user_deleted: "Deleted a user", upload: "Added a document",
  document_removed: "Removed a document", documents_removed_all: "Removed all documents",
  ask: "Asked a question", indexing_done: "Finished indexing", indexing_failed: "Indexing stopped",
};

function setAuthError(message) { $("authError").textContent = message; }

function resetSessionState() {
  // Forget everything belonging to the person who was signed in (so the next person sees none of it).
  history = [];
  docs = [];
  hasDocs = false;
  tracked.clear();
  activeJobs = [];
  reading = null;
  if (pollTimer !== null) { clearTimeout(pollTimer); pollTimer = null; }
  pendingRemove = null;
  confirmAll = false;
  busy = false;
  uploading = false;
  newChat();
  $("question").value = "";
  $("toasts").replaceChildren();
  // The administrator's panels and every form field: nothing of the last person may be left in the page.
  userUi.clear();
  $("userList").replaceChildren();
  $("auditBody").replaceChildren();
  $("adminError").textContent = "";
  $("pwError").textContent = "";
  for (const id of ["nuName", "nuPass", "pwCurrent", "pwNew", "pwConfirm"]) $(id).value = "";
  renderDocs();
  renderRemoveAll();
  renderWelcome();
  renderJobs();
  updateControls();
}

function showAuth(mode, notice = "") {
  authMode = mode;
  currentUser = null;
  closeModals();
  setUserMenu(false);
  setDrawer(false);
  $("app").hidden = true;
  $("authView").hidden = false;
  const setup = mode === "setup";
  $("authLead").textContent = setup
    ? "Welcome! Create the administrator account to get started. You can add other people from the account menu later."
    : "Sign in to continue.";
  $("authSubmit").textContent = setup ? "Create account" : "Sign in";
  $("authConfirmRow").hidden = !setup;
  $("authHint").hidden = !setup;
  $("authPass").autocomplete = setup ? "new-password" : "current-password";
  $("authPass").value = "";
  $("authConfirm").value = "";
  setAuthError(notice);
  $("authUser").focus();
}

async function enterApp(user) {
  currentUser = user;
  $("userBtn").textContent = user.username;
  $("whoName").textContent = user.username;
  $("whoRole").textContent = user.role === "admin" ? "Administrator" : "User";
  $("adminBtn").hidden = user.role !== "admin";
  $("authView").hidden = true;
  $("app").hidden = false;
  renderWelcome();
  updateControls();
  try {
    await refreshDocs();
    if (!hasDocs && isMobile()) setDrawer(true);
    await resumeJobs();
  } catch (err) {
    toast(err.message, "error");
  }
}

function sessionEnded() {
  // Any request answered "please sign in" (an expired or revoked session) lands here.
  if (endingSession || currentUser === null) return;
  endingSession = true;
  resetSessionState();
  showAuth("login", "Your session has ended. Please sign in again.");
  endingSession = false;
}

$("authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = $("authUser").value.trim();
  const password = $("authPass").value;
  if (!username || !password) { setAuthError("Enter your username and password."); return; }
  if (authMode === "setup" && password !== $("authConfirm").value) {
    setAuthError("The two passwords don't match.");
    return;
  }
  $("authSubmit").disabled = true;
  setAuthError("");
  try {
    const status = await api(authMode === "setup" ? "/auth/setup" : "/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    $("authPass").value = "";
    $("authConfirm").value = "";
    await enterApp(status.user);
  } catch (err) {
    setAuthError(err.message);
    $("authPass").value = "";
  } finally {
    $("authSubmit").disabled = false;
  }
});

async function signOut() {
  setUserMenu(false);
  try { await api("/auth/logout", { method: "POST" }); } catch { /* signed out locally either way */ }
  resetSessionState();
  currentUser = null;
  showAuth("login", "");
  $("authUser").value = "";  // on purpose: don't leave the last username on a shared computer
}

/* --- account menu --- */
function setUserMenu(open) {
  $("userPanel").hidden = !open;
  $("userBtn").setAttribute("aria-expanded", String(open));
}
$("userBtn").addEventListener("click", (event) => { event.stopPropagation(); setUserMenu($("userPanel").hidden); });
document.addEventListener("click", (event) => {
  if (!$("userPanel").hidden && !event.target.closest(".user-menu")) setUserMenu(false);
});
$("logoutBtn").addEventListener("click", signOut);

/* --- dialogs --- */
function openModal(id) {
  $("modalScrim").hidden = false;
  for (const modal of document.querySelectorAll(".modal")) modal.hidden = modal.id !== id;
  const first = $(id).querySelector("input, button");
  if (first) first.focus();
}
function closeModals() {
  $("modalScrim").hidden = true;
  for (const modal of document.querySelectorAll(".modal")) modal.hidden = true;
}
$("modalScrim").addEventListener("click", (event) => { if (event.target === $("modalScrim")) closeModals(); });
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { closeModals(); setUserMenu(false); }
});

/* --- change password --- */
$("passwordBtn").addEventListener("click", () => {
  setUserMenu(false);
  $("pwError").textContent = "";
  openModal("passwordModal");
});
$("pwCancel").addEventListener("click", closeModals);
$("passwordForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const next = $("pwNew").value;
  if (next !== $("pwConfirm").value) { $("pwError").textContent = "The new passwords don't match."; return; }
  try {
    await api("/auth/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: $("pwCurrent").value, new_password: next }),
    });
    for (const id of ["pwCurrent", "pwNew", "pwConfirm"]) $(id).value = "";
    closeModals();
    toast("Password changed");
  } catch (err) {
    $("pwError").textContent = err.message;
  }
});

/* --- administration (administrators only) --- */
const userUi = new Map();  // user id -> "reset" or "delete" while that row is asking for more input

function showAdminTab(name) {
  const users = name === "users";
  $("usersPane").hidden = !users;
  $("auditPane").hidden = users;
  $("tabUsers").setAttribute("aria-selected", String(users));
  $("tabAudit").setAttribute("aria-selected", String(!users));
  if (users) loadUsers(); else loadAudit();
}

$("adminBtn").addEventListener("click", () => {
  setUserMenu(false);
  $("adminError").textContent = "";
  openModal("adminModal");
  showAdminTab("users");
});
$("adminClose").addEventListener("click", closeModals);
$("tabUsers").addEventListener("click", () => showAdminTab("users"));
$("tabAudit").addEventListener("click", () => showAdminTab("audit"));

async function adminCall(path, method, payload) {
  $("adminError").textContent = "";
  try {
    const options = { method };
    if (payload !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(payload);
    }
    const result = await api(path, options);
    await loadUsers();
    return result;
  } catch (err) {
    $("adminError").textContent = err.message;
    return null;
  }
}

function userButton(label, handler, extra = "") {
  const button = el("button", "btn sm " + extra, label);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function renderUsers(users) {
  const list = $("userList");
  list.replaceChildren();
  for (const user of users) {
    const row = el("li", "user-row");
    const who = el("div", "who");
    who.append(document.createTextNode(user.username));
    who.append(el("small", null, "Added " + new Date(user.created_at * 1000).toLocaleDateString()));
    row.append(who);
    row.append(el("span", "tag", user.role === "admin" ? "Administrator" : "User"));
    if (user.disabled) row.append(el("span", "tag off", "Disabled"));
    const isMe = currentUser && user.id === currentUser.id;
    if (isMe) row.append(el("span", "tag", "You"));
    const actions = el("div", "actions");
    const mode = userUi.get(user.id);
    if (mode === "reset") {
      const input = el("input");
      input.type = "password";
      input.placeholder = "New password (10+ characters)";
      input.autocomplete = "new-password";
      input.setAttribute("aria-label", "New password for " + user.username);
      actions.append(input);
      actions.append(userButton("Save", async () => {
        if (await adminCall("/admin/users/" + user.id, "PATCH", { password: input.value })) {
          userUi.delete(user.id);
          toast("Password reset for " + user.username);
          await loadUsers();
        }
      }, "primary"));
      actions.append(userButton("Cancel", () => { userUi.delete(user.id); loadUsers(); }));
    } else if (mode === "delete") {
      actions.append(el("span", null, "Delete " + user.username + " and all their documents?"));
      actions.append(userButton("Delete", async () => {
        userUi.delete(user.id);
        if (await adminCall("/admin/users/" + user.id, "DELETE")) toast("Deleted " + user.username);
      }, "danger"));
      actions.append(userButton("Cancel", () => { userUi.delete(user.id); loadUsers(); }));
    } else {
      actions.append(userButton("Reset password", () => { userUi.set(user.id, "reset"); loadUsers(); }));
      if (!isMe) {
        actions.append(userButton(user.disabled ? "Enable" : "Disable",
          () => adminCall("/admin/users/" + user.id, "PATCH", { disabled: !user.disabled })));
        actions.append(userButton(user.role === "admin" ? "Make user" : "Make administrator",
          () => adminCall("/admin/users/" + user.id, "PATCH", { role: user.role === "admin" ? "user" : "admin" })));
        actions.append(userButton("Delete", () => { userUi.set(user.id, "delete"); loadUsers(); }));
      }
    }
    row.append(actions);
    list.append(row);
  }
}

async function loadUsers() {
  try {
    renderUsers(await api("/admin/users"));
  } catch (err) {
    $("adminError").textContent = err.message;
  }
}

$("newUserForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const created = await adminCall("/admin/users", "POST", {
    username: $("nuName").value.trim(), password: $("nuPass").value, role: $("nuRole").value,
  });
  if (created) {
    toast("Added " + created.username);
    $("nuName").value = "";
    $("nuPass").value = "";
  }
});

async function loadAudit() {
  const body = $("auditBody");
  body.replaceChildren();
  try {
    const entries = await api("/admin/audit?limit=200");
    for (const entry of entries) {
      const row = el("tr");
      row.append(
        el("td", "when", new Date(entry.time * 1000).toLocaleString()),
        el("td", null, entry.user || "-"),
        el("td", null, ACTION_LABELS[entry.action] || entry.action),
        el("td", null, entry.detail + (entry.ip ? "  (" + entry.ip + ")" : "")),
      );
      body.append(row);
    }
    if (!entries.length) {
      const row = el("tr");
      const cell = el("td", null, "Nothing has happened yet.");
      cell.colSpan = 4;
      row.append(cell);
      body.append(row);
    }
  } catch (err) {
    $("adminError").textContent = err.message;
  }
}

/* ---------- start ---------- */
$("brandMark").append(brandMark(32));
$("authMark").append(brandMark(34));
$("adminClose").append(icon("x", 18));
$("menuBtn").append(icon("menu", 20));
withIcon($("addBtn"), "plus", "Add documents");
withIcon($("newChat"), "refresh", "New chat", 15);
$("send").append(icon("send", 19));
renderWelcome();
renderDocs();
updateControls();

(async function boot() {
  // Ask the server who (if anyone) is signed in, and show the right screen.
  try {
    const status = await api("/auth/status");
    if (status.authenticated) await enterApp(status.user);
    else showAuth(status.setup_required ? "setup" : "login");
  } catch (err) {
    showAuth("login", err.message);
  }
})();
