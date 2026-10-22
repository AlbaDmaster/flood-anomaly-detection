// Overview page: summary cards and the five most recent alerts.
// Refreshes every 15 seconds so a running replay shows up live.
const REFRESH_MS = 15000;

async function loadOverview() {
  try {
    const [{ regions }, recent] = await Promise.all([
      api("/api/regions"),
      api("/api/alerts?per_page=5"),
    ]);
    const scored = regions.map((r) => r.latest_scored).filter(Boolean);
    const highRisk = scored.filter((r) => r.severity === "high").length;
    const avg = scored.length ? scored.reduce((sum, r) => sum + r.anomaly_score, 0) / scored.length : null;

    document.getElementById("kpi-regions").textContent = regions.length;
    document.getElementById("kpi-alerts").textContent = regions.reduce((n, r) => n + r.open_alerts, 0);
    document.getElementById("kpi-risk").textContent = highRisk;
    document.getElementById("kpi-score").textContent = avg === null ? "–" : avg.toFixed(2);

    const body = document.getElementById("recent-alerts");
    body.replaceChildren();
    if (!recent.alerts.length) {
      const row = document.createElement("tr");
      row.append(cell("No alerts yet.", "muted"));
      row.firstChild.colSpan = 5;
      body.append(row);
    }
    for (const alert of recent.alerts) {
      const row = document.createElement("tr");
      const severity = document.createElement("td");
      severity.append(severityBadge(alert.severity));
      row.append(cell(alert.region_name), cell(alert.date, "nowrap"), severity,
                 cell(alert.anomaly_score.toFixed(3)), cell(alert.status, `status status-${alert.status}`));
      body.append(row);
    }
    document.getElementById("updated").textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (error) {
    document.getElementById("updated").textContent = "Could not load data: " + error.message;
  }
}

loadOverview();
setInterval(() => { if (!document.hidden) loadOverview(); }, REFRESH_MS);
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadOverview(); });
