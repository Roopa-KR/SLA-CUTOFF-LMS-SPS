// Charts of the truck drill-down. Data comes from the page (#detail-data).
(function () {
  const d = JSON.parse(document.getElementById("detail-data").textContent);
  const cfg = { displayModeBar: false, responsive: true };
  const base = { margin: { t: 10, r: 10, b: 40, l: 50 }, legend: { orientation: "h", y: -0.2 } };
  const colorFor = m => (m === "YES" ? "#198754" : m === "NO" || (m || "").startsWith("NO -") ? "#dc3545" : "#ffc107");
  const empty = (id, msg) => { document.getElementById(id).innerHTML = '<p class="text-muted small">' + msg + "</p>"; };

  // 1. Work types: bar from now to ready time, cutoff as a line
  const wt = d.worktypes.filter(w => w.ready);
  if (wt.length) {
    const traces = [{ type: "bar", orientation: "h", y: wt.map(w => w.name), base: wt.map(() => d.now),
      x: wt.map(w => (new Date(w.ready) - new Date(d.now))), marker: { color: wt.map(w => colorFor(w.meets)) },
      name: "Ready (now)", hovertext: wt.map(w => w.name + ": ready " + w.ready.slice(11, 16)), hoverinfo: "text" }];
    const after = wt.filter(w => w.ready_after && w.temps);
    if (after.length) traces.push({ type: "scatter", mode: "markers", y: after.map(w => w.name), x: after.map(w => w.ready_after),
      marker: { symbol: "diamond", size: 11, color: "#0d6efd" }, name: "Ready with temporary operators" });
    Plotly.newPlot("wt-chart", traces, Object.assign({}, base, { xaxis: { type: "date", tickformat: "%H:%M" },
      shapes: [{ type: "line", x0: d.cutoff, x1: d.cutoff, yref: "paper", y0: 0, y1: 1, line: { color: "#212529", dash: "dash" } }],
      annotations: [{ x: d.cutoff, yref: "paper", y: 1.05, text: "pick cutoff " + d.cutoff.slice(11, 16), showarrow: false, font: { size: 11 } }] }), cfg);
  } else empty("wt-chart", "No open work: nothing to finish.");

  // 2. Operator status donut and pace vs area average
  const st = {}; d.operators.forEach(o => { st[o.status] = (st[o.status] || 0) + 1; });
  if (d.operators.length) {
    Plotly.newPlot("status-chart", [{ type: "pie", hole: 0.55, labels: Object.keys(st), values: Object.values(st), sort: false,
      marker: { colors: Object.keys(st).map(s => ({ "Working": "#198754", "Assigned, not started": "#ffc107", "Idle": "#6c757d", "Logged out": "#ced4da" }[s])) } }],
      { margin: { t: 0, b: 0, l: 0, r: 0 }, showlegend: true, legend: { orientation: "v" } }, cfg);
    const p = d.operators.filter(o => o.pace);
    Plotly.newPlot("pace-chart", [
      { type: "bar", name: "Pace (lines/h)", x: p.map(o => o.name), y: p.map(o => o.pace),
        marker: { color: p.map(o => (o.pct_of_avg !== null && o.pct_of_avg < 0.6 ? "#dc3545" : "#0d6efd")) } },
      { type: "scatter", mode: "markers", name: "Area average", x: p.map(o => o.name), y: p.map(o => o.wt_avg),
        marker: { symbol: "line-ew-open", size: 22, color: "#212529", line: { width: 2 } } }],
      Object.assign({}, base, { yaxis: { title: "lines / hour" }, margin: { t: 10, r: 10, b: 80, l: 50 } }), cfg);
  } else { empty("status-chart", "No operators yet."); empty("pace-chart", ""); }

  // 3. Open lines (bars) and slack (line) over the snapshots
  Plotly.newPlot("burn-chart", [
    { type: "bar", name: "Open lines", x: d.trends.labels, y: d.trends.open_lines, marker: { color: "#adb5bd" } },
    { type: "scatter", mode: "lines+markers", name: "Slack (min)", x: d.trends.labels, y: d.trends.slack, yaxis: "y2",
      line: { color: "#dc3545" }, connectgaps: false }],
    Object.assign({}, base, { yaxis: { title: "open lines" }, yaxis2: { title: "slack min", overlaying: "y", side: "right", zeroline: true } }), cfg);

  // 4. Throughput per 15 minutes, stacked by work type
  const names = d.wt_names;
  const tr = Object.entries(d.trends.throughput).map(([w, ys]) => ({ type: "bar", name: names[w] || ("WT " + w), x: d.trends.labels, y: ys }));
  if (tr.length) Plotly.newPlot("through-chart", tr, Object.assign({}, base, { barmode: "stack", yaxis: { title: "lines" } }), cfg);
  else empty("through-chart", "Nothing picked yet.");

  // 5. Pickers logged in per area over time
  const pk = Object.entries(d.trends.pickers).map(([w, ys]) => ({ type: "scatter", mode: "lines", line: { shape: "hv" },
    name: names[w] || ("WT " + w), x: d.trends.labels, y: ys }));
  if (pk.length) Plotly.newPlot("pickers-chart", pk, Object.assign({}, base, { yaxis: { title: "pickers", dtick: 1 } }), cfg);
  else empty("pickers-chart", "No areas yet.");
})();
