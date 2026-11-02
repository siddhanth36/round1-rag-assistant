// Batch inspection: POST /api/inspections/batches/, then poll GET .../batches/<id>/ until done. Contract §3.4.
import { api } from "./api.js";
import { el, empty, fill, fmtDateTime, icon, loadFailed, toast } from "./ui.js";
import { MAX_BATCH, checkImage, errorBox, fmtBytes, messages, notBuilt, notReady, pct, predictionChip, productOptions, statusChip, upload } from "./inspect-common.js";

const $ = (id) => document.getElementById(id);
const form = $("batch-form"), input = $("images"), drop = $("drop");
const POLL_MS = 1500;
let items = []; // {id, file, url, check}
let nextId = 0, available = null, pollTimer = null, tracking = null;

// ---------- selection ----------
function showErrors(lines) {
  $("form-errors").hidden = !lines.length;
  $("form-errors").replaceChildren(...(lines.length ? [errorBox(lines)] : []));
}

function addFiles(files) {
  const room = MAX_BATCH - items.length;
  const accepted = [...files].slice(0, Math.max(0, room));
  showErrors(files.length > accepted.length
    ? [`A batch can contain at most ${MAX_BATCH} images; ${files.length - accepted.length} file${files.length - accepted.length === 1 ? " was" : "s were"} not added.`] : []);
  for (const file of accepted) {
    const item = { id: nextId++, file, url: URL.createObjectURL(file), check: null };
    items.push(item);
    checkImage(file).then((check) => { item.check = check; renderThumbs(); });
  }
  renderThumbs();
}

function removeItem(id) {
  const item = items.find((i) => i.id === id);
  URL.revokeObjectURL(item.url);
  items = items.filter((i) => i !== item);
  showErrors([]);
  renderThumbs();
  (document.querySelector(".thumb button") || input).focus();
}

function clearItems() {
  items.forEach((i) => URL.revokeObjectURL(i.url));
  items = [];
  renderThumbs();
}

function renderThumbs() {
  $("count").textContent = `${items.length} / ${MAX_BATCH} images`;
  $("clear").hidden = !items.length;
  $("thumbs").replaceChildren(...items.map((item) => {
    const c = item.check;
    return el("li", { class: `thumb ${c?.error ? "is-invalid" : ""}` },
      el("img", { src: item.url, alt: "" }),
      el("div", { class: "thumb-meta" },
        el("span", { class: "mono thumb-name", text: item.file.name, title: item.file.name }),
        el("span", { class: "readout", text: c?.width ? `${fmtBytes(item.file.size)} · ${c.width}×${c.height}` : fmtBytes(item.file.size) }),
        !c ? el("span", { class: "subtle small-ui", text: "Checking…" })
          : c.error ? el("span", { class: "thumb-error" }, icon("x-octagon"), ` ${c.error}`)
          : el("span", { class: "thumb-ok" }, icon("check-circle"), ` ${c.format}, ready`)),
      el("button", { type: "button", class: "btn btn-outline-secondary btn-xs", "aria-label": `Remove ${item.file.name}`, onclick: () => removeItem(item.id) }, icon("x-lg")));
  }));
  updateSubmit();
}

function updateSubmit() {
  const invalid = items.filter((i) => i.check?.error).length;
  const checking = items.some((i) => !i.check);
  $("submit").disabled = available !== true || !items.length || invalid > 0 || checking || !form.product_id.value;
  $("submit-help").textContent = available === false ? "Batch processing is not available yet."
    : !form.product_id.value ? "Select a product."
    : !items.length ? "Add at least one image."
    : invalid ? `Remove ${invalid} invalid image${invalid === 1 ? "" : "s"} to submit.`
    : checking ? "Checking images…" : "";
}

