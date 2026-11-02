// Supervisor/Admin analytics. Read-only: GET endpoints only.
import { api } from "./api.js";
import { answerPanel, el, empty, fill, fmtDateTime, icon, kpiStrip, loadFailed, machineOptions, machineTag, pager, restoreFilters, statusChip, syncFilters } from "./ui.js";

const $ = (id) => document.getElementById(id);
const form = $("filters");
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const SEGMENTS = [
  ["answered", "seg-answered", "Answered"],
  ["insufficient_evidence", "seg-insufficient", "Not in the manuals"],
  ["error", "seg-error", "Couldn't answer"],
];

function ledgerRow(label, row) {
  const none = row.total - SEGMENTS.reduce((sum, [key]) => sum + row[key], 0); // logged without an answer row
  const parts = [...SEGMENTS.map(([key, cls, name]) => [row[key], cls, name]), [none, "seg-none", "No answer recorded"]];
  const summary = parts.filter(([n]) => n).map(([n, , name]) => `${n} ${name.toLowerCase()}`).join(", ") || "no questions";
  return el("div", { class: "ledger-row" }, label, el("span", { class: "readout nums", text: String(row.total) }),
    el("div", { class: "bar", role: "img", "aria-label": `${row.total} questions: ${summary}` },
      parts.filter(([n]) => n).map(([n, cls, name]) => el("span", { class: cls, style: `width:${(n / row.total) * 100}%`, title: `${name}: ${n}` }))));
}

function renderLedger(a) {
  fill($("ledger"),
    el("div", { class: "ledger-legend mb-2" }, [...SEGMENTS.map(([, cls, name]) => [cls, name]), ["seg-none", "No answer recorded"]].map(([cls, name]) =>
      el("span", {}, el("span", { class: `sw ${cls.replace("seg", "sw")}`, "aria-hidden": "true" }), name))),
    ledgerRow(el("strong", { text: "All machines" }), { total: a.total, ...a.by_status }),
    a.per_machine.map((m) => ledgerRow(machineTag(m.machine_id), m)));
}

function renderTrend(days) {
  $("trend").removeAttribute("aria-busy");
  $("trend-table").replaceChildren(el("table", { class: "table data-table" },
    el("thead", {}, el("tr", {}, el("th", { scope: "col", text: "Date" }), el("th", { scope: "col", text: "Questions" }))),
    el("tbody", {}, days.map((d) => el("tr", {}, el("td", { class: "mono", text: d.date }), el("td", { class: "mono nums", text: String(d.count) }))))));
  if (!window.Chart) return; // CDN blocked: the table above still carries the data
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  new window.Chart($("trend-chart"), {
    type: "line",
    data: { labels: days.map((d) => d.date.slice(5)), datasets: [{
      label: "Questions", data: days.map((d) => d.count), borderColor: token("--color-accent"), backgroundColor: token("--color-accent-bg"),
      fill: true, tension: 0, pointRadius: 3, pointBackgroundColor: token("--color-accent"), borderWidth: 2 }] },
    options: {
      maintainAspectRatio: false, animation: reduce ? false : { duration: 240 },
      plugins: { legend: { display: false }, tooltip: { backgroundColor: token("--color-surface-raised"), borderColor: token("--color-divider-strong"),
        borderWidth: 1, titleColor: token("--color-text"), bodyColor: token("--color-text"), titleFont: { family: token("--font-mono") } } },
      scales: {
        x: { grid: { color: token("--color-divider") }, ticks: { color: token("--color-text-muted"), font: { family: token("--font-mono"), size: 12 } } },
        y: { beginAtZero: true, grid: { color: token("--color-divider") }, ticks: { precision: 0, color: token("--color-text-muted"), font: { family: token("--font-mono"), size: 12 } } },
      },
    },
  });
}

function detailRow(q, cols) {
  const trace = q.retrieval_trace && Object.keys(q.retrieval_trace).length ? JSON.stringify(q.retrieval_trace, null, 2) : null;
  return el("tr", { class: "detail-row", hidden: true }, el("td", { colspan: String(cols) }, el("div", { class: "d-grid gap-3 py-2" },
    answerPanel(q),
    trace && el("details", {}, el("summary", { class: "small-ui", text: "Retrieval trace (diagnostics)" }), el("pre", { class: "trace mt-2", text: trace })))));
}

async function loadLog(page = 1) {
  const params = syncFilters(form, { page: page > 1 ? page : "" });
  $("log").setAttribute("aria-busy", "true");
  try {
    const data = await api(`/api/supervisor/questions/?${params}`);
    if (!data.results.length) return fill($("log"), empty({ iconName: "funnel", title: "No questions match", body: "Nothing is logged for these filters yet." }));
    const headers = ["Asked", "Machine", "Engineer", "Question", "Outcome", "Latency", ""];
    fill($("log"), el("div", { class: "table-wrap" }, el("table", { class: "table data-table table-hover" },
      el("caption", { class: "visually-hidden", text: "Question log" }),
      el("thead", {}, el("tr", {}, headers.map((h) => el("th", { scope: "col" }, h || el("span", { class: "visually-hidden", text: "Details" }))))),
      el("tbody", {}, data.results.flatMap((q) => {
        const detail = detailRow(q, headers.length);
        const toggle = el("button", { type: "button", class: "btn btn-outline-secondary btn-xs", "aria-expanded": "false",
          "aria-label": `Details for question ${q.id}`, onclick: () => {
            detail.hidden = !detail.hidden;
            toggle.setAttribute("aria-expanded", String(!detail.hidden));
          } }, icon("chevron-down"));
        return [el("tr", {},
          el("td", { class: "readout", text: fmtDateTime(q.created_at) }), el("td", {}, machineTag(q.machine.machine_id)),
          el("td", { class: "mono", text: q.asked_by }), el("td", { text: q.question }), el("td", {}, statusChip(q.status)),
          el("td", { class: "readout", text: q.latency_ms != null ? `${(q.latency_ms / 1000).toFixed(1)} s` : "—" }),
          el("td", { class: "text-end" }, toggle)), detail];
      })))), pager(data, page, loadLog));
  } catch (error) {
    fill($("log"), error.status === 400 ? empty({ iconName: "funnel", title: "Check the filters", body: Object.values(error.data || {}).flat().join(" ") }) : loadFailed(error));
  }
}

form.addEventListener("change", () => loadLog(1));
try {
  const [analytics] = await Promise.all([api("/api/supervisor/analytics/"), machineOptions($("f-machine"))]);
  fill($("kpis"), kpiStrip(analytics));
  renderLedger(analytics);
  renderTrend(analytics.per_day);
  loadLog(restoreFilters(form));
} catch (error) {
  for (const id of ["kpis", "ledger", "log"]) fill($(id), loadFailed(error));
  $("trend").closest("section").hidden = true;
}
