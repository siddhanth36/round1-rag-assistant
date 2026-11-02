// Shared DOM renderers. Server text is only ever inserted with textContent (via el()), never as HTML.
import { api } from "./api.js"; // api.js imports toast from here; the cycle resolves at call time

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

export const icon = (name) => el("i", { class: `bi bi-${name}`, "aria-hidden": "true" });

export function toast(message, kind = "info") {
  const node = el("div", { class: `toast show align-items-center ${kind === "danger" ? "toast-danger" : ""}`, role: kind === "danger" ? "alert" : "status" },
    el("div", { class: "d-flex" },
      el("div", { class: "toast-body d-flex gap-2 align-items-start" }, icon(kind === "danger" ? "x-octagon" : "info-circle"), el("span", { text: message })),
      el("button", { type: "button", class: "btn-close me-2 m-auto", "aria-label": "Dismiss", onclick: () => node.remove() })));
  document.getElementById("toasts").append(node);
  setTimeout(() => node.remove(), 6000);
}

const pad = (n) => String(n).padStart(2, "0");
export function fmtDateTime(iso) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export const machineTag = (machineId) => el("span", { class: "tag", text: machineId });

export function empty({ iconName = "inbox", title, body, action }) {
  return el("div", { class: "empty" }, icon(iconName), el("h3", { text: title }), body && el("p", { text: body }), action);
}

export const skeleton = () => el("div", { class: "skeleton", "aria-hidden": "true" }, el("span"), el("span"), el("span"));

// Replace a container's loading skeleton with content.
export function fill(container, ...nodes) {
  container.replaceChildren(...nodes.flat().filter(Boolean));
  container.removeAttribute("aria-busy");
}

// Consistent "could not load" state for any API read.
export function loadFailed(error) {
  if (error.status === 0) return empty({ iconName: "wifi-off", title: "Can't reach the server", body: "Check your connection and reload the page." });
  if (error.status === 403) return empty({ iconName: "shield-lock", title: "Not available for your role", body: "The server refused this request for your account." });
  if (error.status === 404) return empty({ iconName: "plug", title: "Not connected yet", body: "This part of the API is not available on the server yet." });
  return empty({ iconName: "exclamation-triangle", title: "Couldn't load this", body: `The server answered with an error (HTTP ${error.status}). Reload to try again.` });
}

const INDEXING = {
  indexed: { cls: "chip-ok", iconName: "check-circle", label: "Indexed" },
  pending: { cls: "chip-caution", iconName: "hourglass-split", label: "Pending indexing" },
  failed: { cls: "chip-danger", iconName: "x-octagon", label: "Indexing failed" },
};
export function indexingChip(status) {
  const s = INDEXING[status] || INDEXING.pending;
  return el("span", { class: `chip ${s.cls}` }, icon(s.iconName), s.label);
}

// Manuals grouped by machine (dashboard for Admin + manuals library).
export function manualsTable(manuals, machines, { caption }) {
  if (!manuals.length) return empty({ iconName: "journal-x", title: "No manuals registered", body: "Manuals appear here once an administrator uploads them." });
  const name = Object.fromEntries(machines.map((m) => [m.machine_id, `${m.model} ${m.machine_name}`]));
  const groups = [...new Set(manuals.map((m) => m.machine_id))];
  return el("div", { class: "table-wrap" }, el("table", { class: "table data-table table-hover" },
    el("caption", { class: "visually-hidden", text: caption }),
    el("thead", {}, el("tr", {}, ["Document", "Rev", "File", "Pages", "Status", "Uploaded", ""].map((h) =>
      el("th", { scope: "col" }, h || el("span", { class: "visually-hidden", text: "Actions" }))))),
    groups.map((machineId) => el("tbody", {},
      el("tr", { class: "group-row" }, el("th", { scope: "colgroup", colspan: "7" }, machineTag(machineId), ` ${name[machineId] || ""}`)),
      manuals.filter((m) => m.machine_id === machineId).map((m) => el("tr", {},
        el("td", { class: "mono", text: m.document_id }),
        el("td", { class: "mono", text: m.revision }),
        el("td", { class: "subtle", text: m.original_filename }),
        el("td", { class: "mono nums", text: String(m.page_count) }),
        el("td", {}, indexingChip(m.indexing_status)),
        el("td", { class: "readout" }, `${m.uploaded_by} · ${fmtDateTime(m.uploaded_at)}`),
        el("td", { class: "text-end" }, el("a", { class: "btn btn-outline-secondary btn-xs", href: `/viewer/?manual=${m.id}&page=1`,
          "aria-label": `Open ${m.document_id} revision ${m.revision}` }, icon("file-earmark-pdf"), " Open"))))))));
}

