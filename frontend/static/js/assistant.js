// Assistant: one machine in scope, independent questions, honest states. Never renders a canned answer.
import { api, errorLines } from "./api.js";
import { answerPanel, el, empty, fmtDateTime, icon, loadingPanel, machineTag, networkErrorPanel, notConnectedPanel, selectedMachine, statusLabel } from "./ui.js";

const MAX = 1000;
const PRACTICE = [ // the challenge brief's practice questions
  "What does E17 mean?",
  "What is the normal operating pressure range?",
  "How often is the filter checked?",
  "What should be recorded before escalation?",
];
const $ = (id) => document.getElementById(id);
const thread = $("thread"), form = $("composer"), input = $("question"), askButton = $("ask"), errorBox = $("question-error");
const machine = selectedMachine();
let scope = "";

function showFieldError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

function renderEmptyThread() {
  thread.replaceChildren(empty({
    iconName: "chat-square-text", title: `Ask about ${machine.machine_id}`,
    body: "Answers come only from this machine's manuals, with the document, revision and page for every source.",
    action: el("div", { class: "suggestions mt-2" }, PRACTICE.map((text) => el("button", { type: "button", class: "btn btn-outline-secondary btn-sm",
      onclick: () => { input.value = text; updateCount(); input.focus(); } }, text))),
  }));
}

function updateCount() {
  $("question-count").textContent = `${input.value.length} / ${MAX}`;
}

async function loadContext() {
  const ctx = $("context");
  const parts = [machineTag(machine.machine_id), el("strong", { text: `${machine.model} ${machine.machine_name}` })];
  try {
    const manuals = await api(`/api/manuals/?machine=${encodeURIComponent(machine.machine_id)}`);
    scope = manuals.map((m) => `${m.document_id} r${m.revision}`).join(" · ");
    parts.push(el("span", { class: "scope" }, "Scope: ", el("span", { class: "readout", text: scope || "no manuals registered" })));
  } catch {
    parts.push(el("span", { class: "scope", text: "Scope unavailable" }));
  }
  parts.push(el("a", { class: "btn btn-outline-secondary btn-sm change", href: "/machines/" }, icon("arrow-left-right"), " Change machine"));
  ctx.replaceChildren(...parts);
}

function settle(slot, panel, announcement) {
  slot.replaceChildren(panel);
  $("announce").textContent = announcement;
  panel.focus({ preventScroll: true }); // visible outline marks where the new answer is
  panel.scrollIntoView({ block: "nearest" });
}

async function ask(text, slot) {
  slot.replaceChildren(loadingPanel(machine.machine_id));
  $("announce").textContent = `Searching ${machine.machine_id} manuals`;
  const retry = () => ask(text, slot);
  try {
    const q = await api("/api/assistant/ask/", { method: "POST", body: { machine_id: machine.machine_id, question: text } });
    const count = q.citations.length;
    settle(slot, answerPanel(q, { onRetry: retry, scope }),
      q.status === "answered" ? `Answered with ${count} source${count === 1 ? "" : "s"}` : `${statusLabel(q.status)} for ${machine.machine_id}`);
  } catch (error) {
    if (error.status === 404 || error.status === 501) settle(slot, notConnectedPanel(), "Assistant engine not connected yet");
    else if (error.status === 0) settle(slot, networkErrorPanel(retry), "Network error. The question was not sent.");
    else if (error.status === 400) {
      slot.closest(".turn").remove();
      showFieldError(errorLines(error.data).join(" ") || "The question was rejected.");
      input.value = text;
      updateCount();
      input.focus();
    } else settle(slot, answerPanel({ id: 0, status: "error", citations: [] }, { onRetry: error.status >= 500 ? retry : null }),
      error.status === 403 ? "Not permitted for your role" : "Couldn't answer");
  } finally {
    askButton.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return showFieldError("Type a question first.");
  if (text.length > MAX) return showFieldError(`Questions are limited to ${MAX} characters.`);
  showFieldError("");
  if (thread.querySelector(".empty")) thread.replaceChildren();
  const slot = el("div");
  thread.append(el("article", { class: "turn" },
    el("div", { class: "turn-q" }, el("span", { class: "readout", text: fmtDateTime(new Date().toISOString()) }), el("p", { class: "q-text m-0", text })),
    slot));
  input.value = "";
  updateCount();
  askButton.disabled = true;
  ask(text, slot);
});

input.addEventListener("input", updateCount);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

if (!machine) {
  $("context").replaceChildren(el("span", { class: "muted", text: "No machine selected" }));
  thread.replaceChildren(empty({ iconName: "gear-wide-connected", title: "Select a machine first",
    body: "The assistant answers from one machine's manuals at a time.",
    action: el("a", { class: "btn btn-primary btn-sm mt-2", href: "/machines/" }, "Choose a machine") }));
} else {
  form.hidden = false;
  loadContext();
  renderEmptyThread();
}
