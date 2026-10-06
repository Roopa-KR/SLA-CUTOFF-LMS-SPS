// Truck ETC (hours) next to the hours left to each truck's pick cutoff.
(function () {
  const el = document.getElementById("etc-chart");
  const data = JSON.parse(document.getElementById("chart-data").textContent);
  if (!data.trucks.length) { el.innerHTML = '<p class="text-muted">No truck has open work at this snapshot.</p>'; return; }
  const etc = data.etc.map(v => (v === null ? 0 : v));
  const traces = [
    { type: "bar", name: "ETC (hours)", x: data.trucks, y: etc,
      marker: { color: data.status.map(s => (s === "ON TIME" ? "#198754" : s === "NO RATE" ? "#ffc107" : "#dc3545")) },
      text: data.etc.map(v => (v === null ? "no rate" : v.toFixed(2) + " h")), textposition: "outside" },
    { type: "bar", name: "Hours to pick cutoff", x: data.trucks, y: data.cutoff, marker: { color: "#adb5bd" } }
  ];
  Plotly.newPlot(el, traces, { barmode: "group", margin: { t: 20, r: 10, b: 60, l: 50 },
    yaxis: { title: "Hours" }, legend: { orientation: "h", y: 1.12 } }, { displayModeBar: false, responsive: true });
})();