// Previous / next for DRF page-number pagination (history + question log).
export function pager(data, page, onPage) {
  const pages = Math.max(1, Math.ceil(data.count / 25));
  return el("nav", { class: "pager", "aria-label": "Pagination" },
    el("button", { type: "button", class: "btn btn-outline-secondary btn-sm", disabled: !data.previous, onclick: () => onPage(page - 1) }, icon("chevron-left"), " Previous"),
    el("span", { class: "readout", text: `Page ${page} of ${pages} · ${data.count} question${data.count === 1 ? "" : "s"}` }),
    el("button", { type: "button", class: "btn btn-outline-secondary btn-sm", disabled: !data.next, onclick: () => onPage(page + 1) }, "Next ", icon("chevron-right")));
}

// Reflect a filter form in the URL (shareable, survives reload) and read it back.
export function syncFilters(form, extra = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of new FormData(form)) if (value) params.set(key, value);
  for (const [key, value] of Object.entries(extra)) if (value) params.set(key, value);
  history.replaceState(null, "", `${location.pathname}${params.size ? `?${params}` : ""}`);
  return params;
}
export function restoreFilters(form) {
  const params = new URLSearchParams(location.search);
  for (const field of form.elements) if (field.name && params.has(field.name)) field.value = params.get(field.name);
  return Number(params.get("page")) || 1;
}

export async function machineOptions(select) {
  const machines = await api("/api/machines/");
  select.append(...machines.map((m) => el("option", { value: m.machine_id, text: `${m.machine_id} · ${m.machine_name}` })));
  return machines;
}

// Compact question rows (engineer dashboard).
export function questionRows(questions) {
  if (!questions.length) return empty({ iconName: "chat-square-text", title: "No questions yet", body: "Pick a machine and ask your first question." });
  return el("ul", { class: "log" }, questions.map((q) => el("li", { class: "log-item" },
    el("div", { class: "d-grid gap-1 px-3 py-2 small-ui" },
      el("div", { class: "d-flex flex-wrap gap-2 align-items-center" }, el("span", { class: "readout", text: fmtDateTime(q.created_at) }),
        machineTag(q.machine.machine_id), statusChip(q.status, q.status === "answered" ? sourcesSuffix(q.citations.length) : "")),
      el("span", { text: q.question })))));
}

// ---------- selected machine (per browser session) ----------
const MACHINE_KEY = "industrix.machine";
export function selectedMachine() {
  try { return JSON.parse(sessionStorage.getItem(MACHINE_KEY)); } catch { return null; }
}
export function selectMachine(machine) {
  sessionStorage.setItem(MACHINE_KEY, JSON.stringify({ machine_id: machine.machine_id, model: machine.model, machine_name: machine.machine_name }));
}

// Machine nameplate rows (dashboard + machine selection page).
export function machinePicker(machines, manuals, { canAsk = true } = {}) {
  if (!machines.length) return empty({ iconName: "gear-wide-connected", title: "No machines registered", body: "An administrator registers machines from the challenge pack." });
  const current = selectedMachine()?.machine_id;
  return el("ul", { class: "machine-list" }, machines.map((m) => {
    const own = manuals.filter((x) => x.machine_id === m.machine_id);
    return el("li", { class: "machine-row", "data-selected": String(m.machine_id === current) },
      machineTag(m.machine_id),
      el("div", {},
        el("div", { class: "machine-name" }, `${m.model} · ${m.machine_name}`),
        el("div", { class: "manual-ids" }, own.length
          ? el("span", { class: "readout", text: own.map((x) => `${x.document_id} r${x.revision}`).join("  ·  ") })
          : el("span", { class: "subtle small-ui", text: "No manuals registered yet" }))),
      canAsk
        ? el("button", { type: "button", class: "btn btn-primary btn-sm", "aria-label": `Ask about ${m.machine_id} ${m.machine_name}`,
            onclick: () => { selectMachine(m); location.href = "/assistant/"; } }, "Ask about it ", icon("arrow-right"))
        : el("a", { class: "btn btn-outline-secondary btn-sm", href: `/manuals/?machine=${encodeURIComponent(m.machine_id)}` }, icon("journal-text"), " Manuals"));
  }));
}

