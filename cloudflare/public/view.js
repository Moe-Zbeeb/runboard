(function (root) {
  const SUFFIXES = [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "k"]];

  function fmt(v) {
    if (v == null || Number.isNaN(v)) return "–";
    const a = Math.abs(v);
    if (a === 0) return "0";
    if (a < 1e-3 || a >= 1e12) return v.toExponential(3);
    if (a >= 1e5) return Number(v.toPrecision(6)).toLocaleString("en-US", { maximumFractionDigits: 0 });
    return Number(v.toPrecision(5)).toString();
  }

  function tick(v) {
    if (v == null) return "";
    const a = Math.abs(v);
    if (a === 0) return "0";
    if (a < 1e-3) return v.toExponential(1);
    for (const [unit, suffix] of SUFFIXES) if (a >= unit) return Number((v / unit).toPrecision(3)) + suffix;
    return Number(v.toPrecision(4)).toString();
  }

  function lastValue(s) {
    if (!s) return null;
    for (let i = s.y.length - 1; i >= 0; i--) if (s.y[i] != null) return { value: s.y[i], step: s.step[i] };
    return null;
  }

  function nearestIndex(ys, idx) {
    if (idx == null || !ys) return null;
    for (let i = Math.min(idx, ys.length - 1); i >= 0; i--) if (ys[i] != null) return i;
    for (let i = idx + 1; i < ys.length; i++) if (ys[i] != null) return i;
    return null;
  }

  function showPoints(xs, ys, xmin, xmax, width) {
    let count = 0, first = null, last = null;
    for (let i = 0; i < xs.length; i++) {
      if (ys[i] == null || xs[i] < xmin || xs[i] > xmax) continue;
      count++;
      if (first == null) first = xs[i];
      last = xs[i];
    }
    if (!count) return false;
    if (count === 1) return true;
    const fraction = xmax > xmin ? (last - first) / (xmax - xmin) : 1;
    return (fraction * width) / (count - 1) >= 8;
  }

  function robustRange(arrays) {
    const values = [];
    for (const ys of arrays) for (const y of ys) if (y != null && Number.isFinite(y)) values.push(y);
    if (values.length < 10) return null;
    values.sort((a, b) => a - b);
    const at = (q) => values[Math.min(values.length - 1, Math.max(0, Math.round(q * (values.length - 1))))];
    const lo = at(0.01), hi = at(0.99);
    const pad = (hi - lo) * 0.1 || Math.abs(hi) * 0.05 || 1;
    return [lo - pad, hi + pad];
  }

  function flatten(value, prefix, out) {
    out = out || {};
    for (const [k, v] of Object.entries(value || {})) {
      const key = prefix ? prefix + "." + k : k;
      if (v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).length) flatten(v, key, out);
      else out[key] = v;
    }
    return out;
  }

  function configDiff(configs) {
    if (configs.length < 2) return [];
    const flat = configs.map((c) => {
      const copy = Object.assign({}, c || {});
      delete copy.runboard_binned_charts;
      return flatten(copy);
    });
    const keys = [...new Set(flat.flatMap((f) => Object.keys(f)))].sort();
    return keys
      .map((key) => ({ key, values: flat.map((f) => f[key]) }))
      .filter((row) => new Set(row.values.map((v) => (v === undefined ? "\u0000" : JSON.stringify(v)))).size > 1);
  }

  function matcher(text) {
    if (!text) return { invalid: false, test: () => true };
    try {
      const re = new RegExp(text, "i");
      return { invalid: false, test: (s) => re.test(s) };
    } catch (e) {
      const needle = text.toLowerCase();
      return { invalid: true, test: (s) => s.toLowerCase().includes(needle) };
    }
  }

  function duration(s) {
    if (s == null || !Number.isFinite(s) || s < 0) return "";
    s = Math.round(s);
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m " + (s % 60) + "s";
    if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
    return Math.floor(s / 86400) + "d " + Math.floor((s % 86400) / 3600) + "h";
  }

  function defaultSelection(runs, project, n) {
    if (!runs.length) return [];
    const recent = (a, b) => (b.updated || 0) - (a.updated || 0);
    const target = project || [...runs].sort(recent)[0].project;
    return runs.filter((r) => r.project === target).sort(recent).slice(0, n).map((r) => r.project + "/" + r.run_id);
  }

  function placeChildren(parent, nodes) {
    nodes.forEach((node, i) => {
      const at = parent.children[i];
      if (at === node) return;
      if (at) parent.insertBefore(node, at);
      else parent.appendChild(node);
    });
  }

  root.RunboardView = { fmt, tick, lastValue, nearestIndex, showPoints, robustRange, flatten, configDiff, matcher, duration, defaultSelection, placeChildren };
  if (typeof module !== "undefined") module.exports = root.RunboardView;
})(typeof globalThis === "undefined" ? window : globalThis);
