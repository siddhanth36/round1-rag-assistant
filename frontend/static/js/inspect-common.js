// Shared pieces for the Round 2 inspection pages (docs/ROUND2_API_CONTRACT.md).
// Client checks mirror the server's rules for fast feedback; the server stays the authority.
import { api, csrfToken, errorLines, handleStatus } from "./api.js";
import { el, empty, icon } from "./ui.js";

export const role = document.body.dataset.role;
export const MAX_BYTES = 5 * 1024 * 1024; // 5 MiB, contract §3.2
export const MIN_SIDE = 64, MAX_SIDE = 4096;
export const MAX_BATCH = 10;

// An endpoint that isn't deployed yet: Django's HTML 404 (no JSON body) or DRF's 405.
// A JSON 404 is a real answer ("doesn't exist or isn't visible to you", "metrics not generated").
export const notBuilt = (error) => (error.status === 404 && !error.data) || error.status === 405;

export function notReady(title, body) {
  return empty({ iconName: "plug", title, body });
}

// Readable error lines for any failed request.
export function messages(status, data) {
  if (status === 0) return ["Network error: the server could not be reached."];
  const lines = errorLines(data);
  return lines.length ? lines : [`The server answered with HTTP ${status}.`];
}

export function errorBox(lines) {
  return el("div", { class: "notice notice-danger", role: "alert" }, icon("x-octagon"),
    el("div", {}, lines.length === 1 ? lines[0] : el("ul", {}, lines.map((line) => el("li", { text: line })))));
}

// Multipart POST with upload progress (the same XHR pattern as manage.js). Resolves {status, body}.
export async function upload(url, formData, onProgress) {
  const token = await csrfToken();
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("X-CSRFToken", token);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* HTML error page */ }
      if (xhr.status >= 400) handleStatus(xhr.status, body); // 401 -> login, 403 -> toast
      resolve({ status: xhr.status, body });
    };
    xhr.onerror = () => resolve({ status: 0, body: null });
    xhr.send(formData);
  });
}

export async function productOptions(select) {
  const products = await api("/api/products/");
  select.replaceChildren(el("option", { value: "", text: products.length ? "Select a product" : "No active products" }),
    ...products.map((p) => el("option", { value: p.product_id, text: `${p.product_id} · ${p.product_name}` })));
  if (products.length === 1) select.value = products[0].product_id;
  return products;
}

// ---------- client-side file checks (same wording as the server's failure_reason) ----------
const thousands = (n) => n.toLocaleString("en-US");
export const fmtBytes = (n) => (n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(2)} MB`);

async function sniff(file) {
  const b = new Uint8Array(await file.slice(0, 8).arrayBuffer());
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "JPEG";
  if ([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a].every((v, i) => b[i] === v)) return "PNG";
  return null;
}

// Returns {format, width, height, error}. error is null when the file passes every check.
export async function checkImage(file) {
  if (!file.size) return { error: "The file is empty." };
  if (file.size > MAX_BYTES) return { error: `The file is ${thousands(file.size)} bytes; the limit is 5 MiB (5,242,880 bytes).` };
  const format = await sniff(file);
  if (!format) return { error: "The file content is not a JPEG or PNG image." };
  let bitmap;
  try { bitmap = await createImageBitmap(file); } catch { return { format, error: "The image data is corrupt or truncated." }; }
  const { width, height } = bitmap;
  bitmap.close();
  const ok = (side) => side >= MIN_SIDE && side <= MAX_SIDE;
  if (!ok(width) || !ok(height)) return { format, width, height, error: `The image is ${width}×${height} px; each side must be 64–4096 px.` };
  return { format, width, height, error: null };
}

// ---------- state vocabulary: icon + label + shape + color, never color alone ----------
const PREDICTION = {
  Normal: { cls: "chip-ok", iconName: "check-circle", label: "Normal" },
  Defective: { cls: "chip-danger", iconName: "exclamation-diamond-fill", label: "Defective" },
};
export function predictionChip(cls) {
  const p = PREDICTION[cls];
  return p ? el("span", { class: `chip ${p.cls}` }, icon(p.iconName), p.label) : el("span", { class: "subtle", text: "—" });
}

const STATUS = {
  queued: { cls: "chip-neutral", iconName: "hourglass", label: "Queued" },
  processing: { cls: "chip-caution", iconName: "arrow-repeat", label: "Processing" },
  completed: { cls: "chip-quiet", iconName: "check2", label: "Completed" },
  failed: { cls: "chip-failed", iconName: "x-octagon", label: "Failed" },
};
export function statusChip(status) {
  const s = STATUS[status] || { cls: "chip-neutral", iconName: "question-circle", label: status || "Unknown" };
  return el("span", { class: `chip ${s.cls}` }, icon(s.iconName), s.label);
}

const REVIEW = {
  unreviewed: { cls: "chip-neutral", iconName: "dash-circle", label: "Unreviewed" },
  confirmed: { cls: "chip-ok", iconName: "person-check", label: "Confirmed" },
  corrected: { cls: "chip-caution", iconName: "pencil-square", label: "Corrected" },
};
export function reviewChip(status) {
  const r = REVIEW[status] || REVIEW.unreviewed;
  return el("span", { class: `chip ${r.cls}` }, icon(r.iconName), r.label);
}

// Scores are shown exactly as returned, as a percentage with one decimal.
export const pct = (x) => (x === null || x === undefined ? "—" : `${(x * 100).toFixed(1)}%`);

// The model's prediction plate. `original` = {predicted_class, confidence, defect_probability, model_version}.
export function verdict(original, { heading = "Model prediction" } = {}) {
  const defective = original.predicted_class === "Defective";
  const p = PREDICTION[original.predicted_class];
  return el("section", { class: `verdict ${defective ? "verdict--defective" : "verdict--normal"}`, "aria-label": heading },
    el("div", { class: "verdict-head" },
      el("span", { class: "verdict-kicker", text: heading }),
      el("span", { class: "verdict-class" }, icon(p?.iconName || "question-circle"), original.predicted_class)),
    el("dl", { class: "verdict-data" },
      el("div", {}, el("dt", { text: "Confidence" }), el("dd", { class: "readout-lg", text: pct(original.confidence) })),
      el("div", {}, el("dt", { text: "Defect probability" }),
        el("dd", {}, el("span", { class: "readout-lg", text: pct(original.defect_probability) }),
          el("span", { class: "meter", role: "img", "aria-label": `Defect probability ${pct(original.defect_probability)}` },
            el("span", { style: `width: ${Math.max(0, Math.min(1, original.defect_probability)) * 100}%` })))),
      el("div", {}, el("dt", { text: "Model version" }), el("dd", { class: "mono", text: original.model_version }))));
}

export function failedPlate(reason, { title = "Inspection failed" } = {}) {
  return el("section", { class: "verdict verdict--failed", "aria-label": title },
    el("div", { class: "verdict-head" },
      el("span", { class: "verdict-kicker", text: "No prediction" }),
      el("span", { class: "verdict-class" }, icon("x-octagon"), title)),
    el("p", { class: "verdict-reason", text: reason || "No reason was recorded." }),
    el("p", { class: "subtle small-ui m-0", text: "The image was not classified, so no result is shown." }));
}