// ---------- answer states ----------
const INSUFFICIENT_TEXT ="The available manuals for the selected machine do not contain enough information to answer this question.";
const STATUS = {
  answered: { cls: "chip-ok", iconName: "check-circle", label: "Answered" },
  insufficient_evidence: { cls: "chip-caution", iconName: "journal-x", label: "Not in the manuals" },
  error: { cls: "chip-danger", iconName: "x-octagon", label: "Couldn't answer" },
  none: { cls: "chip-neutral", iconName: "hourglass-split", label: "No answer recorded" },
};
export function statusChip(status, suffix = "") {
  const s = STATUS[status] || STATUS.none;
  return el("span", { class: `chip ${s.cls}` }, icon(s.iconName), `${s.label}${suffix}`);
}
export const statusLabel = (status) => (STATUS[status] || STATUS.none).label;

function sourcesSuffix(n) { return ` · ${n} source${n === 1 ? "" : "s"}`; }

function answerText(q) {
  // "[S1]" markers become links to the matching evidence slip.
  const tags = new Set(q.citations.map((c) => c.source_tag));
  return el("p", {}, q.answer.split(/(\[S\d+\])/).map((part) => {
    const tag = part.match(/^\[(S\d+)\]$/)?.[1];
    if (!tag || !tags.has(tag)) return part;
    return el("a", { class: "src-tag", href: `#slip-${q.id}-${tag}`, "aria-label": `Source ${tag}`, onclick: (e) => {
      e.preventDefault();
      const slip = document.getElementById(`slip-${q.id}-${tag}`);
      slip.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      slip.classList.add("is-flagged");
      slip.focus({ preventScroll: true });
      setTimeout(() => slip.classList.remove("is-flagged"), 1600);
    } }, tag);
  }));
}

function slip(qid, c) {
  const text = el("p", { class: "is-clamped m-0", text: c.passage, id: `passage-${qid}-${c.source_tag}` });
  const toggle = c.passage.length > 320 && el("button", { type: "button", class: "slip-toggle", "aria-expanded": "false",
    "aria-controls": text.id, onclick: () => {
      const open = text.classList.toggle("is-clamped") === false;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? "Show less" : "Show full passage";
    } }, "Show full passage");
  const ref = `${c.document_id} Rev ${c.revision}, PDF page ${c.pdf_page}`;
  return el("article", { class: "slip", id: `slip-${qid}-${c.source_tag}`, tabindex: "-1", "aria-label": `Source ${c.source_tag}: ${ref}` },
    el("div", { class: "slip-head" }, el("span", { class: "tag-s", text: c.source_tag }), `${c.document_id} | Rev ${c.revision} | Page ${c.pdf_page}`),
    el("div", { class: "slip-passage" }, text, toggle),
    el("div", { class: "slip-actions" },
      el("a", { class: "btn btn-primary btn-xs", href: `/viewer/?manual=${c.manual_id}&page=${c.pdf_page}` }, icon("box-arrow-up-right"), ` Open page ${c.pdf_page}`),
      el("button", { type: "button", class: "btn btn-outline-secondary btn-xs", onclick: () => navigator.clipboard.writeText(ref).then(
        () => toast(`Copied: ${ref}`), () => toast("Copy failed. Select the reference manually.", "danger")) }, icon("copy"), " Copy reference")));
}

// tabindex=-1: the assistant moves focus to the whole panel (visible outline), not to a hidden heading.
function panel(cls, head, body, foot) {
  return el("section", { class: `answer ${cls} enter`, tabindex: "-1" },
    el("div", { class: "answer-head" }, head), body && el("div", { class: "answer-body" }, body), foot);
}

