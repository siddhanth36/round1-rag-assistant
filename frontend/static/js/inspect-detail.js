// Inspection detail: GET /api/inspections/<id>/ (§3.3) and supervisor review POST .../review/ (§3.6).
// `original` is the immutable model output; reviews are separate, append-only records shown beside it.
import { api } from "./api.js";
import { el, empty, fill, fmtDateTime, icon, loadFailed, toast } from "./ui.js";
import { errorBox, failedPlate, messages, notBuilt, predictionChip, reviewChip, role, statusChip, verdict } from "./inspect-common.js";

const container = document.getElementById("detail");
const id = container.dataset.id;
const MAX_COMMENT = 1000;
let products = {};

const row = (label, value) => el("div", {}, el("dt", { text: label }), el("dd", {}, value ?? el("span", { class: "subtle", text: "—" })));
const when = (iso) => iso && el("span", { class: "readout", text: fmtDateTime(iso) });

function imagePanel(i) {
  return el("section", { class: "panel", "aria-labelledby": "image-h" },
    el("div", { class: "panel-head" }, el("h2", { id: "image-h", text: "Inspected image" }), i.image_format && el("span", { class: "readout", text: `${i.image_format} · ${i.width}×${i.height} px` })),
    i.image_url
      ? el("div", { class: "detail-image" }, el("img", { src: i.image_url, alt: `Inspection ${i.id}: ${i.original_filename}` }))
      : empty({ iconName: "image", title: "No image stored", body: "This upload was rejected during validation, so the file was never stored or sent to the model." }));
}

function predictionPanel(i) {
  return el("section", { class: "panel", "aria-labelledby": "prediction-h" },
    el("div", { class: "panel-head" }, el("h2", { id: "prediction-h", text: "Original model prediction" }),
      el("span", { class: "chip chip-neutral" }, icon("lock"), "Immutable")),
    el("div", { class: "panel-body stack" },
      i.original ? verdict(i.original, { heading: "Model output" })
        : i.status === "failed" ? failedPlate(i.failure_reason)
        : el("div", { class: "d-flex gap-2 align-items-center" }, statusChip(i.status), el("span", { class: "muted small-ui", text: "No prediction yet. Reload to check again." })),
      i.completed_at && el("p", { class: "subtle small-ui m-0" }, "Recorded ", when(i.completed_at), ". Reviews never change this result.")));
}

function recordPanel(i) {
  const product = products[i.product];
  return el("section", { class: "panel", "aria-labelledby": "record-h" },
    el("div", { class: "panel-head" }, el("h2", { id: "record-h", text: "Record" }), statusChip(i.status)),
    el("dl", { class: "record panel-body" },
      row("Product", el("span", {}, el("span", { class: "tag", text: i.product }), product ? ` ${product.product_name}` : "")),
      row("File", el("span", { class: "mono", text: i.original_filename })),
      row("Batch", i.batch ? el("a", { class: "mono", href: `/inspect/batch/?batch=${i.batch}`, text: `#${i.batch}` }) : null),
      row("Submitted by", i.submitted_by),
      row("Submitted", when(i.created_at)),
      row("Started", when(i.started_at)),
      row("Completed", when(i.completed_at))));
}

function reviewItem(r, current) {
  return el("li", { class: `review ${current ? "is-current" : ""}` },
    el("div", { class: "review-head" },
      reviewChip(r.decision),
      el("span", {}, "Reviewed class: ", predictionChip(r.reviewed_class)),
      current && el("span", { class: "chip chip-neutral", text: "Current" })),
    r.comment ? el("p", { class: "review-comment", text: r.comment }) : el("p", { class: "subtle small-ui m-0", text: "No comment." }),
    el("p", { class: "readout m-0" }, `${r.reviewer} · ${fmtDateTime(r.created_at)}`));
}

