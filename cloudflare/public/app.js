(() => {
  const V = RunboardView;
  const MAX_SELECTED = 8;
  const POLL_MS = 5000;
  const STALE_S = 600;
  const TRACE_S = 900;
  const TRACE_W = 180;
  const TRACE_BINS = 30;
  const CHART_H = 220;

  const state = {
    runs: new Map(),
    data: new Map(),
    selected: [],
    slot: new Map(),
    project: null,
    xMode: "step",
    smoothing: 0,
    logY: false,
    clip: false,
    theme: "system",
    runFilter: "",
    metricFilter: "",
    wide: new Set(),
    collapsed: new Set(),
    zoom: null,
    focus: null,
  };
  const charts = new Map();
  const beats = new Map();
  const $ = (id) => document.getElementById(id);

  const store = {
    get(k, d) { try { const v = localStorage.getItem("runboard:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("runboard:" + k, JSON.stringify(v)); } catch {} },
  };

  const key = (r) => `${r.project}/${r.run_id}`;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const colorOf = (k) => css(`--s${(state.slot.get(k) ?? 0) + 1}`);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const nameOf = (k) => state.runs.get(k)?.name || k;

  function axisSize(u, values, axisIdx, cycleNum) {
    if (cycleNum > 1) return u.axes[axisIdx]._size;
    const longest = (values || []).reduce((m, s) => Math.max(m, String(s).length), 0);
    return Math.max(36, longest * 7 + 16);
  }

  function ago(t) {
    if (!t) return "";
    const s = Date.now() / 1000 - t;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return `${Math.round(s / 86400)}d ago`;
  }

  function when(t) {
    if (!t) return "–";
    return new Date(t * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }

  function statusOf(r) {
    const s = r.status || "running";
    if (s === "running" && r.updated && Date.now() / 1000 - r.updated > STALE_S) return "stale";
    return s;
  }
  const STATUS_ICON = { running: "●", finished: "✓", crashed: "✕", killed: "✕", stale: "!" };
  const statusHtml = (st) => `<span class="status ${esc(st)}">${STATUS_ICON[st] || "·"} ${esc(st)}</span>`;

  function runDuration(r) {
    const end = r.finished || (statusOf(r) === "running" ? Date.now() / 1000 : r.updated);
    return r.created && end ? V.duration(end - r.created) : "";
  }

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
    if (state.project && state.runs.size && !projects.includes(state.project)) {
      state.project = null;
      store.set("project", null);
    }
    const sel = $("project");
    sel.innerHTML = `<option value="">All projects</option>` + projects.map((p) => `<option value="${esc(p)}"${p === state.project ? " selected" : ""}>${esc(p)}</option>`).join("");
    sel.value = state.project || "";
  }

  function pruneToProject() {
    if (!state.project) return;
    for (const k of [...state.selected]) {
      const r = state.runs.get(k);
      if (r && r.project !== state.project) select(k, false);
    }
  }

  function visibleRuns() {
    const f = state.runFilter.toLowerCase();
    return [...state.runs.values()]
      .filter((r) => !state.project || r.project === state.project)
      .filter((r) => !f || `${r.name} ${r.run_id} ${r.project} ${(r.tags || []).join(" ")}`.toLowerCase().includes(f))
      .sort((a, b) => (b.created || 0) - (a.created || 0));
  }

  function activity(k) {
    const now = Date.now() / 1000, from = now - TRACE_S;
    const bins = new Array(TRACE_BINS).fill(false);
    const mark = (t) => {
      if (t >= from && t <= now + 5) bins[Math.min(TRACE_BINS - 1, Math.floor(((t - from) / TRACE_S) * TRACE_BINS))] = true;
    };
    const d = state.data.get(k);
    if (d) for (const s of d.series.values()) for (let i = s.t.length - 1; i >= 0 && s.t[i] >= from; i--) mark(s.t[i]);
    for (const t of beats.get(k) || []) mark(t);
    return bins;
  }

  function tracePath(bins) {
    const step = TRACE_W / TRACE_BINS;
    let d = "M0 9";
    bins.forEach((on, i) => {
      if (!on) return;
      const x = (i + 0.5) * step;
      d += `L${(x - 1.4).toFixed(1)} 9L${(x - 0.4).toFixed(1)} 3L${(x + 0.6).toFixed(1)} 15L${(x + 1.4).toFixed(1)} 9`;
    });
    return d + `L${TRACE_W} 9`;
  }

  function updatePulses(runs) {
    const items = $("runs").querySelectorAll("li[data-run]");
    runs.forEach((r, i) => {
      const li = items[i];
      if (!li) return;
      li.querySelector(".beat").setAttribute("d", tracePath(activity(key(r))));
      li.querySelector(".ago").textContent = ago(r.updated);
    });
  }

  let lastRunsHtml = "";
  function renderRuns() {
    const ul = $("runs");
    let html;
    const runs = visibleRuns();
    if (!runs.length) {
      html = `<li class="empty muted">${state.runs.size ? "No runs match your search." : "No runs yet."}</li>`;
    } else {
      html = runs.map((r) => {
        const k = key(r);
        const on = state.selected.includes(k);
        const st = statusOf(r);
        const full = !on && state.selected.length >= MAX_SELECTED;
        return `<li data-run="${esc(k)}" class="${on ? "on" : ""}">
          <input type="checkbox" data-k="${esc(k)}" ${on ? "checked" : ""} ${full ? `disabled title="Up to ${MAX_SELECTED} runs can be compared at once"` : ""} aria-label="Plot ${esc(r.name)}">
          <span class="swatch" style="background:${on ? colorOf(k) : "transparent"}"></span>
          <span class="info">
            <button class="name" type="button" data-details="${esc(k)}" title="Show details for ${esc(r.name)}">${esc(r.name)}</button>
            <span class="sub">${statusHtml(st)}${state.project ? "" : `<span>${esc(r.project)}</span>`}</span>
          </span>
          <span class="pulse ${esc(st)}" title="Metric writes over the last 15 minutes">
            <svg viewBox="0 0 ${TRACE_W} 18" preserveAspectRatio="none" aria-hidden="true"><path class="base" d="M0 9H${TRACE_W}"/><path class="beat" d="M0 9H${TRACE_W}"/></svg>
            <span class="ago"></span>
          </span>
        </li>`;
      }).join("");
    }
    if (html !== lastRunsHtml) {
      ul.innerHTML = html;
      lastRunsHtml = html;
    }
    updatePulses(runs);
    const count = `${state.selected.length} selected`;
    $("sel-count").textContent = state.selected.length >= MAX_SELECTED ? `${count} (max ${MAX_SELECTED})` : count;
    const total = state.project ? [...state.runs.values()].filter((r) => r.project === state.project).length : state.runs.size;
    $("runs-toggle-count").textContent = `${state.selected.length} of ${total} selected`;
  }

  async function refreshRuns() {
    const list = await api("/api/runs");
    const seen = new Set();
    const cutoff = Date.now() / 1000 - TRACE_S;
    for (const r of list) {
      const k = key(r);
      seen.add(k);
      state.runs.set(k, r);
      const history = (beats.get(k) || []).filter((t) => t >= cutoff);
      if (r.updated && history[history.length - 1] !== r.updated) history.push(r.updated);
      beats.set(k, history);
    }
    for (const k of [...state.runs.keys()]) if (!seen.has(k)) state.runs.delete(k);
    state.selected.filter((k) => !state.runs.has(k)).forEach((k) => select(k, false));
    renderProjects();
    pruneToProject();
    if (!state.selected.length && !store.get("touched", false)) {
      V.defaultSelection([...state.runs.values()], state.project, 3).forEach((k) => select(k, true));
    }
    renderRuns();
    if (!list.length) {
      setMessage(`<strong>No runs yet.</strong> Wrap your training command:<pre>runboard run --project my-project -- python train.py</pre>or log from Python:<pre>import runboard
runboard.init(project="my-project", config={"lr": 3e-4})
for step in range(1000):
    runboard.log({"train/loss": loss}, step=step)
runboard.finish()</pre>`);
    } else {
      setMessage("");
    }
  }

  let dataQueue = Promise.resolve();
  function refreshData() {
    const next = dataQueue.then(fetchSelected);
    dataQueue = next.catch(() => false);
    return next;
  }

  async function fetchSelected() {
    let changed = false;
    await Promise.all(state.selected.map(async (k) => {
      const r = state.runs.get(k);
      if (!r) return;
      let d = state.data.get(k);
      if (!d) {
        d = { offset: 0, t0: null, series: new Map(), seen: null };
        state.data.set(k, d);
      }
      const seen = `${r.status}|${r.updated}`;
      if (d.seen === seen) return;
      for (let guard = 0; guard < 1000; guard++) {
        const q = `/api/metrics?project=${encodeURIComponent(r.project)}&run=${encodeURIComponent(r.run_id)}&offset=${d.offset}`;
        const res = await api(q);
        if (res.rows.length) changed = true;
        ingest(d, res.rows);
        const done = res.offset === d.offset || !res.rows.length;
        d.offset = res.offset;
        if (done) { d.seen = seen; break; }
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

  function xyFor(k, m, alpha) {
    const d = state.data.get(k);
    const s = d && d.series.get(m);
    if (!s) return null;
    let x = state.xMode === "step" ? s.step : state.xMode === "rel" ? s.t.map((t) => (t - d.t0) / 60) : s.t;
    let y = s.y;
    let sorted = true;
    for (let i = 1; i < x.length; i++) if (x[i] < x[i - 1]) { sorted = false; break; }
    if (!sorted) {
      const idx = x.map((_, i) => i).sort((a, b) => x[a] - x[b]);
      x = idx.map((i) => x[i]);
      y = idx.map((i) => y[i]);
    }
    y = smooth(y, alpha);
    if (state.logY) y = y.map((v) => (v != null && v > 0 ? v : null));
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

  function definitionsFor(k) {
    return RunboardBinned.definitions(state.runs.get(k)?.config);
  }

  function binnedDefinition(k, metric) {
    return definitionsFor(k).find((item) => "relationships/" + item.title === metric);
  }

  function metricNames() {
    const names = new Set();
    for (const k of state.selected) {
      const defs = definitionsFor(k);
      const d = state.data.get(k);
      if (d) for (const m of d.series.keys()) {
        const detail = defs.some((item) => m.startsWith(item.prefix + "/bin_"));
        if (!detail || state.metricFilter) names.add(m);
      }
      for (const definition of defs) names.add("relationships/" + definition.title);
    }
    const match = V.matcher(state.metricFilter);
    return [...names].filter((m) => match.test(m)).sort();
  }

  function groupOf(m) {
    const i = m.indexOf("/");
    return i > 0 ? m.slice(0, i) : "";
  }

  function chartData(m) {
    const tables = [], raws = [], ks = [], steps = [];
    let binned = null;
    for (const k of state.selected) {
      const definition = binnedDefinition(k, m);
      const result = definition && state.data.get(k) ? RunboardBinned.latestXY(state.data.get(k).series, definition) : null;
      let xy = definition ? result?.data : xyFor(k, m, state.smoothing);
      if (definition && state.logY && xy) xy = [xy[0], xy[1].map((y) => (y > 0 ? y : null))];
      if (definition) binned = definition;
      if (xy && xy[0].length) {
        tables.push(xy);
        ks.push(k);
        steps.push(result?.step);
        if (!definition && state.smoothing > 0) raws.push(xyFor(k, m, 0));
      }
    }
    if (!tables.length) return { data: null, ks, binned, steps, raw: 0 };
    const raw = raws.length === tables.length ? raws.length : 0;
    return { data: uPlot.join(raw ? raws.concat(tables) : tables), ks, binned, steps, raw };
  }

  function formatX(v, binned) {
    if (v == null) return "";
    if (binned) return `${binned.x_label} ${V.fmt(v)}`;
    if (state.xMode === "wall") return when(v);
    if (state.xMode === "rel") return `${V.fmt(v)} min`;
    return `step ${Math.round(v).toLocaleString("en-US")}`;
  }

  function visibleArrays(u) {
    const xs = u.data[0], lo = u.scales.x.min, hi = u.scales.x.max;
    if (lo == null || hi == null) return u.data.slice(1);
    return u.data.slice(1).map((ys) => ys.filter((y, i) => xs[i] >= lo && xs[i] <= hi));
  }

  function pointsShow(u, si) {
    const ratio = typeof devicePixelRatio === "number" ? devicePixelRatio : 1;
    return V.showPoints(u.data[0], u.data[si], u.scales.x.min, u.scales.x.max, u.bbox.width / ratio);
  }

  function makeOpts(c, ks, width, binned, raw) {
    const muted = css("--muted"), grid = css("--grid"), axis = css("--axis");
    const ax = { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { stroke: axis, width: 1, size: 4 }, font: "11px Recursive, system-ui, sans-serif" };
    const xAxis = { ...ax };
    if (binned || state.xMode !== "wall") xAxis.values = (u, vals) => vals.map(V.tick);
    const yScale = { distr: state.logY ? 3 : 1 };
    if (state.clip && !binned && !state.logY) yScale.range = (u, min, max) => V.robustRange(visibleArrays(u)) || uPlot.rangeNum(min, max, 0.1, true);
    return {
      width,
      height: CHART_H,
      pxAlign: false,
      legend: { show: false },
      focus: { alpha: 0.2 },
      cursor: {
        sync: { key: binned ? "bin:" + binned.prefix : "x:" + state.xMode },
        drag: { x: !binned, y: false, setScale: !binned },
        points: { size: 7 },
        focus: { prox: 16 },
      },
      scales: { x: { time: !binned && state.xMode === "wall" }, y: yScale },
      axes: [xAxis, { ...ax, size: axisSize, values: (u, vals) => vals.map(V.tick) }],
      series: [
        { label: binned ? binned.x_label : state.xMode },
        ...(raw ? ks : []).map((k) => ({
          label: `${nameOf(k)} (raw)`,
          stroke: colorOf(k) + "3a",
          width: 1,
          spanGaps: true,
          points: { show: false },
        })),
        ...ks.map((k) => ({
          label: nameOf(k),
          stroke: colorOf(k),
          width: 2,
          spanGaps: true,
          points: { show: binned ? true : pointsShow, size: binned ? 5 : 6 },
        })),
      ],
      hooks: {
        setCursor: [() => updateReadout(c)],
        setSelect: [(u) => {
          if (binned || !(u.select.width > 0)) return;
          const lo = u.posToVal(u.select.left, "x"), hi = u.posToVal(u.select.left + u.select.width, "x");
          setZoom([lo, hi]);
        }],
      },
    };
  }

  function applyZoom(c, reset) {
    if (!c.u || c.binned || (!state.zoom && !reset)) return;
    const xs = c.u.data[0];
    if (!xs || !xs.length) return;
    const [min, max] = state.zoom || [xs[0], xs[xs.length - 1]];
    c.u.setScale("x", { min, max });
  }

  function zoomLabel([lo, hi]) {
    if (state.xMode === "wall") return `${when(lo)} to ${when(hi)}`;
    if (state.xMode === "rel") return `${V.fmt(lo)} to ${V.fmt(hi)} min`;
    return `Steps ${Math.round(lo).toLocaleString("en-US")} to ${Math.round(hi).toLocaleString("en-US")}`;
  }

  function setZoom(range) {
    state.zoom = range;
    $("zoombar").hidden = !range;
    $("zoom-range").textContent = range ? zoomLabel(range) : "";
    for (const c of charts.values()) applyZoom(c, true);
  }

  function lastIndex(ys) {
    for (let i = ys.length - 1; i >= 0; i--) if (ys[i] != null) return i;
    return null;
  }

  function updateReadout(c) {
    const u = c.u;
    if (!u || !c.chips) return;
    const idx = u.cursor?.idx;
    const hovering = idx != null && (u.cursor.left ?? -1) >= 0;
    c.readX.textContent = hovering ? formatX(u.data[0][idx], c.binned) : c.binned ? binnedSteps(c) : "latest";
    const rows = [];
    c.chips.forEach((chip, n) => {
      const ys = u.data[c.offset + n + 1] || [];
      const j = hovering ? V.nearestIndex(ys, idx) : lastIndex(ys);
      const v = j == null ? null : ys[j];
      chip.value.textContent = V.fmt(v);
      rows.push({ k: c.ks[n], v, step: c.binned ? c.steps[n] : null });
    });
    if (!c.tip) return;
    if (!hovering || !c.hover) { c.tip.hidden = true; return; }
    rows.sort((a, b) => (b.v ?? -Infinity) - (a.v ?? -Infinity));
    c.tip.innerHTML = `<div class="tip-x">${esc(formatX(u.data[0][idx], c.binned))}</div>` + rows.map((r) =>
      `<div class="tip-row"><i style="background:${colorOf(r.k)}"></i><span class="tip-name">${esc(nameOf(r.k))}${r.step != null ? ` (step ${esc(r.step)})` : ""}</span><span class="tip-v">${esc(V.fmt(r.v))}</span></div>`).join("");
    c.tip.hidden = false;
    const over = u.over;
    const left = over.offsetLeft + u.cursor.left;
    const w = c.tip.offsetWidth;
    const room = c.plot.clientWidth;
    c.tip.style.left = `${left + 14 + w > room ? Math.max(0, left - 14 - w) : left + 14}px`;
    c.tip.style.top = `${over.offsetTop + 6}px`;
  }

  function binnedSteps(c) {
    const unique = [...new Set(c.steps)];
    return unique.length === 1 ? `step ${unique[0]}` : "latest step of each run";
  }

  function buildReadout(c, ks) {
    const box = c.card.querySelector(".chips");
    box.innerHTML = ks.map((k, n) => `<span class="chip" data-i="${n + 1}" title="${esc(nameOf(k))}${c.binned ? ` (step ${esc(c.steps[n])})` : ""}"><i style="background:${colorOf(k)}"></i><span class="v"></span></span>`).join("");
    c.chips = [...box.querySelectorAll(".chip")].map((el) => ({ el, value: el.querySelector(".v") }));
    c.chips.forEach(({ el }, n) => {
      el.addEventListener("mouseenter", () => c.u?.setSeries(c.offset + n + 1, { focus: true }));
      el.addEventListener("mouseleave", () => c.u?.setSeries(state.focus && c.ks.includes(state.focus) ? c.offset + c.ks.indexOf(state.focus) + 1 : null, { focus: true }));
    });
  }

  function focusRun(k) {
    if (state.focus === k) return;
    state.focus = k;
    for (const c of charts.values()) {
      if (!c.u) continue;
      const i = k ? c.ks.indexOf(k) : -1;
      c.u.setSeries(i >= 0 ? c.offset + i + 1 : null, { focus: true });
    }
  }

  function placeholder(root, html) {
    for (const [m, c] of charts) { c.u?.destroy(); charts.delete(m); }
    root.innerHTML = html ? `<p class="placeholder muted">${html}</p>` : "";
  }

  function makeSection(g) {
    const sec = document.createElement("section");
    sec.dataset.g = g;
    sec.innerHTML = `<h2 class="section-title"><button type="button" class="section-toggle" aria-expanded="true"><svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5 5 6.5 8 3.5"/></svg><span class="g">${esc(g || "metrics")}</span><span class="count muted"></span></button></h2><div class="grid"></div>`;
    sec.querySelector(".section-toggle").onclick = () => {
      if (state.collapsed.has(g)) state.collapsed.delete(g); else state.collapsed.add(g);
      store.set("collapsed", [...state.collapsed]);
      renderCharts();
      renderSummary();
    };
    return sec;
  }

  function makeCard(m) {
    const card = document.createElement("div");
    card.className = "card" + (state.wide.has(m) ? " wide" : "");
    card.innerHTML = `<div class="card-head"><div class="card-title" title="${esc(m)}">${esc(m)}</div>
      <div class="card-actions"><button type="button" data-wide aria-pressed="${state.wide.has(m)}">${state.wide.has(m) ? "Shrink" : "Expand"}</button></div></div>
      <div class="plot"></div>
      <div class="readout"><span class="read-x"></span><span class="chips"></span></div>`;
    const c = { card, plot: card.querySelector(".plot"), readX: card.querySelector(".read-x"), u: null, sig: "", ks: [], offset: 0, hover: false };
    card.querySelector("[data-wide]").onclick = (e) => {
      if (state.wide.has(m)) state.wide.delete(m); else state.wide.add(m);
      store.set("wide", [...state.wide]);
      card.classList.toggle("wide", state.wide.has(m));
      e.target.textContent = state.wide.has(m) ? "Shrink" : "Expand";
      e.target.setAttribute("aria-pressed", String(state.wide.has(m)));
      c.sig = "";
      renderCharts();
    };
    return c;
  }

  function drawChart(c, m) {
    const { data, ks, binned, steps, raw } = chartData(m);
    const width = Math.max(200, c.plot.clientWidth || c.card.clientWidth - 28);
    const theme = css("--surface");
    const sig = [ks.join("|"), ks.map((k) => state.slot.get(k)).join(","), ks.map(nameOf).join("|"), state.xMode, state.logY, state.clip, raw, theme, width, binned?.prefix, steps.join(",")].join(";");
    if (!data) {
      c.u?.destroy(); c.u = null; c.sig = ""; c.chips = null;
      c.plot.innerHTML = `<p class="muted">No data for the selected runs.</p>`;
      c.card.querySelector(".chips").innerHTML = "";
      c.readX.textContent = "";
      return;
    }
    if (c.u && c.sig === sig) {
      c.u.setData(data);
      applyZoom(c);
      updateReadout(c);
      return;
    }
    c.u?.destroy();
    c.plot.innerHTML = "";
    c.ks = ks;
    c.binned = binned;
    c.steps = steps;
    c.offset = raw;
    buildReadout(c, ks);
    const u = new uPlot(makeOpts(c, ks, width, binned, raw), data, c.plot);
    c.u = u;
    c.sig = sig;
    c.tip = document.createElement("div");
    c.tip.className = "tip";
    c.tip.hidden = true;
    c.plot.appendChild(c.tip);
    u.over.addEventListener("dblclick", () => { if (!binned) setZoom(null); });
    u.over.addEventListener("mouseenter", () => { c.hover = true; });
    u.over.addEventListener("mouseleave", () => { c.hover = false; if (c.tip) c.tip.hidden = true; updateReadout(c); });
    applyZoom(c);
    if (state.focus && ks.includes(state.focus)) u.setSeries(raw + ks.indexOf(state.focus) + 1, { focus: true });
    updateReadout(c);
  }

  function renderCharts() {
    const root = $("charts");
    const filter = $("metric-filter");
    const match = V.matcher(state.metricFilter);
    filter.classList.toggle("invalid", match.invalid);
    filter.title = match.invalid ? "Not a valid regular expression, so it is matched as plain text" : "";
    if (!state.selected.length) {
      placeholder(root, state.runs.size ? "Select runs in the sidebar to plot them." : "");
      return;
    }
    const metrics = metricNames();
    if (!metrics.length) {
      const loaded = state.selected.every((k) => state.data.get(k)?.seen);
      placeholder(root, state.metricFilter ? `No metrics match “${esc(state.metricFilter)}”.` : loaded ? "The selected runs have not logged any metrics yet." : "Loading metrics…");
      return;
    }
    root.querySelectorAll(":scope > p").forEach((p) => p.remove());

    const groups = new Map();
    for (const m of metrics) {
      const g = groupOf(m);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(m);
    }
    const shown = new Set(metrics.filter((m) => !state.collapsed.has(groupOf(m))));
    for (const [m, c] of charts) {
      if (!shown.has(m)) { c.u?.destroy(); c.card.remove(); charts.delete(m); }
    }
    const order = [...groups.keys()].sort((a, b) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b)));
    const existing = new Map([...root.querySelectorAll(":scope > section")].map((s) => [s.dataset.g, s]));
    for (const [g, s] of existing) if (!groups.has(g)) s.remove();
    const sections = order.map((g) => existing.get(g) || makeSection(g));
    V.placeChildren(root, sections);

    sections.forEach((sec, i) => {
      const g = order[i];
      const names = groups.get(g);
      const collapsed = state.collapsed.has(g);
      sec.classList.toggle("collapsed", collapsed);
      sec.querySelector(".section-toggle").setAttribute("aria-expanded", String(!collapsed));
      sec.querySelector(".count").textContent = `${names.length} ${names.length === 1 ? "chart" : "charts"}`;
      const grid = sec.querySelector(".grid");
      grid.hidden = collapsed;
      if (collapsed) return;
      const cards = names.map((m) => {
        if (!charts.has(m)) charts.set(m, makeCard(m));
        return charts.get(m);
      });
      V.placeChildren(grid, cards.map((c) => c.card));
      names.forEach((m, n) => drawChart(cards[n], m));
    });
  }

  function cell(v) {
    if (v === undefined) return `<td class="muted">–</td>`;
    if (typeof v === "number") {
      const a = Math.abs(v);
      return `<td class="num">${esc(a !== 0 && (a < 1e-3 || a >= 1e15) ? v.toExponential() : String(v))}</td>`;
    }
    if (typeof v === "string") return `<td>${esc(v)}</td>`;
    return `<td>${esc(JSON.stringify(v))}</td>`;
  }

  function renderSummary() {
    const t = $("summary");
    const ks = state.selected.filter((k) => state.runs.has(k));
    $("summary-wrap").hidden = !ks.length;
    if (!ks.length) { t.innerHTML = ""; return; }
    const metrics = metricNames().filter((m) => !m.startsWith("relationships/"));
    const head = `<tr><th scope="col"></th>${ks.map((k) => `<th scope="col" title="${esc(nameOf(k))}"><span class="swatch-line" style="background:${colorOf(k)}"></span>${esc(nameOf(k))}</th>`).join("")}</tr>`;
    const row = (label, cells) => `<tr><th scope="row">${esc(label)}</th>${cells}</tr>`;
    const group = (label) => `<tr class="group"><th scope="rowgroup" colspan="${ks.length + 1}">${esc(label)}</th></tr>`;
    const runsOf = ks.map((k) => state.runs.get(k));
    let body = row("Status", runsOf.map((r) => `<td>${statusHtml(statusOf(r))}</td>`).join(""));
    body += row("Duration", runsOf.map((r) => `<td class="num">${esc(runDuration(r) || "–")}</td>`).join(""));
    body += row("Latest step", ks.map((k) => {
      let step = null;
      for (const s of state.data.get(k)?.series.values() || []) {
        const last = V.lastValue(s);
        if (last && (step == null || last.step > step)) step = last.step;
      }
      return step == null ? `<td class="muted">–</td>` : `<td class="num">${esc(step.toLocaleString("en-US"))}</td>`;
    }).join(""));
    const diff = V.configDiff(runsOf.map((r) => r.config || {}));
    if (diff.length) {
      body += group("Config that differs");
      for (const d of diff) body += row(d.key, d.values.map(cell).join(""));
    }
    let current = null;
    for (const m of metrics) {
      const g = groupOf(m) || "metrics";
      if (g !== current) { body += group(g); current = g; }
      body += row(m, ks.map((k) => {
        const last = V.lastValue(state.data.get(k)?.series.get(m));
        return last ? `<td class="num" title="at step ${esc(last.step)}">${esc(V.fmt(last.value))}</td>` : `<td class="muted">–</td>`;
      }).join(""));
    }
    t.innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
  }

  function redraw() {
    renderCharts();
    renderSummary();
  }

  let busy = false;
  let lastRunsSig = "";
  async function poll() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      await refreshRuns();
      const changed = await refreshData();
      updatePulses(visibleRuns());
      const sig = JSON.stringify([...state.runs.values()].map((r) => [key(r), r.status, r.name]));
      if (changed || sig !== lastRunsSig || !charts.size) redraw();
      lastRunsSig = sig;
      $("live").classList.remove("error");
      $("live-text").textContent = "live";
      $("live").title = `Refreshes every 5 seconds while this tab is open. Last refresh ${new Date().toLocaleTimeString()}.`;
    } catch (e) {
      $("live").classList.add("error");
      $("live-text").textContent = e.message === "unauthorized" ? "unauthorized" : "reconnecting…";
      if (e.message === "unauthorized") setMessage("<strong>Unauthorized.</strong> Open the full dashboard URL, the one that ends in <code>?token=…</code>. <code>runboard url</code> prints it.");
    } finally {
      busy = false;
    }
  }

  function showDetails(k) {
    const r = state.runs.get(k);
    if (!r) return;
    const st = statusOf(r);
    $("details-title").innerHTML = `${esc(r.name)} ${statusHtml(st)}`;
    const facts = [
      ["Project", esc(r.project)],
      ["Run ID", `<code>${esc(r.run_id)}</code>`],
      ["Started", esc(when(r.created))],
      [r.finished ? "Finished" : "Last update", esc(r.finished ? when(r.finished) : `${when(r.updated)} (${ago(r.updated)})`)],
      ["Duration", esc(runDuration(r) || "–")],
      ["Host", r.host ? esc(r.host) : ""],
      ["Slurm job", r.slurm_job_id ? `<code>${esc(r.slurm_job_id)}</code>` : ""],
      ["Command", Array.isArray(r.argv) && r.argv.length ? `<code class="cmd">${esc(r.argv.join(" "))}</code>` : ""],
      ["Tags", (r.tags || []).length ? r.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("") : ""],
    ].filter(([, v]) => v);
    const config = V.flatten(Object.assign({}, r.config || {}, { runboard_binned_charts: undefined }));
    delete config.runboard_binned_charts;
    const keys = Object.keys(config).sort();
    $("details-body").innerHTML = `<dl class="facts">${facts.map(([label, v]) => `<dt>${label}</dt><dd>${v}</dd>`).join("")}</dl>
      <h4>Config</h4>
      ${keys.length ? `<table class="config"><tbody>${keys.map((key) => `<tr><th scope="row">${esc(key)}</th>${cell(config[key])}</tr>`).join("")}</tbody></table>` : `<p class="muted">This run has no config.</p>`}
      <details class="raw"><summary>Raw metadata</summary><pre>${esc(JSON.stringify(r, null, 2))}</pre></details>`;
    $("details").showModal();
  }

  function applyTheme() {
    if (state.theme === "light" || state.theme === "dark") document.documentElement.dataset.theme = state.theme;
    else delete document.documentElement.dataset.theme;
  }

  function repaint() {
    lastRunsHtml = "";
    renderRuns();
    for (const c of charts.values()) c.sig = "";
    redraw();
  }

  function bind() {
    state.selected = [];
    for (const k of store.get("selected", [])) select(k, true);
    state.wide = new Set(store.get("wide", []));
    state.collapsed = new Set(store.get("collapsed", []));
    state.project = store.get("project", null);
    state.xMode = store.get("xmode", "step");
    state.smoothing = store.get("smoothing", 0);
    state.logY = store.get("logy", false);
    state.clip = store.get("clip", false);
    const savedTheme = store.get("theme", "system");
    state.theme = ["system", "light", "dark"].includes(savedTheme) ? savedTheme : "system";
    applyTheme();
    $("xmode").value = state.xMode;
    $("smooth").value = state.smoothing;
    $("smooth-val").textContent = state.smoothing;
    $("logy").checked = state.logY;
    $("clip").checked = state.clip;
    $("theme").value = state.theme;

    $("project").onchange = async (e) => {
      state.project = e.target.value || null;
      store.set("project", state.project);
      pruneToProject();
      if (!state.selected.length) V.defaultSelection([...state.runs.values()], state.project, 3).forEach((k) => select(k, true));
      setZoom(null);
      renderRuns();
      redraw();
      await refreshData().catch(() => {});
      redraw();
    };
    $("xmode").onchange = (e) => { state.xMode = e.target.value; store.set("xmode", state.xMode); state.zoom = null; $("zoombar").hidden = true; redraw(); };
    $("smooth").oninput = (e) => { state.smoothing = +e.target.value; $("smooth-val").textContent = state.smoothing; store.set("smoothing", state.smoothing); redraw(); };
    $("logy").onchange = (e) => { state.logY = e.target.checked; store.set("logy", state.logY); redraw(); };
    $("clip").onchange = (e) => { state.clip = e.target.checked; store.set("clip", state.clip); redraw(); };
    $("theme").onchange = (e) => { state.theme = e.target.value; store.set("theme", state.theme); applyTheme(); repaint(); };
    $("reset-zoom").onclick = () => setZoom(null);
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
    $("runs").addEventListener("mouseover", (e) => {
      const k = e.target.closest("li[data-run]")?.dataset.run;
      focusRun(k && state.selected.includes(k) ? k : null);
    });
    $("runs").addEventListener("mouseleave", () => focusRun(null));
    $("runs-toggle").onclick = () => {
      const open = $("sidebar").classList.toggle("open");
      $("runs-toggle").setAttribute("aria-expanded", String(open));
    };
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && state.zoom && !$("details").open) { setZoom(null); return; }
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = (document.activeElement?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "select" || tag === "textarea") return;
      e.preventDefault();
      $("metric-filter").focus();
    });
    const topbar = $("topbar");
    if (topbar && typeof ResizeObserver === "function") {
      new ResizeObserver(() => document.documentElement.style.setProperty("--topbar-h", `${topbar.offsetHeight}px`)).observe(topbar);
    }
    let rt;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(redraw, 150); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
    if (document.fonts) document.fonts.load("11px Recursive").then(() => { for (const c of charts.values()) c.sig = ""; redraw(); }).catch(() => {});
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (state.theme === "system") repaint(); });
  }

  bind();
  poll();
  setInterval(poll, POLL_MS);
})();
