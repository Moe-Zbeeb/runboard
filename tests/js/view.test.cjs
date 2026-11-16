const assert = require("node:assert/strict");
const { test } = require("node:test");
require("../../src/runboard/static/view.js");
const V = globalThis.RunboardView;

test("tick uses compact suffixes for large axis values", () => {
  assert.equal(V.tick(0), "0");
  assert.equal(V.tick(20000), "20k");
  assert.equal(V.tick(100000), "100k");
  assert.equal(V.tick(175000), "175k");
  assert.equal(V.tick(1500), "1.5k");
  assert.equal(V.tick(1250000), "1.25M");
  assert.equal(V.tick(2.5e9), "2.5B");
  assert.equal(V.tick(-3000), "-3k");
  assert.equal(V.tick(999), "999");
  assert.equal(V.tick(0.5), "0.5");
  assert.equal(V.tick(1.8), "1.8");
  assert.equal(V.tick(0.00012), "1.2e-4");
  assert.equal(V.tick(null), "");
});

test("fmt shows readable values", () => {
  assert.equal(V.fmt(null), "–");
  assert.equal(V.fmt(NaN), "–");
  assert.equal(V.fmt(0), "0");
  assert.equal(V.fmt(1.03), "1.03");
  assert.equal(V.fmt(753.7612), "753.76");
  assert.equal(V.fmt(0.0021581234), "0.0021581");
  assert.equal(V.fmt(104857.6), "104,858");
  assert.equal(V.fmt(1234567.89), "1,234,570");
  assert.equal(V.fmt(0.00001234), "1.234e-5");
  assert.equal(V.fmt(2.5e13), "2.500e+13");
});

test("lastValue finds the last non-null point and its step", () => {
  assert.deepEqual(V.lastValue({ step: [1, 2, 3], y: [0.5, 0.4, null] }), { value: 0.4, step: 2 });
  assert.equal(V.lastValue({ step: [1], y: [null] }), null);
  assert.equal(V.lastValue(undefined), null);
});

test("nearestIndex prefers the closest earlier point, then a later one", () => {
  assert.equal(V.nearestIndex([1, null, null, 4], 2), 0);
  assert.equal(V.nearestIndex([null, null, 3], 0), 2);
  assert.equal(V.nearestIndex([5, 6], 1), 1);
  assert.equal(V.nearestIndex([null, null], 1), null);
  assert.equal(V.nearestIndex([1, 2], null), null);
});

test("showPoints only marks points that are visibly far apart", () => {
  const xs = Array.from({ length: 400 }, (_, i) => i);
  const dense = xs.map((x) => x);
  assert.equal(V.showPoints(xs, dense, 0, 399, 500), false);
  const short = xs.map((x) => (x < 50 ? x : null));
  assert.equal(V.showPoints(xs, short, 0, 399, 500), false);
  const eval10 = xs.map((x) => (x % 10 === 0 ? x : null));
  assert.equal(V.showPoints(xs, eval10, 0, 399, 500), true);
  assert.equal(V.showPoints(xs, dense, 100, 110, 500), true);
  assert.equal(V.showPoints([0], [1], 0, 0, 500), true);
  assert.equal(V.showPoints(xs, xs.map(() => null), 0, 399, 500), false);
});

test("robustRange ignores rare spikes", () => {
  const ys = Array.from({ length: 1000 }, (_, i) => 1 + (i % 10) / 100);
  ys[500] = 50;
  ys[700] = -40;
  const [lo, hi] = V.robustRange([ys]);
  assert.ok(lo > 0.9 && lo < 1, `lo=${lo}`);
  assert.ok(hi > 1.09 && hi < 1.2, `hi=${hi}`);
  assert.equal(V.robustRange([[1, 2, null]]), null);
  const flat = V.robustRange([Array(20).fill(3)]);
  assert.ok(flat[0] < 3 && flat[1] > 3);
});

test("flatten turns nested config into dotted keys", () => {
  assert.deepEqual(V.flatten({ a: 1, b: { c: [1, 2], d: { e: "x" } }, f: null }), { a: 1, "b.c": [1, 2], "b.d.e": "x", f: null });
});

test("configDiff lists only keys that differ across runs", () => {
  const rows = V.configDiff([
    { lr: 1e-4, bs: 256, opt: { name: "adamw" }, runboard_binned_charts: [1] },
    { lr: 3e-4, bs: 256, opt: { name: "sgd" }, extra: true },
  ]);
  assert.deepEqual(rows, [
    { key: "extra", values: [undefined, true] },
    { key: "lr", values: [1e-4, 3e-4] },
    { key: "opt.name", values: ["adamw", "sgd"] },
  ]);
  assert.deepEqual(V.configDiff([{ a: 1 }]), []);
});

test("matcher falls back to plain text for invalid patterns", () => {
  const ok = V.matcher("kl$");
  assert.equal(ok.invalid, false);
  assert.ok(ok.test("actor/kl") && !ok.test("actor/kl_coef"));
  const bad = V.matcher("(kl");
  assert.equal(bad.invalid, true);
  assert.ok(bad.test("x/(KL") && !bad.test("actor/kl"));
  assert.ok(V.matcher("").test("anything"));
});

test("duration is short and human", () => {
  assert.equal(V.duration(45), "45s");
  assert.equal(V.duration(125), "2m 5s");
  assert.equal(V.duration(3600 * 2 + 60 * 13 + 9), "2h 13m");
  assert.equal(V.duration(86400 * 3 + 3600 * 4), "3d 4h");
  assert.equal(V.duration(null), "");
});

test("defaultSelection picks recent runs from the most recently active project", () => {
  const runs = [
    { project: "a", run_id: "1", updated: 10 },
    { project: "b", run_id: "2", updated: 50 },
    { project: "b", run_id: "3", updated: 40 },
    { project: "a", run_id: "4", updated: 45 },
    { project: "b", run_id: "5", updated: 30 },
    { project: "b", run_id: "6", updated: 20 },
  ];
  assert.deepEqual(V.defaultSelection(runs, null, 3), ["b/2", "b/3", "b/5"]);
  assert.deepEqual(V.defaultSelection(runs, "a", 3), ["a/4", "a/1"]);
  assert.deepEqual(V.defaultSelection([], null, 3), []);
});

test("placeChildren moves nothing when the order is already right", () => {
  function parent(kids) {
    const p = { children: [], moves: 0 };
    p.appendChild = (n) => { p.moves++; p.children = p.children.filter((c) => c !== n).concat([n]); n.parentNode = p; };
    p.insertBefore = (n, ref) => {
      p.moves++;
      p.children = p.children.filter((c) => c !== n);
      p.children.splice(p.children.indexOf(ref), 0, n);
      n.parentNode = p;
    };
    kids.forEach((k) => { p.children.push(k); k.parentNode = p; });
    return p;
  }
  const a = { id: "a" }, b = { id: "b" }, c = { id: "c" };
  const p = parent([a, b, c]);
  V.placeChildren(p, [a, b, c]);
  assert.equal(p.moves, 0);
  V.placeChildren(p, [c, a, b]);
  assert.deepEqual(p.children.map((n) => n.id), ["c", "a", "b"]);
  const d = { id: "d" };
  p.moves = 0;
  V.placeChildren(p, [c, a, b, d]);
  assert.deepEqual(p.children.map((n) => n.id), ["c", "a", "b", "d"]);
  assert.equal(p.moves, 1);
});
