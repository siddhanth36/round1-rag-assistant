import { api } from "./api.js";
import { fill, loadFailed, machinePicker, selectedMachine } from "./ui.js";

const container = document.getElementById("machines");
const current = selectedMachine();
if (current && document.body.dataset.role === "ENGINEER") {
  document.getElementById("selected-readout").textContent = `Selected: ${current.machine_id}`;
}

try {
  const [machines, manuals] = await Promise.all([api("/api/machines/"), api("/api/manuals/")]);
  fill(container, machinePicker(machines, manuals, { canAsk: document.body.dataset.role === "ENGINEER" }));
} catch (error) {
  fill(container, loadFailed(error));
}
