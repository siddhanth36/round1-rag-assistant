// Model metrics: GET /api/inspections/metrics/ (contract §3.7). Every number comes from the server.
import { api } from "./api.js";
import { el, empty, fill, icon, loadFailed } from "./ui.js";
import { notBuilt, notReady, pct } from "./inspect-common.js";

const container = document.getElementById("metrics");

// Rows = actual, columns = predicted, both in m.labels order (Normal, Defective).
function matrix(m) {
  const name = { "0,0": "True negatives", "0,1": "False positives", "1,0": "False negatives", "1,1": "True positives" };
  return el("table", { class: "matrix" },
    el("caption", { text: "Confusion matrix: rows are the actual class, columns the predicted class." }),
    el("thead", {},
      el("tr", {}, el("td", { class: "matrix-corner" }), el("th", { scope: "colgroup", colspan: String(m.labels.length), class: "matrix-axis", text: "Predicted" })),
      el("tr", {}, el("td", { class: "matrix-corner", "aria-hidden": "true" }, el("span", { class: "matrix-axis", text: "Actual ↓" })),
        m.labels.map((l) => el("th", { scope: "col", text: l })))),
    el("tbody", {}, m.matrix.map((cells, r) => el("tr", {},
      el("th", { scope: "row", text: m.labels[r] }),
      cells.map((value, c) => el("td", { class: `matrix-cell ${r === c ? "is-hit" : value ? "is-miss" : ""}` },
        el("span", { class: "matrix-n", text: String(value) }),
        el("span", { class: "matrix-k", text: name[`${r},${c}`] || "" })))))));
}

function render(m) {
  const scored = m.total - m.failed.length;
  fill(container, el("div", { class: "metrics-grid" },
    el("section", { class: "panel", "aria-labelledby": "matrix-h" },
      el("div", { class: "panel-head" }, el("h2", { id: "matrix-h", text: "Confusion matrix" }), el("span", { class: "readout", text: `Defective = positive class` })),
      el("div", { class: "panel-body" }, matrix(m))),
    el("div", { class: "stack" },
      el("section", { class: "panel", "aria-labelledby": "summary-h" },
        el("div", { class: "panel-head" }, el("h2", { id: "summary-h", text: "Summary" })),
        el("dl", { class: "record panel-body" },
          el("div", {}, el("dt", { text: "Defective recall" }), el("dd", {},
            el("span", { class: "readout-lg", text: m.recall === null ? "n/a" : pct(m.recall) }),
            el("span", { class: "subtle small-ui d-block", text: m.recall === null ? "No Defective samples were scored." : `TP ${m.tp} / (TP ${m.tp} + FN ${m.fn})` }))),
          el("div", {}, el("dt", { text: "Model version" }), el("dd", { class: "mono", text: m.model_version })),
          el("div", {}, el("dt", { text: "Labeled samples" }), el("dd", { class: "mono nums", text: String(m.total) })),
          el("div", {}, el("dt", { text: "Scored" }), el("dd", { class: "mono nums", text: String(scored) })),
          el("div", {}, el("dt", { text: "Not scored" }), el("dd", { class: "mono nums", text: String(m.failed.length) })))),
      el("section", { class: "panel", "aria-labelledby": "limits-h" },
        el("div", { class: "panel-head" }, el("h2", { id: "limits-h", text: "Model limitations" })),
        el("ul", { class: "limits panel-body" }, m.limitations.map((l) => el("li", {}, icon("exclamation-circle"), el("span", { text: l })))))),
    el("section", { class: "panel metrics-wide", "aria-labelledby": "failed-h" },
      el("div", { class: "panel-head" }, el("h2", { id: "failed-h", text: "Samples without a prediction" }), el("span", { class: "readout", text: String(m.failed.length) })),
      m.failed.length
        ? el("div", { class: "table-wrap" }, el("table", { class: "table data-table" },
            el("caption", { class: "visually-hidden", text: "Samples that could not be scored" }),
            el("thead", {}, el("tr", {}, ["Image", "File", "Reason"].map((h) => el("th", { scope: "col", text: h })))),
            el("tbody", {}, m.failed.map((f) => el("tr", {},
              el("td", { class: "mono", text: f.image_id }), el("td", { class: "mono", text: f.file }), el("td", { text: f.reason }))))))
        : empty({ iconName: "check2-all", title: "Every sample was scored", body: "No sample was missing or failed validation." }))));
}

try {
  render(await api("/api/inspections/metrics/"));
} catch (error) {
  if (notBuilt(error)) fill(container, el("div", { class: "panel" }, notReady("Metrics are not available yet",
    "The server does not provide the metrics endpoint yet. No numbers are shown until it does.")));
  else if (error.status === 404) fill(container, el("div", { class: "panel" }, notReady("Metrics are not available yet",
    `${error.data?.detail || "Metrics have not been generated yet."} An administrator runs the evaluation on the server to produce them.`)));
  else fill(container, el("div", { class: "panel" }, loadFailed(error)));
}
