const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function element() {
  return {
    innerHTML: "",
    textContent: "",
    value: "",
    checked: false,
    hidden: false,
    dataset: {},
    style: {},
    clientWidth: 800,
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    appendChild() {},
    remove() {},
    showModal() {},
    querySelector() { return element(); },
    querySelectorAll() { return []; },
  };
}

const elements = new Map();
const status = `<img src=x onerror="globalThis.pwned=1">`;
const runs = [{ project: "p", run_id: "r", name: "run", status, created: 1, updated: Date.now() / 1000 }];
const context = {
  console,
  Promise,
  setTimeout,
  clearTimeout,
  setInterval() {},
  localStorage: { getItem() { return null; }, setItem() {} },
  getComputedStyle() { return { getPropertyValue() { return ""; } }; },
  uPlot: Object.assign(function (opts, data) {
    return { over: { addEventListener() {} }, hooks: {}, axes: [], select: {}, series: [], data, setData() {}, destroy() {}, setCursor() {}, setLegend() {} };
  }, { join: (tables) => [tables[0][0], ...tables.map((table) => table[1])] }),
  matchMedia() { return { matches: false, addEventListener() {} }; },
  addEventListener() {},
  fetch: async (url) => ({
    ok: true,
    status: 200,
    json: async () => (url.startsWith("/api/runs") ? runs : url.endsWith("offset=0") ? { rows: [{ loss: 1, _step: 0, _time: 1 }], offset: 1 } : { rows: [], offset: 1 }),
  }),
  document: {
    hidden: false,
    documentElement: { dataset: {} },
    addEventListener() {},
    createElement: () => element(),
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
  },
};
context.window = context;
context.globalThis = context;

vm.createContext(context);
for (const file of ["binned.js", "app.js"]) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../../src/runboard/static", file), "utf8"), context);
}

setTimeout(() => {
  assert.equal(elements.get("live-text").textContent, "live");
  const html = elements.get("runs").innerHTML + elements.get("summary").innerHTML;
  assert.ok(elements.get("summary").innerHTML.includes("<tbody><tr>"), "summary table was not rendered");
  assert.ok(!html.includes("<img"), "run status was rendered as HTML");
  console.log("dashboard escaping checks passed");
}, 100);