// q = a Question from the API ({id, status, answer, latency_ms, citations, machine}).
export function answerPanel(q, { onRetry, scope } = {}) {
  const heading = (text) => el("h3", { class: "visually-hidden", text });
  const latency = q.latency_ms != null && el("span", { class: "readout", text: `${(q.latency_ms / 1000).toFixed(1)} s` });
  if (q.status === "answered") {
    return panel("", [heading("Answer"), statusChip("answered", sourcesSuffix(q.citations.length)), latency], answerText(q),
      q.citations.length > 0 && el("div", { class: "slips" }, q.citations.map((c) => slip(q.id, c))));
  }
  if (q.status === "insufficient_evidence") {
    return panel("answer--insufficient", [heading("Not in the manuals"), statusChip("insufficient_evidence"), latency],
      [el("p", { text: q.answer || INSUFFICIENT_TEXT }),
       scope && el("p", { class: "muted small-ui mt-2", text: `Searched: ${scope}` }),
       el("p", { class: "muted small-ui mt-1", text: "Try rephrasing, or check that the right machine is selected." })]);
  }
  if (q.status === "error") {
    return panel("answer--error", [heading("Couldn't answer"), statusChip("error")],
      el("p", { text: "The assistant couldn't complete this request. It has been logged for your administrator." }),
      onRetry && el("div", { class: "answer-foot" }, el("button", { type: "button", class: "btn btn-outline-danger btn-sm", onclick: onRetry }, icon("arrow-repeat"), " Retry")));
  }
  return panel("answer--offline", [heading("No answer recorded"), statusChip(null)],
    el("p", { class: "muted", text: "This question was logged, but no answer was stored for it." }));
}

export function loadingPanel(machineId) {
  return el("section", { class: "answer answer--loading", "aria-busy": "true" },
    el("div", { class: "answer-head" }, el("span", { class: "chip chip-neutral" }, icon("search"), `Searching ${machineId} manuals…`)),
    el("div", { class: "answer-body" }, el("div", { class: "retrieving mb-3" }), skeleton()));
}

export function notConnectedPanel() {
  return panel("answer--offline", [el("h3", { class: "visually-hidden", text: "Assistant engine not connected" }),
    el("span", { class: "chip chip-neutral" }, icon("plug"), "Assistant engine not connected yet")],
    el("p", { class: "muted", text: "The question-answering service is not available on this server yet, so nothing was answered. No answer is shown until the engine is connected." }));
}

export function networkErrorPanel(onRetry) {
  return panel("answer--error", [el("h3", { class: "visually-hidden", text: "Network error" }),
    el("span", { class: "chip chip-danger" }, icon("wifi-off"), "Network error")],
    el("p", { text: "The server could not be reached. Check your connection; your question was not sent." }),
    el("div", { class: "answer-foot" }, el("button", { type: "button", class: "btn btn-outline-danger btn-sm", onclick: onRetry }, icon("arrow-repeat"), " Retry")));
}

// ---------- analytics renderers (dashboard + analytics page) ----------
export function kpiStrip(a) {
  const pct = a.total ? Math.round((a.by_status.answered / a.total) * 100) : 0;
  const kpi = (cls, iconName, label, value) => el("div", { class: `kpi ${cls}` }, el("dt", {}, icon(iconName), label), el("dd", { text: value }));
  return el("dl", { class: "kpis m-0" },
    kpi("", "chat-square-text", "Questions", String(a.total)),
    kpi("kpi-ok", "check-circle", "Answered", `${a.by_status.answered} (${pct}%)`),
    kpi("kpi-caution", "journal-x", "Not in the manuals", String(a.by_status.insufficient_evidence)),
    kpi("kpi-danger", "x-octagon", "Couldn't answer", String(a.by_status.error)),
    kpi("", "inbox", "Unanswered", String(a.unanswered)));
}

export function unansweredList(rows) {
  if (!rows.length) return empty({ iconName: "check2-all", title: "No unanswered questions", body: "Every logged question has a stored answer." });
  return el("ul", { class: "log" }, rows.map((r) => el("li", { class: "log-item" },
    el("div", { class: "d-flex flex-wrap gap-2 align-items-center px-3 py-2 small-ui" },
      el("span", { class: "readout", text: fmtDateTime(r.created_at) }), machineTag(r.machine_id), statusChip(r.status),
      el("span", { class: "subtle", text: r.asked_by }),
      el("span", { class: "w-100", text: r.question })))));
}
