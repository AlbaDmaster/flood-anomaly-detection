// Alerts page: filtered, paginated list of alerts. Clicking "Details" opens a
// dialog with the alert's facts and feedback; staff can acknowledge the alert
// and record whether it was a real flood risk or a false alarm.
const PER_PAGE = 20;
const page = document.getElementById("alerts-page");
const canAct = page.dataset.canAct === "true";
const dialog = document.getElementById("alert-dialog");
let state = { region: "", severity: "", status: "", page: 1 };
let total = 0;
let openAlertId = null;
let listSeq = 0;

function readStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  for (const key of ["region", "severity", "status"]) {
    const select = document.getElementById(`f-${key}`);
    select.value = params.get(key) || "";
    if (select.selectedIndex < 0) select.value = "";  // ignore values that are not in the list
    state[key] = select.value;
  }
  state.page = Math.max(parseInt(params.get("page") || "1", 10) || 1, 1);
}

function writeStateToUrl() {
  const params = new URLSearchParams();
  for (const key of ["region", "severity", "status"]) if (state[key]) params.set(key, state[key]);
  if (state.page > 1) params.set("page", state.page);
  const query = params.toString();
  window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
}

function showError(id, message) {
  const box = document.getElementById(id);
  box.textContent = message || "";
  box.hidden = !message;
}

async function loadRegions() {
  const { regions } = await api("/api/regions");
  const select = document.getElementById("f-region");
  for (const region of regions) {
    const option = document.createElement("option");
    option.value = region.slug;
    option.textContent = region.name;
    select.append(option);
  }
}

async function loadList() {
  const seq = ++listSeq;
  const params = new URLSearchParams({ page: state.page, per_page: PER_PAGE });
  for (const key of ["region", "severity", "status"]) if (state[key]) params.set(key, state[key]);
  let data;
  try {
    data = await api(`/api/alerts?${params}`);
  } catch (error) {
    if (seq === listSeq) {
      showError("list-error", error.message);
      document.getElementById("alert-rows").replaceChildren();
    }
    return;
  }
  if (seq !== listSeq) return;
  showError("list-error", "");
  total = data.total;
  const pages = Math.max(Math.ceil(total / PER_PAGE), 1);
  if (state.page > pages && total > 0) {
    state.page = pages;
    writeStateToUrl();
    return loadList();
  }

  const body = document.getElementById("alert-rows");
  body.replaceChildren();
  if (!data.alerts.length) {
    const row = document.createElement("tr");
    row.append(cell("No alerts match these filters.", "muted"));
    row.firstChild.colSpan = 7;
    body.append(row);
  }
  for (const alert of data.alerts) {
    const row = document.createElement("tr");
    const severity = document.createElement("td");
    severity.append(severityBadge(alert.severity));
    const action = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-button";
    button.textContent = "Details";
    button.dataset.alertId = alert.alert_id;
    button.setAttribute("aria-label", `Details for ${alert.region_name} on ${alert.date}`);
    button.addEventListener("click", () => openAlert(alert.alert_id));
    action.append(button);
    row.append(cell(alert.date, "nowrap"), cell(alert.region_name), severity,
               cell(alert.anomaly_score.toFixed(3)), cell(alert.status, `status status-${alert.status}`),
               cell(String(alert.feedback_count)), action);
    body.append(row);
  }
  document.getElementById("page-info").textContent =
    total ? `Page ${state.page} of ${pages} · ${total} alerts` : "0 alerts";
  document.getElementById("prev").disabled = state.page <= 1;
  document.getElementById("next").disabled = state.page >= pages;
}

