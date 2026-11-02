import { api } from "./api.js";
import { answerPanel, el, empty, fill, fmtDateTime, icon, loadFailed, machineOptions, machineTag, pager, restoreFilters, selectMachine, statusChip, syncFilters } from "./ui.js";

const form = document.getElementById("filters");
const container = document.getElementById("history");
let machines = [];

function row(q) {
  const machine = machines.find((m) => m.machine_id === q.machine.machine_id) || q.machine;
  const suffix = q.status === "answered" ? ` · ${q.citations.length} source${q.citations.length === 1 ? "" : "s"}` : "";
  return el("li", { class: "log-item" }, el("details", {},
    el("summary", {},
      el("span", { class: "readout when", text: fmtDateTime(q.created_at) }),
      machineTag(q.machine.machine_id),
      el("span", { class: "q", text: q.question }),
      statusChip(q.status, suffix),
      icon("chevron-down")),
    el("div", { class: "log-detail" },
      answerPanel(q),
      el("div", {}, el("button", { type: "button", class: "btn btn-outline-secondary btn-sm",
        onclick: () => { selectMachine(machine); location.href = "/assistant/"; } }, icon("chat-square-text"), ` Ask again about ${q.machine.machine_id}`)))));
}

async function load(page = 1) {
  const params = syncFilters(form, { page: page > 1 ? page : "" });
  container.setAttribute("aria-busy", "true");
  try {
    const data = await api(`/api/history/?${params}`);
    const filtered = [...new FormData(form).values()].some(Boolean);
    fill(container, data.results.length
      ? [el("ul", { class: "log" }, data.results.map(row)), pager(data, page, load)]
      : empty(filtered
        ? { iconName: "funnel", title: "No questions match these filters", body: "Change the machine or outcome filter." }
        : { iconName: "chat-square-text", title: "No questions yet", body: "Questions you ask are saved here with their answers and sources.",
            action: el("a", { class: "btn btn-primary btn-sm mt-2", href: "/machines/" }, "Ask a question") }));
  } catch (error) {
    fill(container, loadFailed(error));
  }
}

form.addEventListener("change", () => load(1));
try {
  machines = await machineOptions(document.getElementById("f-machine"));
} catch { /* the filter just lists no machines; the table still loads */ }
load(restoreFilters(form));
