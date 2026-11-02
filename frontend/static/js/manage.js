// Admin corpus management. Client checks mirror the server's; the server stays the authority.
import { api, csrfToken, errorLines, handleStatus } from "./api.js";
import { el, icon, indexingChip, machineTag, toast } from "./ui.js";

const $ = (id) => document.getElementById(id);
const uploadForm = $("upload-form"), machineForm = $("machine-form");
const maxMb = Number(uploadForm.dataset.maxMb);

function showErrors(box, lines) {
  box.hidden = !lines.length;
  box.querySelector("div").replaceChildren(lines.length === 1 ? lines[0] : el("ul", {}, lines.map((line) => el("li", { text: line }))));
}

async function loadMachines() {
  try {
    const machines = await api("/api/machines/");
    const select = $("u-machine");
    select.replaceChildren(el("option", { value: "", text: machines.length ? "Select a machine" : "No machines registered" }),
      ...machines.map((m) => el("option", { value: m.machine_id, text: `${m.machine_id} · ${m.model} · ${m.machine_name}` })));
    $("registered").replaceChildren(...machines.map((m) => machineTag(m.machine_id)));
  } catch {
    $("u-machine").replaceChildren(el("option", { value: "", text: "Machines could not be loaded" }));
  }
}

// Same rules as the server: .pdf name, size limit, %PDF- magic bytes. PyMuPDF parsing happens server-side.
async function clientChecks(form) {
  const errors = [];
  if (!form.machine_id.value) errors.push("Select a machine.");
  if (!form.document_id.value.trim()) errors.push("Enter a document ID.");
  if (!form.revision.value.trim()) errors.push("Enter a revision.");
  const file = form.file.files[0];
  if (!file) errors.push("Choose a PDF file.");
  else {
    if (!file.name.toLowerCase().endsWith(".pdf")) errors.push("Only .pdf files are accepted.");
    if (file.size > maxMb * 1024 * 1024) errors.push(`The file is larger than the ${maxMb} MB limit.`);
    else if ((await file.slice(0, 5).text()) !== "%PDF-") errors.push("The file is not a PDF.");
  }
  return errors;
}

function setProgress(percent, text) {
  $("upload-progress").hidden = percent === null;
  if (percent === null) return;
  const bar = $("upload-progress").querySelector(".progress");
  bar.setAttribute("aria-valuenow", String(percent));
  bar.querySelector(".progress-bar").style.width = `${percent}%`;
  $("upload-progress-text").textContent = text;
}

function showResult(manual, file) {
  $("upload-result").replaceChildren(el("ul", { class: "steps" },
    el("li", {}, icon("check-circle"), el("span", {}, "Uploaded ", el("span", { class: "mono", text: `${manual.document_id} r${manual.revision}` }),
      ` (${(file.size / 1024).toFixed(0)} KB) for `, machineTag(manual.machine_id))),
    el("li", {}, icon("check-circle"), el("span", { text: `Validated: readable PDF, ${manual.page_count} page${manual.page_count === 1 ? "" : "s"}` })),
    el("li", {}, indexingChip(manual.indexing_status),
      el("span", { class: "muted", text: manual.indexing_status === "pending" ? "Indexing runs when the RAG engine is connected." : "" }))));
}

async function upload(form) {
  const data = new FormData(form);
  const token = await csrfToken();
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/manuals/upload/");
    xhr.setRequestHeader("X-CSRFToken", token);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (e) => e.lengthComputable && setProgress(Math.round((e.loaded / e.total) * 100), "Uploading…");
    xhr.upload.onload = () => setProgress(100, "Validating on the server…");
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON error page */ }
      resolve({ status: xhr.status, body });
    };
    xhr.onerror = () => resolve({ status: 0, body: null });
    xhr.send(data);
  });
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const box = $("upload-errors");
  const errors = await clientChecks(uploadForm);
  showErrors(box, errors);
  if (errors.length) return;
  const submit = $("upload-submit");
  submit.disabled = true;
  const file = uploadForm.file.files[0];
  setProgress(0, "Uploading…");
  const { status, body } = await upload(uploadForm);
  setProgress(null);
  submit.disabled = false;
  if (status === 201) {
    toast(`${body.document_id} revision ${body.revision} uploaded.`);
    showResult(body, file);
    uploadForm.reset();
    return;
  }
  handleStatus(status, body); // 401 -> login, 403 -> toast
  showErrors(box, status === 0 ? ["Network error: the server could not be reached."]
    : errorLines(body).length ? errorLines(body) : [`The server answered with HTTP ${status}.`]);
});

machineForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const box = $("machine-errors");
  const payload = Object.fromEntries(["machine_id", "model", "machine_name"].map((k) => [k, machineForm[k].value.trim()]));
  const missing = Object.entries(payload).filter(([, v]) => !v).map(([k]) => `${k.replace("_", " ")} is required.`);
  showErrors(box, missing);
  if (missing.length) return;
  try {
    const machine = await api("/api/machines/", { method: "POST", body: payload });
    toast(`${machine.machine_id} registered.`);
    machineForm.reset();
    loadMachines();
  } catch (error) {
    showErrors(box, error.status === 0 ? [error.message] : errorLines(error.data).length ? errorLines(error.data) : [`The server answered with HTTP ${error.status}.`]);
  }
});

loadMachines();
