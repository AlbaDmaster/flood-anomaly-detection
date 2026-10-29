// Replay demo page (administrators): start a historical flood replay, then
// pause, resume or stop it while it runs on the server. Polls the status
// every second while a replay is active.
const PRESETS = {
  "2024": { start: "2024-04-01", end: "2024-05-31", regions: null },
  "2018": { start: "2018-04-20", end: "2018-05-15", regions: ["tana_river_hola"] },
};
const ACTIVE = ["starting", "running", "paused"];
const STATE_LABELS = { idle: "Idle", starting: "Loading history", running: "Running", paused: "Paused",
                       finished: "Finished", stopped: "Stopped", error: "Error" };
let pollTimer = null;
let regionNames = {};
let busy = false;
let speedChanging = false;
let lastState = "idle";

function showError(message) {
  const box = document.getElementById("replay-error");
  box.textContent = message || "";
  box.hidden = !message;
}

function chosenPreset() {
  return document.querySelector("input[name=preset]:checked").value;
}

function requestBody() {
  const preset = chosenPreset();
  const body = { delay: Number(document.getElementById("r-delay").value),
                 reset: document.getElementById("r-reset").checked };
  if (preset === "custom") {
    body.start = document.getElementById("r-start").value;
    body.end = document.getElementById("r-end").value;
    body.regions = [...document.querySelectorAll("#r-regions input:checked")].map((box) => box.value);
    if (!body.start || !body.end) throw new Error("Choose a start and an end date.");
    if (!body.regions.length) throw new Error("Choose at least one region.");
  } else {
    Object.assign(body, { start: PRESETS[preset].start, end: PRESETS[preset].end });
    if (PRESETS[preset].regions) body.regions = PRESETS[preset].regions;
  }
  return body;
}

function render(status) {
  lastState = status.state;
  const active = ACTIVE.includes(status.state);
  const stateBox = document.getElementById("r-state");
  stateBox.textContent = STATE_LABELS[status.state] || status.state;
  stateBox.className = `state state-${status.state}`;

  const percent = status.total_days ? Math.round((100 * status.day) / status.total_days) : 0;
  document.getElementById("r-fill").style.width = `${percent}%`;
  document.getElementById("r-bar").setAttribute("aria-valuenow", String(percent));
  let text = "No replay has run since the server started.";
  if (status.state !== "idle") {
    const where = status.current_date ? `now on ${status.current_date}`
      : `loading the ${status.history_days} days before the start`;
    text = `Day ${status.day} of ${status.total_days} (${status.start} to ${status.end}), ${where}.`;
    if (status.message) text += ` ${status.message}`;
  }
  document.getElementById("r-progress").textContent = text;
  document.getElementById("r-readings").textContent = status.new_readings ?? 0;
  document.getElementById("r-scored").textContent = status.scored ?? 0;
  document.getElementById("r-alerts").textContent = status.alerts ?? 0;
  const failed = status.emails_failed ?? 0;
  document.getElementById("r-emails").textContent = `${status.emails_sent ?? 0}${failed ? ` (${failed} failed)` : ""}`;

  const feed = document.getElementById("r-feed");
  feed.replaceChildren();
  if (!status.recent_alerts || !status.recent_alerts.length) {
    const empty = document.createElement("li");
    empty.className = "muted";
    empty.textContent = "Alerts appear here as the replay raises them.";
    feed.append(empty);
  } else {
    for (const alert of status.recent_alerts) {
      const item = document.createElement("li");
      const when = document.createElement("span");
      when.className = "nowrap";
      when.textContent = alert.date;
      const where = document.createElement("span");
      where.textContent = regionNames[alert.region] || alert.region;
      const score = document.createElement("span");
      score.className = "muted";
      score.textContent = alert.anomaly_score.toFixed(3);
      item.append(severityBadge(alert.severity), when, where, score);
      feed.append(item);
    }
  }

  document.getElementById("start").disabled = active;
  const pause = document.getElementById("pause");
  pause.disabled = !["running", "paused"].includes(status.state);
  pause.textContent = status.state === "paused" ? "Resume" : "Pause";
  document.getElementById("stop").disabled = !active;
  for (const input of document.querySelectorAll("#replay-form fieldset input, #r-reset, #custom input")) {
    input.disabled = active;
  }
  if (active && !speedChanging) document.getElementById("r-delay").value = String(status.delay);
  if (status.state === "error") showError(status.message);
  schedule(active ? 1000 : 5000);
}

async function refresh() {
  try {
    render((await api("/api/replay")).replay);
  } catch (error) {
    showError(error.message);
    schedule(5000);
  }
}

function schedule(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(refresh, ms);
}

async function control(action, body) {
  if (busy) return;
  busy = true;
  showError("");
  try {
    const options = { method: "POST" };
    if (body) options.body = JSON.stringify(body);
    render((await api(`/api/replay/${action}`, options)).replay);
  } catch (error) {
    showError(error.message);
  } finally {
    busy = false;
  }
}

document.getElementById("replay-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  let body;
  try {
    body = requestBody();
  } catch (error) {
    showError(error.message);
    return;
  }
  if (body.reset && !window.confirm("This deletes every reading, score, alert and piece of feedback before the replay starts. Continue?")) {
    return;
  }
  const button = document.getElementById("start");
  button.disabled = true;
  try {
    render((await api("/api/replay/start", { method: "POST", body: JSON.stringify(body) })).replay);
  } catch (error) {
    showError(error.message);
    button.disabled = false;
  }
});

document.getElementById("pause").addEventListener("click", (event) =>
  control(event.currentTarget.textContent === "Resume" ? "resume" : "pause"));
document.getElementById("stop").addEventListener("click", () => control("stop"));
document.getElementById("r-delay").addEventListener("change", async (event) => {
  if (!ACTIVE.includes(lastState)) return;
  speedChanging = true;
  await control("speed", { delay: Number(event.currentTarget.value) });
  speedChanging = false;
});
for (const radio of document.querySelectorAll("input[name=preset]")) {
  radio.addEventListener("change", () => { document.getElementById("custom").hidden = chosenPreset() !== "custom"; });
}

(async () => {
  try {
    const { regions } = await api("/api/regions");
    const box = document.getElementById("r-regions");
    for (const region of regions) {
      regionNames[region.slug] = region.name;
      const label = document.createElement("label");
      label.className = "check";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = region.slug;
      input.checked = true;
      label.append(input, ` ${region.name}`);
      box.append(label);
    }
  } catch (error) {
    showError(error.message);
  }
  refresh();
})();
