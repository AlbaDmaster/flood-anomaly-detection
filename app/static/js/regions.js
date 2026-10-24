// Regions page: one card per region with its latest scored reading, and for
// the selected region two charts over a date range: the anomaly score with
// the model's moderate/high thresholds, and rainfall with river discharge.
let regions = [];
let selected = null;
let scoreChart = null;
let weatherChart = null;
let detailSeq = 0;

function fmt(value, digits, unit) {
  return value === null || value === undefined ? "–" : `${value.toFixed(digits)}${unit}`;
}

function renderCards() {
  const box = document.getElementById("region-cards");
  box.replaceChildren();
  for (const region of regions) {
    const latest = region.latest_scored;
    const card = document.createElement("button");
    card.type = "button";
    card.className = `region-card severity-${latest ? latest.severity : "none"}`;
    card.setAttribute("aria-pressed", String(region.slug === selected));
    card.addEventListener("click", () => selectRegion(region.slug));

    const head = document.createElement("div");
    head.className = "region-head";
    const name = document.createElement("strong");
    name.textContent = region.name;
    head.append(name, severityBadge(latest ? latest.severity : null));

    const county = document.createElement("p");
    county.className = "muted small";
    county.textContent = `${region.county} County`;

    const stats = document.createElement("dl");
    const rows = [
      ["Latest scored day", latest ? latest.date : "–"],
      ["Anomaly score", latest ? latest.anomaly_score.toFixed(3) : "–"],
      ["Rainfall", latest ? fmt(latest.rainfall, 1, " mm") : "–"],
      ["River discharge", latest ? fmt(latest.river_discharge, 1, " m³/s") : "–"],
      ["Open alerts", String(region.open_alerts)],
    ];
    for (const [label, value] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      stats.append(dt, dd);
    }
    card.append(head, county, stats);
    box.append(card);
  }
}

function showRangeError(message) {
  const box = document.getElementById("range-error");
  box.textContent = message || "";
  box.hidden = !message;
}

async function loadDetail(start, end) {
  const seq = ++detailSeq;
  const region = regions.find((r) => r.slug === selected);
  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  let data;
  try {
    data = await api(`/api/regions/${encodeURIComponent(selected)}/readings?${params}`);
  } catch (error) {
    if (seq === detailSeq) showRangeError(error.message);
    return;
  }
  if (seq !== detailSeq) return;  // a newer request has been made since
  showRangeError("");
  document.getElementById("detail-title").textContent = `${region.name}: anomaly score`;
  document.getElementById("start").value = data.start;
  document.getElementById("end").value = data.end;

  const readings = data.readings;
  const labels = readings.map((r) => r.date);
  const scored = readings.filter((r) => r.severity);
  const flagged = scored.filter((r) => r.severity !== "normal").length;
  document.getElementById("detail-note").textContent = readings.length
    ? `${readings.length} days shown, ${scored.length} scored, ${flagged} moderate or high.`
    : "No readings in this date range.";

  const thresholds = data.thresholds || {};
  const flat = (value) => labels.map(() => value);
  const thresholdLines = [];
  if (thresholds.moderate != null) {
    thresholdLines.push({ label: "Moderate threshold", data: flat(thresholds.moderate), borderColor: SEVERITY_COLOURS.moderate,
      borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0 });
  }
  if (thresholds.high != null) {
    thresholdLines.push({ label: "High threshold", data: flat(thresholds.high), borderColor: SEVERITY_COLOURS.high,
      borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0 });
  }
  scoreChart = upsertChart(scoreChart, document.getElementById("score-chart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Anomaly score",
          data: readings.map((r) => r.anomaly_score),
          borderColor: "#3a6ea5",
          borderWidth: 2,
          tension: 0.2,
          pointRadius: readings.map((r) => (r.severity === "high" ? 5 : r.severity === "moderate" ? 4 : 2)),
          pointBackgroundColor: readings.map((r) => severityColour(r.severity)),
          pointBorderColor: readings.map((r) => severityColour(r.severity)),
        },
        ...thresholdLines,
      ],
    },
    options: baseOptions({
      scales: {
        x: { ticks: { maxTicksLimit: 8, maxRotation: 0 } },
        y: { title: { display: true, text: "Anomaly score" } },
      },
    }),
  });

  weatherChart = upsertChart(weatherChart, document.getElementById("weather-chart"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        { type: "bar", label: "Rainfall (mm)", data: readings.map((r) => r.rainfall),
          backgroundColor: "rgba(58, 110, 165, 0.55)", yAxisID: "y" },
        { type: "line", label: "River discharge (m³/s)", data: readings.map((r) => r.river_discharge),
          borderColor: "#d35400", backgroundColor: "#d35400", borderWidth: 2, pointRadius: 0,
          tension: 0.2, yAxisID: "y1" },
      ],
    },
    options: baseOptions({
      scales: {
        x: { ticks: { maxTicksLimit: 8, maxRotation: 0 } },
        y: { position: "left", beginAtZero: true, title: { display: true, text: "Rainfall (mm)" } },
        y1: { position: "right", beginAtZero: true, grid: { drawOnChartArea: false },
              title: { display: true, text: "Discharge (m³/s)" } },
      },
    }),
  });
}

function selectRegion(slug) {
  selected = slug;
  const url = new URL(window.location);
  url.searchParams.set("region", slug);
  window.history.replaceState(null, "", url);
  document.querySelectorAll("#region-cards .region-card").forEach((button, i) =>
    button.setAttribute("aria-pressed", String(regions[i].slug === slug)));
  loadDetail();
}

document.getElementById("range-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadDetail(document.getElementById("start").value, document.getElementById("end").value);
});

(async () => {
  try {
    regions = (await api("/api/regions")).regions;
  } catch (error) {
    document.getElementById("region-cards").textContent = "Could not load regions: " + error.message;
    return;
  }
  if (!regions.length) {
    document.getElementById("region-cards").textContent = "No regions configured.";
    return;
  }
  const wanted = new URLSearchParams(window.location.search).get("region");
  renderCards();
  selectRegion(regions.some((r) => r.slug === wanted) ? wanted : regions[0].slug);
})();
