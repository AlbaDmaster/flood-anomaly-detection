// Colours and Chart.js helpers shared by the overview and regions pages.
const SEVERITY_COLOURS = { high: "#c0392b", moderate: "#d68910", normal: "#2e7d32", none: "#8a96a3" };
const REGION_COLOURS = ["#3a6ea5", "#8e44ad", "#d35400", "#16a085"];

function severityColour(severity) {
  return SEVERITY_COLOURS[severity] || SEVERITY_COLOURS.none;
}

function baseOptions(extra = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } } },
    scales: { x: { ticks: { maxTicksLimit: 8, maxRotation: 0 } } },
    ...extra,
  };
}

// Draw a chart once, then update it in place on later refreshes.
function upsertChart(existing, canvas, config) {
  if (existing) {
    existing.data = config.data;
    existing.options = config.options;
    existing.update("none");
    return existing;
  }
  return new Chart(canvas, config);
}