input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });
form.product_id.addEventListener("change", updateSubmit);
$("clear").addEventListener("click", () => { clearItems(); showErrors([]); input.focus(); });
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("is-over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("is-over"); addFiles(e.dataTransfer.files); });

// ---------- submit ----------
function setProgress(percent, text) {
  $("progress").hidden = percent === null;
  if (percent === null) return;
  const bar = $("progress").querySelector(".progress");
  bar.setAttribute("aria-valuenow", String(percent));
  bar.querySelector(".progress-bar").style.width = `${percent}%`;
  $("progress-text").textContent = text;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if ($("submit").disabled) return;
  const data = new FormData();
  data.append("product_id", form.product_id.value);
  items.forEach((i) => data.append("images", i.file, i.file.name));
  $("submit").disabled = true;
  setProgress(0, "Uploading…");
  const { status, body } = await upload("/api/inspections/batches/", data, (p) => setProgress(p, p < 100 ? "Uploading…" : "Queuing images…"));
  setProgress(null);
  if (status === 202) {
    toast(`Batch #${body.batch_id} submitted with ${body.items.length} image${body.items.length === 1 ? "" : "s"}.`);
    clearItems();
    showErrors([]);
    track(body);
    loadRecent();
    return;
  }
  if (status === 404 && !body) setAvailable(false);
  showErrors(messages(status, body));
  updateSubmit();
});

// ---------- tracker ----------
function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }

function itemRow(item, index) {
  return el("tr", {},
    el("td", { class: "mono nums", text: String(index + 1) }),
    el("td", { class: "mono", text: item.original_filename }),
    el("td", {}, statusChip(item.status)),
    el("td", {}, item.original ? predictionChip(item.original.predicted_class) : el("span", { class: "subtle", text: "—" })),
    el("td", { class: "mono nums", text: item.original ? pct(item.original.confidence) : "—" }),
    el("td", { class: "mono nums", text: item.original ? pct(item.original.defect_probability) : "—" }),
    el("td", { class: "reason-cell" }, item.status === "failed"
      ? el("span", { class: "thumb-error", text: item.failure_reason || "No reason was recorded." })
      : el("a", { href: `/inspections/${item.id}/`, "aria-label": `Open inspection ${item.id}` }, `#${item.id}`)));
}

function renderBatch(batch) {
  const c = batch.counts;
  $("tracker-panel").hidden = false;
  $("tracker-h").textContent = `Batch #${batch.batch_id}`;
  $("tracker-summary").replaceChildren(
    el("span", { class: "tag", text: batch.product }),
    el("span", { class: "readout", text: `${c.completed} completed · ${c.processing} processing · ${c.queued} queued · ${c.failed} failed` }),
    batch.done ? el("span", { class: "chip chip-quiet" }, icon("check2-all"), "Finished") : el("span", { class: "chip chip-caution" }, icon("arrow-repeat"), "Running"));
  fill($("tracker"),
    batch.warning && el("div", { class: "notice notice-caution m-3", role: "alert" }, icon("exclamation-triangle"),
      el("div", {}, el("strong", { text: "Worker offline. " }), batch.warning)),
    el("div", { class: "table-wrap" }, el("table", { class: "table data-table" },
      el("caption", { class: "visually-hidden", text: `Items in batch ${batch.batch_id}, in upload order` }),
      el("thead", {}, el("tr", {}, ["#", "File", "Status", "Prediction", "Confidence", "Defect prob.", "Record / reason"].map((h) => el("th", { scope: "col", text: h })))),
      el("tbody", {}, batch.items.map(itemRow)))),
    el("p", { class: "subtle small-ui px-3 py-2 m-0", text: batch.done
      ? `Finished. Submitted by ${batch.submitted_by} at ${fmtDateTime(batch.created_at)}.`
      : `Updating every ${POLL_MS / 1000} s until every item is completed or failed.` }));
}

