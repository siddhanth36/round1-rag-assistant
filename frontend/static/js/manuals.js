import { api } from "./api.js";
import { fill, loadFailed, machineOptions, manualsTable, restoreFilters, syncFilters } from "./ui.js";

const form = document.getElementById("filters");
const container = document.getElementById("manuals");
let machines = [];

async function load() {
  const params = syncFilters(form);
  container.setAttribute("aria-busy", "true");
  try {
    const manuals = await api(`/api/manuals/?${params}`);
    const machine = params.get("machine");
    fill(container, manualsTable(manuals, machines, { caption: machine ? `Manuals for ${machine}` : "All manuals" }));
  } catch (error) {
    fill(container, loadFailed(error));
  }
}

form.addEventListener("change", load);
try {
  machines = await machineOptions(document.getElementById("machine-filter"));
  restoreFilters(form);
  load();
} catch (error) {
  fill(container, loadFailed(error));
}
