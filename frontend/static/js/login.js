import { api, errorLines } from "./api.js";

const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const submit = document.getElementById("login-submit");
const password = document.getElementById("password");

function showError(message) {
  errorBox.querySelector("span").textContent = message;
  errorBox.hidden = false;
}

document.getElementById("toggle-password").addEventListener("click", (event) => {
  const show = password.type === "password";
  password.type = show ? "text" : "password";
  event.currentTarget.textContent = show ? "Hide" : "Show";
  event.currentTarget.setAttribute("aria-pressed", String(show));
});

// Only same-site relative paths are followed after login (no open redirect).
function nextUrl() {
  const next = new URLSearchParams(location.search).get("next") || "/";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  const username = form.username.value.trim();
  if (!username || !password.value) return showError("Enter your username and password.");
  submit.disabled = true;
  submit.textContent = "Signing in…";
  try {
    await api("/api/auth/login/", { method: "POST", body: { username, password: password.value } });
    location.href = nextUrl();
  } catch (error) {
    showError(error.status === 0 ? error.message : errorLines(error.data)[0] || "Sign-in failed. Try again.");
    password.select();
    submit.disabled = false;
    submit.textContent = "Sign in";
  }
});
