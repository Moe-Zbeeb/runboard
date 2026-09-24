import assert from "node:assert/strict";
import { test } from "node:test";

const base = process.env.RUNBOARD_TEST_URL;
const token = process.env.RUNBOARD_TEST_TOKEN;

async function api(path, options = {}) {
  return fetch(`${base}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
}

test("requires authentication", { skip: !base }, async () => {
  const result = await fetch(`${base}/api/health`);
  assert.equal(result.status, 401);
});

test("stores, deduplicates, and reads metric batches", { skip: !base }, async () => {
  const runId = `test-${Date.now()}`;
  const payload = {
    meta: { name: "worker-test", config: { lr: 0.1 }, status: "running", created: Date.now() / 1000 },
    rows: [
      { "train/loss": 1, _step: 0, _time: Date.now() / 1000, _seq: 0, _sid: "test-session" },
      { "train/loss": 0.5, _step: 1, _time: Date.now() / 1000, _seq: 1, _sid: "test-session" },
    ],
  };
  for (let attempt = 0; attempt < 2; attempt++) {
    const result = await api(`/api/runs/test/${runId}`, { method: "POST", body: JSON.stringify(payload) });
    assert.equal(result.status, 200);
  }
  const runs = await api("/api/runs");
  assert.equal(runs.status, 200);
  assert.ok((await runs.json()).some((run) => run.run_id === runId && run.config.lr === 0.1));
  const metrics = await api(`/api/metrics?project=test&run=${runId}&offset=0`);
  assert.equal(metrics.status, 200);
  const first = await metrics.json();
  assert.deepEqual(first.rows.map((row) => row["train/loss"]), [1, 0.5]);
  const next = await api(`/api/metrics?project=test&run=${runId}&offset=${first.offset}`);
  assert.deepEqual(await next.json(), { rows: [], offset: first.offset });
});
