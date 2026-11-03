// Product Management (Admin): GET /api/products/manage/, POST /api/products/, PATCH /api/products/<product_id>/.
// The server enforces Admin-only access and validation; the checks here only give faster feedback.
import { api } from "./api.js";
import { el, empty, fill, fmtDateTime, icon, loadFailed, toast } from "./ui.js";
import { errorBox, messages } from "./inspect-common.js";

const PRODUCT_ID = /^[A-Z0-9-]+$/; // the model's validator; max 32 characters
const LABELS = { product_id: "Product ID", product_name: "Product name", description: "Description", is_active: "Active" };

const container = document.getElementById("products");
const filters = document.getElementById("filters");
const form = document.getElementById("product-form");
const fields = { id: form.elements.product_id, name: form.elements.product_name, description: form.elements.description, active: form.elements.is_active };
const formErrors = document.getElementById("product-errors");
const submit = document.getElementById("product-submit");
const confirmOk = document.getElementById("confirm-ok");

let products = null; // null until the first successful load
let editing = null; // product being edited; null when adding
let pendingDeactivate = null;

const modal = (id) => window.bootstrap.Modal.getOrCreateInstance(document.getElementById(id));

// ---------- listing ----------
function setStats() {
  const active = products ? products.filter((p) => p.is_active).length : null;
  document.getElementById("stat-total").textContent = products ? String(products.length) : "—";
  document.getElementById("stat-active").textContent = products ? String(active) : "—";
  document.getElementById("stat-inactive").textContent = products ? String(products.length - active) : "—";
}

const statusChip = (active) => active
  ? el("span", { class: "chip chip-ok" }, icon("check-circle"), "Active")
  : el("span", { class: "chip chip-neutral" }, icon("pause-circle"), "Inactive");

function row(p) {
  return el("tr", { class: p.is_active ? "" : "is-inactive" },
    el("td", {}, el("span", { class: "tag", text: p.product_id })),
    el("td", { text: p.product_name }),
    el("td", { class: "subtle desc-cell", text: p.description || "—", title: p.description || null }),
    el("td", {}, statusChip(p.is_active)),
    el("td", { class: "readout", text: fmtDateTime(p.created_at).slice(0, 10), title: fmtDateTime(p.created_at) }),
    el("td", { class: "text-end" }, el("div", { class: "row-actions" },
      el("button", { type: "button", class: "btn btn-outline-secondary btn-xs", "aria-label": `Edit ${p.product_id}`, onclick: () => openForm(p) },
        icon("pencil"), " Edit"),
      p.is_active
        ? el("button", { type: "button", class: "btn btn-outline-danger btn-xs", "aria-label": `Deactivate ${p.product_id}`, onclick: () => askDeactivate(p) },
            icon("pause-circle"), " Deactivate")
        : el("button", { type: "button", class: "btn btn-outline-secondary btn-xs", "aria-label": `Activate ${p.product_id}`, onclick: () => setActive(p, true) },
            icon("play-circle"), " Activate"))));
}

function render() {
  if (!products) return;
  const q = filters.elements.q.value.trim().toLowerCase();
  const status = filters.elements.status.value;
  const shown = products.filter((p) => (!q || p.product_id.toLowerCase().includes(q) || p.product_name.toLowerCase().includes(q))
    && (!status || p.is_active === (status === "active")));
  if (!products.length) {
    return fill(container, empty({ iconName: "box-seam", title: "No products registered",
      body: "Add a product to make it available for inspections.",
      action: el("button", { type: "button", class: "btn btn-primary btn-sm mt-2", onclick: () => openForm(null) }, icon("plus-lg"), " Add Product") }));
  }
  if (!shown.length) return fill(container, empty({ iconName: "funnel", title: "No products match", body: "Change or reset the search and filter." }));
  fill(container, el("div", { class: "table-wrap" }, el("table", { class: "table data-table table-hover" },
    el("caption", { class: "visually-hidden", text: "Registered products, ordered by product ID" }),
    el("thead", {}, el("tr", {}, ["Product ID", "Product name", "Description", "Status", "Created", "Actions"].map((h) =>
      el("th", { scope: "col", class: h === "Actions" ? "text-end" : null, text: h })))),
    el("tbody", {}, shown.map(row)))));
}

