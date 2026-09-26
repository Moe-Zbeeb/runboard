const assert = require("node:assert/strict");
require("../../src/runboard/static/binned.js");
const { definitions, latestXY } = globalThis.RunboardBinned;
assert.deepEqual(definitions({runboard_binned_charts: [null, {}, {title: "test", prefix: "p", x_label: "ratio"}]}), [{title: "test", prefix: "p", x_label: "ratio"}]);
const series = new Map();
function add(bin, field, step, y) { series.set(`p/bin_${bin}/${field}`, {step, y}); }
add("00", "count", [1,2], [10,0]);
add("00", "x_mean", [1], [0.1]);
add("00", "mean", [1], [9]);
add("01", "count", [1,2], [20,2]);
add("01", "x_mean", [1,2], [0.5,0.6]);
add("01", "mean", [1,2], [3,4]);
add("01", "p50", [1,2], [2,3]);
assert.deepEqual(latestXY(series, {prefix: "p"}), {step: 2, data: [[0.6],[4]]});
assert.deepEqual(latestXY(series, {prefix: "p", y: "p50"}), {step: 2, data: [[0.6],[3]]});
assert.equal(latestXY(new Map(), {prefix: "p"}), null);
console.log("binned relationship checks passed");
