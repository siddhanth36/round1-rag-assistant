// Fetch wrapper for the same-origin Django API: session cookie + CSRF, JSON in/out.
// 401 -> /login/ (with ?next), 403 -> "not permitted" toast. The server stays the real guard.
import { toast } from "./ui.js";

export class ApiError extends Error {
  constructor(status, data) {
    super(status === 0 ? "Network error: the server could not be reached." : `HTTP ${status}`);
    this.status = status; // 0 = network failure
    this.data = data;
  }
}

const cookie = (name) => document.cookie.split("; ").find((c) => c.startsWith(`${name}=`))?.split("=")[1];

export async function csrfToken() {
  if (!cookie("csrftoken")) await fetch("/api/auth/csrf/", { credentials: "same-origin" });
  return cookie("csrftoken");
}

export function toLogin() {
  location.href = `/login/?next=${encodeURIComponent(location.pathname + location.search)}`;
}

// Turn a DRF error body into readable lines, verbatim: {"detail": ...} / {"field": ["msg"]} / {"non_field_errors": [...]}.
export function errorLines(data) {
  if (!data || typeof data !== "object") return [];
  return Object.entries(data).flatMap(([field, value]) => {
    const messages = Array.isArray(value) ? value : typeof value === "object" ? errorLines(value) : [String(value)];
    return field === "detail" || field === "non_field_errors" ? messages : messages.map((m) => `${field}: ${m}`);
  });
}

export function handleStatus(status, data) {
  if (status === 401) toLogin();
  else if (status === 403) toast(errorLines(data)[0] || "You are not permitted to do that.", "danger");
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["X-CSRFToken"] = await csrfToken();
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, null);
  }
  const data = response.headers.get("Content-Type")?.includes("json") ? await response.json() : null;
  if (!response.ok) {
    if (!path.startsWith("/api/auth/login/")) handleStatus(response.status, data);
    throw new ApiError(response.status, data);
  }
  return data;
}
