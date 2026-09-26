(function (root) {
  function definitions(config) {
    const items = config && config.runboard_binned_charts;
    if (!Array.isArray(items)) return [];
    return items.filter((item) => item && typeof item.prefix === "string" && typeof item.title === "string" && typeof item.x_label === "string").slice(0, 32);
  }

  function latestXY(series, definition) {
    const countKeys = [...series.keys()].filter((key) => key.startsWith(definition.prefix + "/bin_") && key.endsWith("/count"));
    let step = -Infinity;
    for (const key of countKeys) for (const value of series.get(key).step) if (Number.isFinite(value)) step = Math.max(step, value);
    if (!Number.isFinite(step)) return null;
    function at(key) {
      const values = series.get(key);
      if (!values) return null;
      for (let i = values.step.length - 1; i >= 0; i--) if (values.step[i] === step) return values.y[i];
      return null;
    }
    const points = [];
    for (const key of countKeys) {
      const prefix = key.slice(0, -6);
      const count = at(key), x = at(prefix + "/x_mean"), y = at(prefix + "/" + (definition.y || "mean"));
      if (count > 0 && Number.isFinite(x) && Number.isFinite(y)) points.push([x, y]);
    }
    points.sort((a, b) => a[0] - b[0]);
    return { step, data: [points.map((p) => p[0]), points.map((p) => p[1])] };
  }

  root.RunboardBinned = { definitions, latestXY };
  if (typeof module !== "undefined") module.exports = root.RunboardBinned;
})(typeof globalThis === "undefined" ? window : globalThis);