function reviewForm(i) {
  const predicted = i.original.predicted_class;
  const other = predicted === "Defective" ? "Normal" : "Defective";
  const errors = el("div");
  const counter = el("span", { class: "form-text", id: "comment-count", text: `0 / ${MAX_COMMENT}` });
  const comment = el("textarea", { class: "form-control", id: "comment", name: "comment", rows: "3", maxlength: String(MAX_COMMENT), required: true,
    "aria-describedby": "comment-help comment-count", oninput: () => { counter.textContent = `${comment.value.length} / ${MAX_COMMENT}`; } });
  const choice = (value, label, hint) => el("label", { class: "decision" },
    el("input", { class: "form-check-input", type: "radio", name: "decision", value, required: true }),
    el("span", {}, el("strong", { text: label }), el("span", { class: "subtle small-ui d-block", text: hint })));
  const submit = el("button", { class: "btn btn-primary", type: "submit" }, icon("send-check"), " Submit review");

  const form = el("form", { class: "panel-body stack", novalidate: true, onsubmit: async (event) => {
    event.preventDefault();
    const decision = form.decision.value;
    const problems = [];
    if (!decision) problems.push("Choose confirm or correct.");
    if (!comment.value.trim()) problems.push("Enter a review comment.");
    errors.replaceChildren(...(problems.length ? [errorBox(problems)] : []));
    if (problems.length) return;
    submit.disabled = true;
    try {
      const updated = await api(`/api/inspections/${i.id}/review/`, { method: "POST",
        body: { decision, reviewed_class: decision === "confirmed" ? predicted : other, comment: comment.value.trim() } });
      toast(`Review saved: ${decision === "confirmed" ? `confirmed ${predicted}` : `corrected to ${other}`}.`);
      render(updated);
      document.getElementById("reviews-h")?.focus();
    } catch (error) {
      submit.disabled = false;
      if (notBuilt(error)) errors.replaceChildren(el("div", { class: "notice notice-caution", role: "alert" }, icon("plug"),
        el("div", { text: "Supervisor review is not available yet: the server does not accept reviews yet. Nothing was saved." })));
      else if (error.status === 403) { errors.replaceChildren(errorBox(messages(403, error.data))); reload(); } // prove nothing changed
      else errors.replaceChildren(errorBox(messages(error.status, error.data)));
    }
  } },
    el("fieldset", { class: "stack gap-2" },
      el("legend", { class: "form-label", text: "Decision" }),
      choice("confirmed", `Confirm: ${predicted}`, "The model's prediction is correct."),
      choice("corrected", `Correct to: ${other}`, `The product is actually ${other}.`)),
    el("div", {},
      el("label", { class: "form-label", for: "comment", text: "Review comment (required)" }), comment,
      el("div", { class: "d-flex justify-content-between gap-2" }, el("span", { class: "form-text", id: "comment-help", text: "Say what you saw, for the audit trail." }), counter)),
    errors,
    el("div", {}, submit));
  return form;
}

function reviewPanel(i) {
  const reviews = i.reviews || [];
  let action = null;
  if (role === "SUPERVISOR" && i.status === "completed") action = reviewForm(i);
  else if (role === "SUPERVISOR") action = el("p", { class: "panel-body muted small-ui m-0", text: "Only completed inspections can be reviewed." });
  else if (i.status === "completed") action = el("p", { class: "panel-body muted small-ui m-0" }, icon("shield-lock"), " Only Supervisors can review inspections.");
  return el("section", { class: "panel", "aria-labelledby": "reviews-h" },
    el("div", { class: "panel-head" }, el("h2", { id: "reviews-h", tabindex: "-1", text: "Human review" }), reviewChip(i.review_status)),
    reviews.length
      ? el("ol", { class: "reviews", "aria-label": "Review history, newest first" }, reviews.map((r, n) => reviewItem(r, n === 0)))
      : el("p", { class: "panel-body muted small-ui m-0 border-bottom", text: "No reviews yet." }),
    action && el("div", { class: "review-action" }, role === "SUPERVISOR" && i.status === "completed" && el("h3", { class: "px-3 pt-3", text: reviews.length ? "Add a review" : "Review this prediction" }), action));
}

function render(i) {
  fill(container, el("div", { class: "detail-grid" },
    el("div", { class: "stack" }, imagePanel(i), recordPanel(i)),
    el("div", { class: "stack" }, predictionPanel(i), reviewPanel(i))));
}

async function reload() {
  try {
    render(await api(`/api/inspections/${id}/`));
  } catch (error) {
    fill(container, error.status === 404
      ? el("div", { class: "panel" }, empty({ iconName: "search", title: "Inspection not found",
          body: "It doesn't exist, or it isn't visible to your account.", action: el("a", { class: "btn btn-outline-secondary btn-sm mt-2", href: "/inspections/" }, "Back to history") }))
      : el("div", { class: "panel" }, loadFailed(error)));
  }
}

try {
  products = Object.fromEntries((await api("/api/products/")).map((p) => [p.product_id, p]));
} catch { /* the product name is optional; the ID still shows */ }
reload();
