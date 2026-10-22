// Small helpers shared by every page: JSON calls to the Flask API and sign-out.
async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname + window.location.search);
    throw new Error("login required");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function severityBadge(severity) {
  const span = document.createElement("span");
  span.className = `badge badge-${severity || "none"}`;
  span.textContent = severity || "not scored";
  return span;
}

const logoutButton = document.getElementById("logout");
if (logoutButton) {
  logoutButton.addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (error) { /* already signed out */ }
    window.location.href = "/login";
  });
}