function renderDialog(alert) {
  document.getElementById("dialog-title").textContent = `${alert.region_name}, ${alert.date}`;
  document.getElementById("dialog-message").textContent = alert.message;
  const facts = document.getElementById("dialog-facts");
  facts.replaceChildren();
  const rows = [["Severity", null], ["Anomaly score", alert.anomaly_score.toFixed(3)],
                ["Status", alert.status], ["Channel", alert.channel]];
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    if (value === null) dd.append(severityBadge(alert.severity)); else dd.textContent = value;
    facts.append(dt, dd);
  }
  if (canAct) {
    const ack = document.getElementById("acknowledge");
    ack.disabled = alert.status === "acknowledged";
    ack.textContent = alert.status === "acknowledged" ? "Acknowledged" : "Acknowledge";
  }
  const list = document.getElementById("feedback-list");
  list.replaceChildren();
  if (!alert.feedback.length) {
    const item = document.createElement("li");
    item.className = "muted";
    item.textContent = "No feedback yet.";
    list.append(item);
  }
  for (const fb of alert.feedback) {
    const item = document.createElement("li");
    const verdict = document.createElement("strong");
    verdict.textContent = fb.is_confirmed ? "Confirmed flood risk" : "False alarm";
    const meta = document.createElement("span");
    meta.className = "muted small";
    meta.textContent = ` by ${fb.user}${fb.created_at ? " · " + fb.created_at.replace("T", " ").slice(0, 16) + " (server time)" : ""}`;
    item.append(verdict, meta);
    if (fb.comment) {
      const comment = document.createElement("p");
      comment.textContent = fb.comment;
      item.append(comment);
    }
    list.append(item);
  }
}

async function openAlert(alertId) {
  openAlertId = alertId;
  showError("dialog-error", "");
  if (canAct) document.getElementById("feedback-form").reset();
  try {
    const { alert } = await api(`/api/alerts/${alertId}`);
    if (openAlertId !== alertId) return;  // another alert was opened meanwhile
    renderDialog(alert);
    if (!dialog.open) dialog.showModal();
  } catch (error) {
    showError(dialog.open ? "dialog-error" : "list-error", error.message);
  }
}

document.getElementById("filters").addEventListener("submit", (event) => {
  event.preventDefault();
  state.region = document.getElementById("f-region").value;
  state.severity = document.getElementById("f-severity").value;
  state.status = document.getElementById("f-status").value;
  state.page = 1;
  writeStateToUrl();
  loadList();
});
document.getElementById("prev").addEventListener("click", () => { state.page -= 1; writeStateToUrl(); loadList(); });
document.getElementById("next").addEventListener("click", () => { state.page += 1; writeStateToUrl(); loadList(); });
document.getElementById("dialog-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("close", () => {
  const id = openAlertId;
  openAlertId = null;
  document.querySelector(`#alert-rows button[data-alert-id="${id}"]`)?.focus();
});

if (canAct) {
  document.getElementById("acknowledge").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const id = openAlertId;
    button.disabled = true;
    try {
      await api(`/api/alerts/${id}/acknowledge`, { method: "POST" });
      if (openAlertId === id) await openAlert(id);
      loadList();
    } catch (error) {
      showError("dialog-error", error.message);
      button.disabled = false;
    }
  });

  document.getElementById("feedback-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const verdict = form.querySelector("input[name=verdict]:checked");
    if (!verdict) {
      showError("dialog-error", "Choose whether this was a confirmed flood risk or a false alarm.");
      return;
    }
    const button = form.querySelector("button[type=submit]");
    const id = openAlertId;
    button.disabled = true;
    try {
      const { alert } = await api(`/api/alerts/${id}/feedback`, {
        method: "POST",
        body: JSON.stringify({ is_confirmed: verdict.value === "true", comment: form.comment.value }),
      });
      if (openAlertId === id) {
        form.reset();
        showError("dialog-error", "");
        renderDialog(alert);
      }
      loadList();
    } catch (error) {
      showError("dialog-error", error.message);
    } finally {
      button.disabled = false;
    }
  });
}

(async () => {
  try {
    await loadRegions();
  } catch (error) {
    showError("list-error", error.message);
  }
  readStateFromUrl();
  loadList();
})();
