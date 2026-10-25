// Overview page: summary cards, anomaly score trend, risk map and recent alerts.
// Refreshes every 15 seconds while the tab is visible, and every 3 seconds
// while a replay is running so the demo updates live.
const REFRESH_MS = 15000;
const FAST_REFRESH_MS = 3000;
let trendChart = null;
let riskMap = null;
const markers = {};
let busy = false;

function renderKpis(regions) {
  const scored = regions.map((r) => r.latest_scored).filter(Boolean);
  const avg = scored.length ? scored.reduce((sum, r) => sum + r.anomaly_score, 0) / scored.length : null;
  document.getElementById("kpi-regions").textContent = regions.length;
  document.getElementById("kpi-alerts").textContent = regions.reduce((n, r) => n + r.open_alerts, 0);
  document.getElementById("kpi-risk").textContent = scored.filter((r) => r.severity === "high").length;
  document.getElementById("kpi-score").textContent = avg === null ? "–" : avg.toFixed(2);
}

async function renderTrend(regions) {
  const series = await Promise.all(
    regions.map((r) => api(`/api/regions/${encodeURIComponent(r.slug)}/readings`)),
  );
  const dates = [...new Set(series.flatMap((s) => s.readings.map((x) => x.date)))].sort();
  const datasets = series.map((s, i) => {
    const byDate = new Map(s.readings.map((x) => [x.date, x.anomaly_score]));
    return {
      label: regions[i].name,
      data: dates.map((d) => (byDate.has(d) ? byDate.get(d) : null)),
      borderColor: REGION_COLOURS[i % REGION_COLOURS.length],
      backgroundColor: REGION_COLOURS[i % REGION_COLOURS.length],
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.2,
    };
  });
  trendChart = upsertChart(trendChart, document.getElementById("trend-chart"), {
    type: "line",
    data: { labels: dates, datasets },
    options: baseOptions({
      scales: {
        x: { ticks: { maxTicksLimit: 6, maxRotation: 0 } },
        y: { title: { display: true, text: "Anomaly score" } },
      },
    }),
  });
}

function popupFor(region) {
  const box = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = region.name;
  const latest = region.latest_scored;
  const line = document.createElement("div");
  line.textContent = latest
    ? `${latest.date}: ${latest.severity}, score ${latest.anomaly_score.toFixed(3)}`
    : "No scored readings yet";
  const alerts = document.createElement("div");
  alerts.textContent = `Open alerts: ${region.open_alerts}`;
  box.append(title, line, alerts);
  return box;
}

function renderMap(regions) {
  if (typeof L === "undefined" || !regions.length) return;
  if (!riskMap) {
    riskMap = L.map("risk-map", { scrollWheelZoom: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 12,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(riskMap);
    riskMap.fitBounds(regions.map((r) => [r.latitude, r.longitude]), { padding: [40, 40] });
  }
  for (const region of regions) {
    const severity = region.latest_scored ? region.latest_scored.severity : "none";
    const style = {
      radius: severity === "high" ? 16 : severity === "moderate" ? 12 : 9,
      color: "#ffffff",
      weight: 2,
      fillColor: severityColour(severity),
      fillOpacity: 0.9,
    };
    if (markers[region.slug]) {
      markers[region.slug].setStyle(style).setRadius(style.radius).setPopupContent(popupFor(region));
    } else {
      const tip = document.createElement("span");
      tip.textContent = region.name;
      markers[region.slug] = L.circleMarker([region.latitude, region.longitude], style)
        .bindTooltip(tip, { permanent: true, direction: "bottom", offset: [0, 10] })
        .bindPopup(popupFor(region))
        .addTo(riskMap);
    }
  }
}

function renderRecentAlerts(alerts) {
  const body = document.getElementById("recent-alerts");
  body.replaceChildren();
  if (!alerts.length) {
    const row = document.createElement("tr");
    row.append(cell("No alerts yet.", "muted"));
    row.firstChild.colSpan = 5;
    body.append(row);
  }
  for (const alert of alerts) {
    const row = document.createElement("tr");
    const severity = document.createElement("td");
    severity.append(severityBadge(alert.severity));
    row.append(cell(alert.region_name), cell(alert.date, "nowrap"), severity,
               cell(alert.anomaly_score.toFixed(3)), cell(alert.status, `status status-${alert.status}`));
    body.append(row);
  }
}

let replayActive = false;
let refreshTimer = null;

function renderReplayBanner(replay) {
  replayActive = ["starting", "running", "paused"].includes(replay.state);
  const banner = document.getElementById("replay-banner");
  banner.hidden = !replayActive;
  if (replayActive) {
    const where = replay.current_date ? `now showing ${replay.current_date}` : "loading history";
    const paused = replay.state === "paused" ? " (paused)" : "";
    document.getElementById("replay-banner-text").textContent =
      `Replaying ${replay.start} to ${replay.end}${paused}: day ${replay.day} of ${replay.total_days}, ${where}. ` +
      `${replay.alerts} alerts raised so far.`;
  }
}

async function loadOverview() {
  if (busy) return;
  busy = true;
  try {
    const [{ regions }, recent, { replay }] = await Promise.all([
      api("/api/regions"), api("/api/alerts?per_page=5"), api("/api/replay"),
    ]);
    renderReplayBanner(replay);
    renderKpis(regions);
    renderMap(regions);
    renderRecentAlerts(recent.alerts);
    await renderTrend(regions);
    document.getElementById("updated").textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (error) {
    document.getElementById("updated").textContent = "Could not load data: " + error.message;
  } finally {
    busy = false;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(tick, replayActive ? FAST_REFRESH_MS : REFRESH_MS);
  }
}

function tick() {
  if (document.hidden) {
    refreshTimer = setTimeout(tick, REFRESH_MS);
  } else {
    loadOverview();
  }
}

loadOverview();
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadOverview(); });
