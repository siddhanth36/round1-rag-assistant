// Behaviour shared by every logged-in page: sign out.
import { api } from "./api.js";
import { toast } from "./ui.js";

document.getElementById("logout")?.addEventListener("click", async () => {
  try {
    await api("/api/auth/logout/", { method: "POST" });
  } catch (error) {
    if (error.status !== 401) return toast("Sign-out failed. Try again.", "danger");
  }
  sessionStorage.clear();
  location.href = "/login/";
});
