// Truck ETC (hours) next to the hours left to each truck's pick cutoff.
(function () {
  const el = document.getElementById("etc-chart");
  const data = JSON.parse(document.getElementById("chart-data").textContent);
  if (!data.trucks.length) { el.innerHTML = '<p class="text-muted">No truck has open work at this snapshot.</p>'; return; }
  // A missing rate means ETC is unknown, not zero. Keep those bars empty and
  // show a separate marker so the chart cannot imply that no-rate work is done.
  const etc = data.etc.map(v => (v === null ? null : v));
  const etcHigh = data.etc_high.map(v => (v === null ? null : v));
  const noRateX = data.trucks.filter((_, i) => data.etc[i] === null);
  const traces = [
    { type: "bar", name: "ETC (hours)", x: data.trucks, y: etc,
      marker: { color: data.status.map(s => (s === "ON TIME" ? "#198754" : "#dc3545")) },
      text: data.etc.map(v => (v === null ? "" : v.toFixed(2) + " h")), textposition: "outside" },
    { type: "scatter", mode: "markers", name: "Slower empirical ETC bound", x: data.trucks, y: etcHigh,
      marker: { color: "#fd7e14", symbol: "line-ew-open", size: 16, line: { width: 2 } },
      hovertemplate: "%{x}: %{y:.2f} h<extra></extra>" },
    { type: "scatter", mode: "markers", name: "ETC unavailable (no rate)",
      x: noRateX, y: noRateX.map(() => 0),
      marker: { color: "#ffc107", symbol: "x", size: 11, line: { width: 2 } },
      hovertemplate: "%{x}: no observed rate<extra></extra>" },
    { type: "bar", name: "Hours to pick cutoff", x: data.trucks, y: data.cutoff, marker: { color: "#adb5bd" } }
  ];
  Plotly.newPlot(el, traces, { barmode: "group", margin: { t: 20, r: 10, b: 60, l: 50 },
    yaxis: { title: "Hours" }, legend: { orientation: "h", y: 1.12 } }, { displayModeBar: false, responsive: true });
})();
