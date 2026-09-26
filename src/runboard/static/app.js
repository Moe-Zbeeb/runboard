(() => {
  const MAX_SELECTED = 8;
  const POLL_MS = 2000;
  const STALE_S = 600;

  const state = {
    runs: new Map(),
    data: new Map(),
    selected: [],
    slot: new Map(),
    project: null,
    xMode: "step",
    smoothing: 0,
    logY: false,
    theme: "system",
    runFilter: "",
    metricFilter: "",
    wide: new Set(),
  };
  const charts = new Map();
  const $ = (id) => document.getElementById(id);

  const store = {
    get(k, d) { try { const v = localStorage.getItem("runboard:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("runboard:" + k, JSON.stringify(v)); } catch {} },
  };

  const key = (r) => `${r.project}/${r.run_id}`;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const colorOf = (k) => css(`--s${(state.slot.get(k) ?? 0) + 1}`);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function fmt(v) {
    if (v == null || Number.isNaN(v)) return "–";
    const a = Math.abs(v);
    if (a !== 0 && (a < 1e-3 || a >= 1e5)) return v.toExponential(3);
    return Number(v.toPrecision(5)).toString();
  }

  function tick(v) {
    if (v == null) return "";
    const a = Math.abs(v);
    if (a !== 0 && (a < 1e-3 || a >= 1e5)) return v.toExponential(1);
    return Number(v.toPrecision(4)).toLocaleString();
  }

  function axisSize(u, values, axisIdx, cycleNum) {
    if (cycleNum > 1) return u.axes[axisIdx]._size;
    const longest = (values || []).reduce((m, s) => Math.max(m, String(s).length), 0);
    return Math.max(40, longest * 7 + 18);
  }

  function ago(t) {
    if (!t) return "";
    const s = Date.now() / 1000 - t;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return `${Math.round(s / 86400)}d ago`;
  }

  function statusOf(r) {
    const s = r.status || "running";
    if (s === "running" && r.updated && Date.now() / 1000 - r.updated > STALE_S) return "stale";
    return s;
  }
  const STATUS_ICON = { running: "●", finished: "✓", crashed: "✕", killed: "✕", stale: "!" };

  async function api(path) {
    const res = await fetch(path, { credentials: "same-origin", cache: "no-store" });
    if (res.status === 401) throw new Error("unauthorized");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  function setMessage(html) {
    const m = $("message");
    m.hidden = !html;
    m.innerHTML = html || "";
  }

  function select(k, on) {
    const i = state.selected.indexOf(k);
    if (on && i < 0) {
      if (state.selected.length >= MAX_SELECTED) return false;
      const used = new Set(state.selected.map((s) => state.slot.get(s)));
      let slot = 0;
      while (used.has(slot)) slot++;
      state.slot.set(k, slot);
      state.selected.push(k);
    } else if (!on && i >= 0) {
      state.selected.splice(i, 1);
      state.slot.delete(k);
    }
    store.set("selected", state.selected);
    return true;
  }

  function renderProjects() {
    const projects = [...new Set([...state.runs.values()].map((r) => r.project))].sort();
    const sel = $("project");
    const cur = state.project;
    sel.innerHTML = `<option value="">All projects</option>` + projects.map((p) => `<option ${p === cur ? "selected" : ""}>${esc(p)}</option>`).join("");
  }

  function visibleRuns() {
    const f = state.runFilter.toLowerCase();
    return [...state.runs.values()]
      .filter((r) => !state.project || r.project === state.project)
      .filter((r) => !f || `${r.name} ${r.run_id} ${r.project} ${(r.tags || []).join(" ")}`.toLowerCase().includes(f))
      .sort((a, b) => (b.created || 0) - (a.created || 0));
  }

  let lastRunsHtml = "";
  function renderRuns() {
    const ul = $("runs");
    let html;
    const runs = visibleRuns();
    if (!runs.length) {
      html = `<li class="muted">No runs yet.</li>`;
    } else {
      html = runs.map((r) => {
        const k = key(r);
        const on = state.selected.includes(k);
        const st = statusOf(r);
        const full = !on && state.selected.length >= MAX_SELECTED;
        return `<li>
          <input type="checkbox" data-k="${esc(k)}" ${on ? "checked" : ""} ${full ? "disabled title='Up to 8 runs can be compared at once'" : ""} aria-label="Show ${esc(r.name)}">
          <span class="swatch" style="background:${on ? colorOf(k) : "transparent"}"></span>
          <span class="info">
            <button class="name" type="button" data-details="${esc(k)}" title="${esc(r.name)}">${esc(r.name)}</button>
            <span class="sub"><span class="status ${st}">${STATUS_ICON[st] || "·"} ${st}</span><span>${esc(state.project ? "" : r.project)}</span><span>${ago(r.updated)}</span></span>
          </span>
        </li>`;
      }).join("");
    }
    if (html !== lastRunsHtml) {
      ul.innerHTML = html;
      lastRunsHtml = html;
    }
    $("sel-count").textContent = `${state.selected.length} selected (max ${MAX_SELECTED})`;
  }

  async function refreshRuns() {
    const list = await api("/api/runs");
    const seen = new Set();
    for (const r of list) {
      const k = key(r);
      seen.add(k);
      state.runs.set(k, r);
    }
    for (const k of [...state.runs.keys()]) if (!seen.has(k)) state.runs.delete(k);
    state.selected.filter((k) => !state.runs.has(k)).forEach((k) => select(k, false));
    if (!state.selected.length && !store.get("touched", false)) {
      visibleRuns().slice(0, 3).forEach((r) => select(key(r), true));
    }
    renderProjects();
    renderRuns();
    if (!list.length) {
      setMessage(`No runs yet. In your training script:<pre>import runboard
runboard.init(project="my-project", config={"lr": 3e-4})
for step in range(1000):
    runboard.log({"train/loss": loss}, step=step)
runboard.finish()</pre>`);
    } else {
      setMessage("");
    }
  }

  async function refreshData() {
    let changed = false;
    await Promise.all(state.selected.map(async (k) => {
      const r = state.runs.get(k);
      if (!r) return;
      let d = state.data.get(k);
      if (!d) {
        d = { offset: 0, t0: null, series: new Map() };
        state.data.set(k, d);
      }
      for (let guard = 0; guard < 1000; guard++) {
        const q = `/api/metrics?project=${encodeURIComponent(r.project)}&run=${encodeURIComponent(r.run_id)}&offset=${d.offset}`;
        const res = await api(q);
        if (res.rows.length) changed = true;
        ingest(d, res.rows);
        const done = res.offset === d.offset || !res.rows.length;
        d.offset = res.offset;
        if (done) break;
      }
    }));
    return changed;
  }

  function ingest(d, rows) {
    for (const row of rows) {
      const step = row._step, t = row._time;
      if (d.t0 == null && t != null) d.t0 = t;
      for (const [m, v] of Object.entries(row)) {
        if (m[0] === "_") continue;
        let s = d.series.get(m);
        if (!s) { s = { step: [], t: [], y: [] }; d.series.set(m, s); }
        s.step.push(step);
        s.t.push(t);
        s.y.push(v);
      }
    }
  }

  function smooth(y, a) {
    if (!a) return y;
    const out = new Array(y.length);
    let last = 0, w = 0;
    for (let i = 0; i < y.length; i++) {
      const v = y[i];
      if (v == null) { out[i] = null; continue; }
      last = last * a + (1 - a) * v;
      w = w * a + (1 - a);
      out[i] = last / w;
    }
    return out;
  }

  function xyFor(k, m) {
    const d = state.data.get(k);
    const s = d && d.series.get(m);
    if (!s) return null;
    let x = state.xMode === "step" ? s.step : state.xMode === "rel" ? s.t.map((t) => (t - d.t0) / 60) : s.t;
    let y = smooth(s.y, state.smoothing);
    if (state.logY) y = y.map((v) => (v != null && v > 0 ? v : null));
    let sorted = true;
    for (let i = 1; i < x.length; i++) if (x[i] < x[i - 1]) { sorted = false; break; }
    if (!sorted) {
      const idx = x.map((_, i) => i).sort((a, b) => x[a] - x[b]);
      x = idx.map((i) => x[i]);
      y = idx.map((i) => y[i]);
    }
    if (x.length > 2000) {
      const bucketSize = Math.ceil(x.length / 500);
      const indices = [];
      for (let start = 0; start < x.length; start += bucketSize) {
        const end = Math.min(x.length, start + bucketSize);
        let min = start, max = start;
        for (let i = start + 1; i < end; i++) {
          if (y[i] != null && (y[min] == null || y[i] < y[min])) min = i;
          if (y[i] != null && (y[max] == null || y[i] > y[max])) max = i;
        }
        for (const i of [start, min, max, end - 1].sort((a, b) => a - b)) {
          if (indices[indices.length - 1] !== i) indices.push(i);
        }
      }
      x = indices.map((i) => x[i]);
      y = indices.map((i) => y[i]);
    }
    return [x, y];
  }

  function binnedDefinition(k, metric) {
    return RunboardBinned.definitions(state.runs.get(k)?.config).find((item) => "relationships/" + item.title === metric);
  }

  function metricNames() {
    const names = new Set();
    for (const k of state.selected) {
      const d = state.data.get(k);
      if (d) for (const m of d.series.keys()) {
        const detail = RunboardBinned.definitions(state.runs.get(k)?.config).some((item) => m.startsWith(item.prefix + "/bin_"));
        if (!detail || state.metricFilter) names.add(m);
      }
      for (const definition of RunboardBinned.definitions(state.runs.get(k)?.config)) names.add("relationships/" + definition.title);
    }
    let re = null;
    try { re = state.metricFilter ? new RegExp(state.metricFilter, "i") : null; } catch { re = null; }
    return [...names].filter((m) => !re || re.test(m)).sort();
  }

  function groupOf(m) {
    const i = m.indexOf("/");
    return i > 0 ? m.slice(0, i) : "";
  }

  function chartData(m) {
    const tables = [], ks = [], steps = [];
    let binned = null;
    for (const k of state.selected) {
      const definition = binnedDefinition(k, m);
      const result = definition && state.data.get(k) ? RunboardBinned.latestXY(state.data.get(k).series, definition) : null;
      let xy = definition ? result?.data : xyFor(k, m);
      if (definition && state.logY && xy) xy = [xy[0], xy[1].map((y) => y > 0 ? y : null)];
      if (definition) binned = definition;
      if (xy && xy[0].length) { tables.push(xy); ks.push(k); steps.push(result?.step); }
    }
    if (!tables.length) return { data: null, ks, binned, steps };
    return { data: uPlot.join(tables), ks, binned, steps };
  }

  function makeOpts(ks, width, binned, steps) {
    const muted = css("--muted"), grid = css("--grid"), axis = css("--axis");
    const ax = { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { stroke: axis, width: 1, size: 4 }, font: "11px system-ui, sans-serif" };
    return {
      width,
      height: 240,
      pxAlign: false,
      cursor: { drag: { x: true, y: false }, points: { size: 8 } },
      scales: { x: { time: !binned && state.xMode === "wall" }, y: { distr: state.logY ? 3 : 1 } },
      axes: [{ ...ax }, { ...ax, size: axisSize, values: (u, vals) => vals.map(tick) }],
      legend: { live: true },
      series: [
        { label: binned ? binned.x_label : state.xMode === "step" ? "step" : state.xMode === "rel" ? "min" : "time", value: !binned && state.xMode === "wall" ? undefined : (u, v) => fmt(v) },
        ...ks.map((k, i) => ({
          label: (state.runs.get(k)?.name || k) + (binned ? " (step " + steps[i] + ")" : ""),
          stroke: colorOf(k),
          width: 2,
          spanGaps: true,
          points: { show: Boolean(binned), size: 5 },
          value: (u, v) => fmt(v),
        })),
      ],
    };
  }

  function showLatest(c) {
    if (!c.u || c.hover) return;
    const n = c.u.data[0].length;
    if (n) c.u.setLegend({ idx: n - 1 });
  }

  function renderCharts() {
    const root = $("charts");
    const metrics = metricNames();
    const theme = css("--surface");
    const wanted = new Set(metrics);
    for (const [m, c] of charts) {
      if (!wanted.has(m)) { c.u?.destroy(); c.card.remove(); charts.delete(m); }
    }
    if (!state.selected.length) {
      root.innerHTML = state.runs.size ? `<p class="muted">Select runs in the sidebar to plot them.</p>` : "";
      charts.clear();
      return;
    }
    root.querySelectorAll(":scope > p").forEach((p) => p.remove());

    const groups = new Map();
    for (const m of metrics) {
      const g = groupOf(m);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(m);
    }
    const order = [...groups.keys()].sort((a, b) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b)));
    const sections = new Map([...root.querySelectorAll(":scope > section")].map((s) => [s.dataset.g, s]));
    for (const [g, s] of sections) if (!groups.has(g)) s.remove();

    for (const g of order) {
      let sec = sections.get(g);
      if (!sec) {
        sec = document.createElement("section");
        sec.dataset.g = g;
        sec.innerHTML = `<h2 class="section-title">${esc(g || "metrics")}</h2><div class="grid"></div>`;
      }
      root.appendChild(sec);
      const grid = sec.querySelector(".grid");
      for (const m of groups.get(g)) {
        let c = charts.get(m);
        if (!c) {
          const card = document.createElement("div");
          card.className = "card" + (state.wide.has(m) ? " wide" : "");
          card.innerHTML = `<div class="card-head"><div class="card-title" title="${esc(m)}">${esc(m)}</div>
            <div class="card-actions"><button type="button" data-wide>${state.wide.has(m) ? "Shrink" : "Expand"}</button></div></div>
            <div class="plot"></div>`;
          c = { card, u: null, sig: "", zoomed: false };
          card.querySelector("[data-wide]").onclick = (e) => {
            if (state.wide.has(m)) state.wide.delete(m); else state.wide.add(m);
            store.set("wide", [...state.wide]);
            card.classList.toggle("wide", state.wide.has(m));
            e.target.textContent = state.wide.has(m) ? "Shrink" : "Expand";
            c.sig = "";
            renderCharts();
          };
          charts.set(m, c);
        }
        grid.appendChild(c.card);
        const { data, ks, binned, steps } = chartData(m);
        const plot = c.card.querySelector(".plot");
        const width = Math.max(200, plot.clientWidth || c.card.clientWidth - 24);
        const sig = [ks.join("|"), ks.map((k) => state.slot.get(k)).join(","), state.xMode, state.logY, theme, width, binned?.prefix, steps.join(",")].join(";");
        if (!data) {
          c.u?.destroy(); c.u = null; c.sig = "";
          plot.innerHTML = `<p class="muted">No data for the selected runs.</p>`;
          continue;
        }
        if (c.u && c.sig === sig) {
          c.u.setData(data, !c.zoomed);
          showLatest(c);
        } else {
          c.u?.destroy();
          plot.innerHTML = "";
          const u = new uPlot(makeOpts(ks, width, binned, steps), data, plot);
          u.over.addEventListener("dblclick", () => { c.zoomed = false; });
          u.over.addEventListener("mouseenter", () => { c.hover = true; });
          u.over.addEventListener("mouseleave", () => { c.hover = false; setTimeout(() => showLatest(c), 0); });
          u.hooks.setSelect = [(uu) => { if (uu.select.width > 0) c.zoomed = true; }];
          c.u = u;
          c.sig = sig;
          c.zoomed = false;
          showLatest(c);
        }
      }
    }
  }

  function renderSummary() {
    const t = $("summary");
    const metrics = metricNames();
    if (!state.selected.length || !metrics.length) { t.innerHTML = ""; return; }
    const head = `<tr><th>Run</th><th>Status</th><th>Step</th>${metrics.map((m) => `<th>${esc(m)}</th>`).join("")}</tr>`;
    const rows = state.selected.map((k) => {
      const r = state.runs.get(k);
      const d = state.data.get(k);
      let step = null;
      const cells = metrics.map((m) => {
        const s = d?.series.get(m);
        if (!s || !s.y.length) return "<td>–</td>";
        step = Math.max(step ?? -Infinity, s.step[s.step.length - 1]);
        return `<td>${fmt(s.y[s.y.length - 1])}</td>`;
      }).join("");
      const st = statusOf(r || {});
      return `<tr><td><span class="swatch" style="display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;background:${colorOf(k)}"></span>${esc(r?.name || k)}</td><td class="status ${st}">${STATUS_ICON[st] || ""} ${st}</td><td>${step ?? "–"}</td>${cells}</tr>`;
    }).join("");
    t.innerHTML = `<thead>${head}</thead><tbody>${rows}</tbody>`;
  }

  function redraw() {
    renderCharts();
    renderSummary();
  }

  let busy = false;
  let lastRunsSig = "";
  async function poll() {
    if (busy) return;
    busy = true;
    try {
      await refreshRuns();
      const changed = await refreshData();
      const sig = JSON.stringify([...state.runs.values()].map((r) => [key(r), r.status, r.name]));
      if (changed || sig !== lastRunsSig || !charts.size) redraw();
      lastRunsSig = sig;
      $("live").classList.remove("error");
      $("live-text").textContent = "live";
    } catch (e) {
      $("live").classList.add("error");
      $("live-text").textContent = e.message === "unauthorized" ? "unauthorized" : "reconnecting…";
      if (e.message === "unauthorized") setMessage("Unauthorized. Open the full URL printed by <code>runboard serve</code> (ending in <code>?token=…</code>).");
    } finally {
      busy = false;
    }
  }

  function showDetails(k) {
    const r = state.runs.get(k);
    if (!r) return;
    $("details-title").textContent = r.name;
    const { config, ...rest } = r;
    $("details-body").textContent = JSON.stringify({ config, ...rest }, null, 2);
    $("details").showModal();
  }

  function bind() {
    state.selected = [];
    for (const k of store.get("selected", [])) select(k, true);
    state.wide = new Set(store.get("wide", []));
    state.project = store.get("project", null);
    state.xMode = store.get("xmode", "step");
    state.smoothing = store.get("smoothing", 0);
    state.logY = store.get("logy", false);
    const savedTheme = store.get("theme", "system");
    state.theme = ["system", "light", "dark"].includes(savedTheme) ? savedTheme : "system";
    if (state.theme === "light" || state.theme === "dark") document.documentElement.dataset.theme = state.theme;
    $("xmode").value = state.xMode;
    $("smooth").value = state.smoothing;
    $("smooth-val").textContent = state.smoothing;
    $("logy").checked = state.logY;
    $("theme").textContent = `${state.theme[0].toUpperCase()}${state.theme.slice(1)} theme`;

    $("project").onchange = (e) => { state.project = e.target.value || null; store.set("project", state.project); renderRuns(); };
    $("xmode").onchange = (e) => { state.xMode = e.target.value; store.set("xmode", state.xMode); redraw(); };
    $("smooth").oninput = (e) => { state.smoothing = +e.target.value; $("smooth-val").textContent = state.smoothing; store.set("smoothing", state.smoothing); redraw(); };
    $("logy").onchange = (e) => { state.logY = e.target.checked; store.set("logy", state.logY); redraw(); };
    $("theme").onclick = () => {
      state.theme = ({ system: "dark", dark: "light", light: "system" })[state.theme];
      if (state.theme === "system") delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = state.theme;
      store.set("theme", state.theme);
      $("theme").textContent = `${state.theme[0].toUpperCase()}${state.theme.slice(1)} theme`;
      redraw();
    };
    $("metric-filter").oninput = (e) => { state.metricFilter = e.target.value; redraw(); };
    $("run-filter").oninput = (e) => { state.runFilter = e.target.value; renderRuns(); };
    $("select-none").onclick = () => { [...state.selected].forEach((k) => select(k, false)); store.set("touched", true); renderRuns(); redraw(); };
    $("runs").addEventListener("change", async (e) => {
      const k = e.target.dataset.k;
      if (!k) return;
      store.set("touched", true);
      select(k, e.target.checked);
      renderRuns();
      await refreshData().catch(() => {});
      redraw();
    });
    $("runs").addEventListener("click", (e) => {
      const k = e.target.closest("[data-details]")?.dataset.details;
      if (k) showDetails(k);
    });
    let rt;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(redraw, 150); });
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { renderRuns(); redraw(); });
  }

  bind();
  poll();
  setInterval(poll, POLL_MS);
})();