async function poll(id) {
  pollTimer = null;
  try {
    const batch = await api(`/api/inspections/batches/${id}/`);
    if (tracking !== id) return;
    renderBatch(batch);
    if (!batch.done) pollTimer = setTimeout(() => poll(id), POLL_MS);
    else loadRecent();
  } catch (error) {
    if (tracking !== id) return;
    if (error.status === 0) { // keep trying: the batch keeps running on the server
      $("tracker").querySelector(".poll-note")?.remove();
      $("tracker").prepend(el("div", { class: "notice poll-note m-3", role: "status" }, icon("wifi-off"), el("div", { text: "Lost connection to the server. Retrying…" })));
      pollTimer = setTimeout(() => poll(id), POLL_MS * 2);
    } else {
      $("tracker-panel").hidden = false;
      fill($("tracker"), notBuilt(error) ? notReady("Batch status is not available yet", "The server does not provide batch tracking yet.") : loadFailed(error));
    }
  }
}

function track(batchOrId) {
  stopPolling();
  const id = typeof batchOrId === "object" ? batchOrId.batch_id : batchOrId;
  tracking = id;
  history.replaceState(null, "", `${location.pathname}?batch=${id}`);
  if (typeof batchOrId === "object") {
    renderBatch(batchOrId);
    if (!batchOrId.done) pollTimer = setTimeout(() => poll(id), POLL_MS);
  } else poll(id);
  $("tracker-panel").scrollIntoView({ block: "nearest" });
}

// Leaving the page stops polling; coming back (back/forward cache) resumes it.
addEventListener("pagehide", stopPolling);
addEventListener("pageshow", (e) => { if (e.persisted && tracking !== null && !pollTimer) poll(tracking); });

// ---------- recent batches + availability ----------
function setAvailable(value) {
  available = value;
  $("availability").replaceChildren(...(value === false ? [el("div", { class: "notice notice-caution mb-4", role: "status" }, icon("plug"),
    el("div", {}, el("strong", { text: "Batch processing is not available yet. " }),
      "The server does not provide the batch endpoints yet, so nothing can be queued. You can still ",
      el("a", { href: "/inspect/", text: "inspect single images" }), "."))] : []));
  updateSubmit();
}

async function loadRecent() {
  try {
    const batches = await api("/api/inspections/batches/");
    setAvailable(true);
    fill($("recent"), batches.length
      ? el("div", { class: "table-wrap" }, el("table", { class: "table data-table table-hover" },
          el("caption", { class: "visually-hidden", text: "Recent batches, newest first" }),
          el("thead", {}, el("tr", {}, ["Batch", "Submitted", "Product", "By", "Items", "State", ""].map((h) =>
            el("th", { scope: "col" }, h || el("span", { class: "visually-hidden", text: "Actions" }))))),
          el("tbody", {}, batches.map((b) => el("tr", {},
            el("td", { class: "mono", text: `#${b.batch_id}` }),
            el("td", { class: "readout", text: fmtDateTime(b.created_at) }),
            el("td", {}, el("span", { class: "tag", text: b.product })),
            el("td", { class: "subtle", text: b.submitted_by }),
            el("td", { class: "readout", text: `${b.counts.completed} ok · ${b.counts.failed} failed · ${b.counts.queued + b.counts.processing} pending` }),
            el("td", {}, b.done ? el("span", { class: "chip chip-quiet" }, icon("check2-all"), "Finished") : el("span", { class: "chip chip-caution" }, icon("arrow-repeat"), "Running")),
            el("td", { class: "text-end" }, el("button", { type: "button", class: "btn btn-outline-secondary btn-xs", "aria-label": `Show batch ${b.batch_id}`,
              onclick: () => track(b.batch_id) }, icon("eye"), " Show")))))))
      : empty({ iconName: "images", title: "No batches yet", body: "Batches you submit appear here." }));
  } catch (error) {
    if (notBuilt(error)) {
      setAvailable(false);
      fill($("recent"), notReady("Batch history is not available yet", "This list appears once the server provides batch processing."));
    } else fill($("recent"), loadFailed(error));
  }
}

try {
  await productOptions(form.product_id);
} catch (error) {
  form.product_id.replaceChildren(el("option", { value: "", text: "Products could not be loaded" }));
  showErrors(messages(error.status, error.data));
}
renderThumbs();
await loadRecent();
const resume = Number(new URLSearchParams(location.search).get("batch"));
if (resume && available) track(resume);
