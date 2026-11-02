// Single image inspection: POST /api/inspections/ (multipart, synchronous model run). Contract §3.2.
import { el, icon, toast } from "./ui.js";
import { checkImage, errorBox, failedPlate, fmtBytes, messages, productOptions, upload, verdict } from "./inspect-common.js";

const $ = (id) => document.getElementById(id);
const form = $("inspect-form"), input = $("image"), drop = $("drop"), result = $("result");
let previewUrl = null;

function showErrors(lines) {
  $("form-errors").hidden = !lines.length;
  $("form-errors").replaceChildren(...(lines.length ? [errorBox(lines)] : []));
}

function setProgress(percent, text) {
  $("progress").hidden = percent === null;
  if (percent === null) return;
  const bar = $("progress").querySelector(".progress");
  bar.setAttribute("aria-valuenow", String(percent));
  bar.querySelector(".progress-bar").style.width = `${percent}%`;
  $("progress-text").textContent = text;
}

async function showFile() {
  const file = input.files[0];
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  $("preview").hidden = !file;
  $("clear").hidden = !file;
  showErrors([]);
  if (!file) return;
  previewUrl = URL.createObjectURL(file);
  $("preview-img").src = previewUrl;
  $("file-meta").replaceChildren(el("span", { class: "mono", text: file.name }), el("span", { class: "readout", text: fmtBytes(file.size) }));
  const check = await checkImage(file);
  if (input.files[0] !== file) return; // a newer file was picked meanwhile
  if (check.width) $("file-meta").append(el("span", { class: "readout", text: `${check.format} · ${check.width}×${check.height} px` }));
  if (check.error) showErrors([check.error]);
}

input.addEventListener("change", showFile);
$("clear").addEventListener("click", () => { input.value = ""; showFile(); });

drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("is-over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("is-over");
  const file = e.dataTransfer.files[0];
  if (!file) return;
  const list = new DataTransfer();
  list.items.add(file);
  input.files = list.files;
  showFile();
});

function detailLink(id) {
  return el("a", { class: "btn btn-outline-secondary btn-sm", href: `/inspections/${id}/` }, icon("box-arrow-up-right"), " Open inspection record");
}

function render(status, body, file) {
  if (status === 201) {
    result.replaceChildren(el("div", { class: "stack" },
      verdict(body.original),
      el("p", { class: "subtle small-ui m-0" }, `Inspection #${body.id} · `, el("span", { class: "mono", text: body.original_filename }),
        ` · ${body.width}×${body.height} px ${body.image_format}`),
      el("div", {}, detailLink(body.id))));
    toast(`Inspection #${body.id}: ${body.original.predicted_class}.`);
    return;
  }
  // 400 rejected upload or 502 model error: the attempt is saved as a failed record.
  if (body?.status === "failed") {
    result.replaceChildren(el("div", { class: "stack" },
      failedPlate(body.failure_reason, { title: status === 502 ? "Model error" : "Image rejected" }),
      el("p", { class: "subtle small-ui m-0" }, `Saved as failed inspection #${body.id} · `, el("span", { class: "mono", text: file.name })),
      el("div", {}, detailLink(body.id))));
    return;
  }
  result.replaceChildren(el("p", { class: "muted small-ui m-0", text: "Nothing was inspected." }));
  showErrors(messages(status, body));
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = input.files[0];
  const errors = [];
  if (!form.product_id.value) errors.push("Select a product.");
  if (!file) errors.push("Choose a JPEG or PNG image.");
  else {
    const check = await checkImage(file);
    if (check.error) errors.push(check.error);
  }
  showErrors(errors);
  if (errors.length) return;

  $("submit").disabled = true;
  result.setAttribute("aria-busy", "true");
  result.replaceChildren(el("div", { class: "inspecting" }, el("span", { class: "scanbar", "aria-hidden": "true" }),
    el("span", { class: "muted small-ui", text: "Inspecting the image…" })));
  setProgress(0, "Uploading…");
  const { status, body } = await upload("/api/inspections/", new FormData(form),
    (p) => setProgress(p, p < 100 ? "Uploading…" : "Running the model…"));
  setProgress(null);
  $("submit").disabled = false;
  result.removeAttribute("aria-busy");
  render(status, body, file);
});

try {
  await productOptions(form.product_id);
} catch (error) {
  form.product_id.replaceChildren(el("option", { value: "", text: "Products could not be loaded" }));
  showErrors(messages(error.status, error.data));
}