async function load() {
  container.setAttribute("aria-busy", "true");
  try {
    products = await api("/api/products/manage/");
    setStats();
    render();
  } catch (error) {
    products = null;
    setStats();
    fill(container, loadFailed(error));
  }
}

// ---------- add / edit ----------
function showErrors(lines) {
  formErrors.hidden = !lines.length;
  formErrors.lastElementChild.replaceChildren(...(lines.length === 1 ? [lines[0]] : [el("ul", {}, lines.map((l) => el("li", { text: l })))]));
}

function openForm(product) {
  editing = product;
  form.reset();
  showErrors([]);
  document.getElementById("product-modal-title").textContent = product ? `Edit ${product.product_id}` : "Add Product";
  fields.id.value = product ? product.product_id : "";
  fields.id.disabled = Boolean(product); // product_id is immutable once created
  fields.name.value = product ? product.product_name : "";
  fields.description.value = product ? product.description : "";
  fields.active.checked = product ? product.is_active : true;
  modal("product-modal").show();
}

function clientErrors(body) {
  const problems = [];
  if (!editing) {
    if (!body.product_id) problems.push("Product ID is required.");
    else if (body.product_id.length > 32) problems.push("Product ID must be at most 32 characters.");
    else if (!PRODUCT_ID.test(body.product_id)) problems.push("Product ID may contain only uppercase letters, digits and hyphens (e.g. P-PLATE01).");
  }
  if (!body.product_name) problems.push("Product name is required.");
  return problems;
}

// DRF field errors with readable field names.
function serverErrors(error) {
  if (error.status === 400 && error.data && typeof error.data === "object") {
    return Object.entries(error.data).flatMap(([field, value]) => (Array.isArray(value) ? value : [String(value)])
      .map((m) => (field in LABELS ? `${LABELS[field]}: ${m}` : m)));
  }
  if (error.status === 403) return ["The server refused this change: only administrators can manage products."];
  if (error.status === 404) return ["This product no longer exists. Reload the page."];
  return messages(error.status, error.data);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = { product_name: fields.name.value.trim(), description: fields.description.value.trim(), is_active: fields.active.checked };
  if (!editing) body.product_id = fields.id.value.trim();
  const problems = clientErrors(body);
  showErrors(problems);
  if (problems.length) return;

  submit.disabled = true;
  try {
    const saved = editing
      ? await api(`/api/products/${encodeURIComponent(editing.product_id)}/`, { method: "PATCH", body })
      : await api("/api/products/", { method: "POST", body });
    modal("product-modal").hide();
    toast(editing ? `Product ${saved.product_id} updated.` : `Product ${saved.product_id} created.`);
    await load();
  } catch (error) {
    if (error.status !== 401) showErrors(serverErrors(error)); // 401 already redirects to the login page
  } finally {
    submit.disabled = false;
  }
});

// ---------- activate / deactivate ----------
async function setActive(product, isActive) {
  try {
    await api(`/api/products/${encodeURIComponent(product.product_id)}/`, { method: "PATCH", body: { is_active: isActive } });
    toast(`Product ${product.product_id} ${isActive ? "activated: available for new inspections" : "deactivated"}.`);
    await load();
  } catch (error) {
    if (error.status !== 401 && error.status !== 403) toast(serverErrors(error)[0], "danger"); // 403 is toasted by api()
  }
}

function askDeactivate(product) {
  pendingDeactivate = product;
  document.getElementById("confirm-text").textContent =
    `${product.product_id} · ${product.product_name} will no longer be offered for new inspections.`;
  modal("confirm-modal").show();
}

confirmOk.addEventListener("click", async () => {
  if (!pendingDeactivate) return;
  confirmOk.disabled = true;
  await setActive(pendingDeactivate, false);
  confirmOk.disabled = false;
  pendingDeactivate = null;
  modal("confirm-modal").hide();
});

// ---------- wiring ----------
document.getElementById("add-product").addEventListener("click", () => openForm(null));
fields.id.addEventListener("input", () => { fields.id.value = fields.id.value.toUpperCase(); });
filters.addEventListener("input", render);
filters.addEventListener("submit", (event) => event.preventDefault());
filters.addEventListener("reset", () => setTimeout(render)); // after the browser clears the fields
load();
