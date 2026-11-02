// Example stub for the Round 2 page pattern (frontend teammate owns this file).
// Shows the API call pattern: api() handles session + CSRF; 401 -> login, 403 -> toast.
import { api } from "./api.js";
import { el, fill, loadFailed } from "./ui.js";

const container = document.getElementById("products");
try {
  const products = await api("/api/products/");
  fill(container, el("ul", { class: "panel-body" },
    ...products.map((p) => el("li", { text: `${p.product_id} · ${p.product_name}` }))));
} catch (error) {
  fill(container, loadFailed(error));
}
