import { api } from "./api.js";
import { el, fill, kpiStrip, loadFailed, machinePicker, machineTag, manualsTable, questionRows, unansweredList } from "./ui.js";

const role = document.body.dataset.role;
const $ = (id) => document.getElementById(id);

async function engineer() {
  try {
    const [machines, manuals] = await Promise.all([api("/api/machines/"), api("/api/manuals/")]);
    fill($("machines"), machinePicker(machines, manuals, { canAsk: true }));
  } catch (error) { fill($("machines"), loadFailed(error)); }
  try {
    const history = await api("/api/history/");
    fill($("recent"), questionRows(history.results.slice(0, 6)));
  } catch (error) { fill($("recent"), loadFailed(error)); }
}

async function admin() {
  try {
    const [machines, manuals] = await Promise.all([api("/api/machines/"), api("/api/manuals/")]);
    fill($("machines"), el("div", { class: "table-wrap" }, el("table", { class: "table data-table" },
      el("caption", { class: "visually-hidden", text: "Registered machines" }),
      el("thead", {}, el("tr", {}, ["Machine", "Model", "Name", "Manuals", "Indexed"].map((h) => el("th", { scope: "col", text: h })))),
      el("tbody", {}, machines.map((m) => {
        const own = manuals.filter((x) => x.machine_id === m.machine_id);
        return el("tr", {}, el("td", {}, machineTag(m.machine_id)), el("td", { class: "mono", text: m.model }), el("td", { text: m.machine_name }),
          el("td", { class: "mono nums", text: String(own.length) }),
          el("td", { class: "mono nums", text: `${own.filter((x) => x.indexing_status === "indexed").length} / ${own.length}` }));
      })))));
    fill($("manuals"), manualsTable(manuals, machines, { caption: "Manuals and indexing status" }));
  } catch (error) {
    fill($("machines"), loadFailed(error));
    fill($("manuals"), loadFailed(error));
  }
}

async function supervisor() {
  try {
    const analytics = await api("/api/supervisor/analytics/");
    fill($("kpis"), kpiStrip(analytics));
    fill($("unanswered"), unansweredList(analytics.recent_unanswered));
  } catch (error) {
    fill($("kpis"), loadFailed(error));
    $("unanswered").closest("section").hidden = true;
  }
}

({ ENGINEER: engineer, ADMIN: admin, SUPERVISOR: supervisor })[role]?.();
