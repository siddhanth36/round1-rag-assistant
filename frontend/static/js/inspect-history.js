// Inspection history: GET /api/inspections/?prediction=&review_status=&status=&product=&from=&to=&page= (contract §3.5).
// The server scopes rows (Engineers: own only); the filters here only narrow what it returns.
import { api } from "./api.js";
import { el, empty, fill, fmtDateTime, icon, loadFailed, restoreFilters, syncFilters } from "./ui.js";
import { errorBox, messages, notBuilt, notReady, pct, predictionChip, productOptions, reviewChip, role, statusChip } from "./inspect-common.js";

const form = document.getElementById("filters");
const container = document.getElementById("history");
const showSubmitter = role !== "ENGINEER";
let request = 0;

function row(i) {
  return el("tr", {},
    el("td", {}, el("a", { class: "mono", href: `/inspections/${i.id}/`, "aria-label": `Open inspection ${i.id}` }, `#${i.id}`)),
    el("td", { class: "readout", text: fmtDateTime(i.created_at) }),
    el("td", {}, el("span", { class: "tag", text: i.product })),
    el("td", { class: "subtle file-cell", text: i.original_filename, title: i.original_filename }),
    el("td", {}, statusChip(i.status)),
    el("td", {}, i.original ? predictionChip(i.original.predicted_class) : el("span", { class: "subtle", text: "—" })),
    el("td", { class: "mono nums", text: i.original ? pct(i.original.confidence) : "—" }),
    el("td", {}, i.status === "completed" ? reviewChip(i.review_status) : el("span", { class: "subtle", text: "—" })),
    showSubmitter && el("td", { class: "subtle", text: i.submitted_by }),
    el("td", { class: "text-end" }, el("a", { class: "btn btn-outline-secondary btn-xs", href: `/inspections/${i.id}/`, "aria-label": `Open inspection ${i.id}` }, "Open ", icon("chevron-right"))));
}

function pager(data, page) {
  const pages = Math.max(1, Math.ceil(data.count / 25));
  return el("nav", { class: "pager", "aria-label": "Pagination" },
    el("button", { type: "button", class: "btn btn-outline-secondary btn-sm", disabled: !data.previous, onclick: () => load(page - 1) }, icon("chevron-left"), " Previous"),
    el("span", { class: "readout", text: `Page ${page} of ${pages} · ${data.count} inspection${data.count === 1 ? "" : "s"}` }),
    el("button", { type: "button", class: "btn btn-outline-secondary btn-sm", disabled: !data.next, onclick: () => load(page + 1) }, "Next ", icon("chevron-right")));
}

async function load(page = 1) {
  const params = syncFilters(form, { page: page > 1 ? page : "" });
  const mine = ++request; // drop responses that arrive after a newer filter change
  container.setAttribute("aria-busy", "true");
  try {
    const data = await api(`/api/inspections/${params.size ? `?${params}` : ""}`);
    if (mine !== request) return;
    const filtered = [...new FormData(form).values()].some(Boolean);
    const headers = ["ID", "Submitted", "Product", "File", "Status", "Prediction", "Confidence", "Review", showSubmitter && "By", ""].filter((h) => h !== false);
    fill(container, data.results.length
      ? [el("div", { class: "table-wrap" }, el("table", { class: "table data-table table-hover" },
          el("caption", { class: "visually-hidden", text: "Inspections, newest first" }),
          el("thead", {}, el("tr", {}, headers.map((h) => el("th", { scope: "col" }, h || el("span", { class: "visually-hidden", text: "Actions" }))))),
          el("tbody", {}, data.results.map(row)))),
        pager(data, page)]
      : empty(filtered
        ? { iconName: "funnel", title: "No inspections match these filters", body: "Change or reset the filters." }
        : { iconName: "camera", title: "No inspections yet", body: "Inspections appear here once images are submitted.",
            action: role !== "SUPERVISOR" && el("a", { class: "btn btn-primary btn-sm mt-2", href: "/inspect/" }, "Inspect an image") }));
  } catch (error) {
    if (mine !== request) return;
    if (notBuilt(error)) fill(container, notReady("Inspection history is not available yet",
      "The server does not provide the history list yet. Single inspections still open from their own link after you submit them."));
    else if (error.status === 400) fill(container, el("div", { class: "panel-body" }, errorBox(messages(400, error.data))));
    else fill(container, loadFailed(error));
  }
}

form.addEventListener("change", () => load(1));
form.addEventListener("reset", () => setTimeout(() => load(1))); // after the browser clears the fields
try {
  await productOptions(document.getElementById("f-product"));
  document.getElementById("f-product").firstChild.textContent = "All products";
  document.getElementById("f-product").value = "";
} catch { /* the product filter just lists no products; the table still loads */ }
load(restoreFilters(form));
