const COOKIE = "runboard_token";
const NAME = /^[A-Za-z0-9._-]{1,128}$/;
const MAX_BODY = 1500000;

let schemaReady;

function response(body, status = 200, headers = {}) {
  const value = typeof body === "string" ? body : JSON.stringify(body);
  return new Response(value, {
    status,
    headers: {
      "Content-Type": typeof body === "string" ? "text/html; charset=utf-8" : "application/json",
      "Cache-Control": "no-store",
      ...headers,
    },
  });
}

function cookieToken(request) {
  const cookie = request.headers.get("Cookie") || "";
  for (const part of cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === COOKIE) {
      try {
        return decodeURIComponent(rest.join("="));
      } catch {
        return "";
      }
    }
  }
  return "";
}

function suppliedToken(request, url) {
  const auth = request.headers.get("Authorization") || "";
  if (auth.startsWith("Bearer ")) return auth.slice(7).trim();
  return url.searchParams.get("token") || cookieToken(request);
}

async function digest(value) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
}

async function equalToken(left, right) {
  const [a, b] = await Promise.all([digest(left), digest(right)]);
  let different = 0;
  for (let i = 0; i < a.length; i++) different |= a[i] ^ b[i];
  return different === 0;
}

async function authorize(request, env, url) {
  if (!env.RUNBOARD_TOKEN) return false;
  return equalToken(suppliedToken(request, url), env.RUNBOARD_TOKEN);
}

function ensureSchema(env) {
  if (!schemaReady) {
    schemaReady = env.DB.batch([
      env.DB.prepare("CREATE TABLE IF NOT EXISTS runs (project TEXT NOT NULL, run_id TEXT NOT NULL, name TEXT NOT NULL, meta TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, PRIMARY KEY (project, run_id))"),
      env.DB.prepare("CREATE TABLE IF NOT EXISTS metric_batches (id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, run_id TEXT NOT NULL, rows_json TEXT NOT NULL, row_count INTEGER NOT NULL, created REAL NOT NULL, batch_key TEXT NOT NULL UNIQUE)"),
      env.DB.prepare("CREATE INDEX IF NOT EXISTS runs_created ON runs (created DESC)"),
      env.DB.prepare("CREATE INDEX IF NOT EXISTS metric_batches_run ON metric_batches (project, run_id, id)"),
    ]).catch((error) => {
      schemaReady = undefined;
      throw error;
    });
  }
  return schemaReady;
}

function validName(value) {
  return typeof value === "string" && NAME.test(value) && value !== "." && value !== "..";
}

function cleanRows(value) {
  if (!Array.isArray(value)) throw new Error("rows must be an array");
  const rows = [];
  for (const source of value) {
    if (!source || typeof source !== "object" || Array.isArray(source)) continue;
    const row = {};
    for (const [key, item] of Object.entries(source)) {
      if (key === "_sid" && typeof item === "string") row[key] = item;
      else if (["_step", "_time", "_seq"].includes(key) && Number.isFinite(item)) row[key] = item;
      else if (!key.startsWith("_") && typeof item === "number" && Number.isFinite(item)) row[key] = item;
      else if (!key.startsWith("_") && item === null) row[key] = null;
    }
    rows.push(row);
  }
  return rows;
}

async function hashRows(project, runId, rows) {
  const bytes = await digest(`${project}\n${runId}\n${JSON.stringify(rows)}`);
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function updateRun(env, project, runId, meta, updated) {
  const currentRow = await env.DB.prepare("SELECT meta, created FROM runs WHERE project = ? AND run_id = ?")
    .bind(project, runId)
    .first();
  let current = {};
  if (currentRow) {
    try {
      current = JSON.parse(currentRow.meta);
    } catch {
      current = {};
    }
  }
  const merged = { ...current, ...meta };
  merged.project = project;
  merged.run_id = runId;
  merged.name = merged.name || runId;
  merged.created = Number.isFinite(merged.created) ? merged.created : currentRow?.created || updated;
  await env.DB.prepare("INSERT INTO runs (project, run_id, name, meta, created, updated) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (project, run_id) DO UPDATE SET name = excluded.name, meta = excluded.meta, updated = excluded.updated")
    .bind(project, runId, String(merged.name), JSON.stringify(merged), merged.created, updated)
    .run();
}

async function postRun(request, env, project, runId) {
  if (!validName(project) || !validName(runId)) return response({ error: "invalid project/run name" }, 400);
  const declared = Number(request.headers.get("Content-Length") || 0);
  if (declared > MAX_BODY) return response({ error: "body too large" }, 413);
  const bytes = await request.arrayBuffer();
  if (bytes.byteLength > MAX_BODY) return response({ error: "body too large" }, 413);
  let body;
  try {
    body = JSON.parse(new TextDecoder().decode(bytes) || "{}");
  } catch {
    return response({ error: "invalid JSON" }, 400);
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return response({ error: "body must be an object" }, 400);
  const meta = body.meta && typeof body.meta === "object" && !Array.isArray(body.meta) ? body.meta : {};
  let rows;
  try {
    rows = cleanRows(body.rows || []);
  } catch (error) {
    return response({ error: error.message }, 400);
  }
  const updated = rows.reduce((latest, row) => Math.max(latest, Number(row._time) || 0), Date.now() / 1000);
  if (rows.length) {
    const batchKey = await hashRows(project, runId, rows);
    const exists = await env.DB.prepare("SELECT 1 FROM metric_batches WHERE batch_key = ?").bind(batchKey).first();
    if (!exists) {
      await env.DB.prepare("INSERT OR IGNORE INTO metric_batches (project, run_id, rows_json, row_count, created, batch_key) VALUES (?, ?, ?, ?, ?, ?)")
        .bind(project, runId, JSON.stringify(rows), rows.length, updated, batchKey)
        .run();
    }
  }
  await updateRun(env, project, runId, meta, updated);
  return response({ ok: true, n: rows.length });
}

async function listRuns(env) {
  const result = await env.DB.prepare("SELECT project, run_id, meta, updated FROM runs ORDER BY created DESC").all();
  const runs = result.results.map((row) => {
    let meta = {};
    try {
      meta = JSON.parse(row.meta);
    } catch {
      meta = {};
    }
    return { ...meta, project: row.project, run_id: row.run_id, updated: row.updated };
  });
  return response(runs);
}

async function getMetrics(env, url) {
  const project = url.searchParams.get("project") || "";
  const runId = url.searchParams.get("run") || "";
  const rawOffset = Number(url.searchParams.get("offset") || 0);
  if (!validName(project) || !validName(runId) || !Number.isSafeInteger(rawOffset) || rawOffset < 0) {
    return response({ error: "invalid project, run, or offset" }, 400);
  }
  const batch = await env.DB.prepare("SELECT id, rows_json FROM metric_batches WHERE project = ? AND run_id = ? AND id > ? ORDER BY id LIMIT 1")
    .bind(project, runId, rawOffset)
    .first();
  if (!batch) return response({ rows: [], offset: rawOffset });
  let rows = [];
  try {
    rows = JSON.parse(batch.rows_json);
  } catch {
    rows = [];
  }
  return response({ rows, offset: batch.id });
}

async function handle(request, env) {
  const url = new URL(request.url);
  const authorized = await authorize(request, env, url);
  if (!authorized) {
    if (url.pathname.startsWith("/api/")) return response({ error: "unauthorized" }, 401);
    return response("<h3>runboard: unauthorized</h3><p>Open your dashboard URL with its <code>?token=...</code> value.</p>", 401);
  }
  if (url.pathname === "/" && url.searchParams.has("token")) {
    return response("", 302, {
      Location: "/",
      "Set-Cookie": `${COOKIE}=${encodeURIComponent(env.RUNBOARD_TOKEN)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=31536000`,
    });
  }
  await ensureSchema(env);
  if (request.method === "GET" && url.pathname === "/api/health") return response({ ok: true, storage: "cloudflare" });
  if (request.method === "GET" && url.pathname === "/api/runs") return listRuns(env);
  if (request.method === "GET" && url.pathname === "/api/metrics") return getMetrics(env, url);
  if (request.method === "POST") {
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length === 4 && parts[0] === "api" && parts[1] === "runs") {
      let project;
      let runId;
      try {
        project = decodeURIComponent(parts[2]);
        runId = decodeURIComponent(parts[3]);
      } catch {
        return response({ error: "invalid project/run name" }, 400);
      }
      return postRun(request, env, project, runId);
    }
  }
  if (request.method === "GET" || request.method === "HEAD") return env.ASSETS.fetch(request);
  return response({ error: "not found" }, 404);
}

export default {
  async fetch(request, env) {
    try {
      return await handle(request, env);
    } catch (error) {
      console.error(error);
      return response({ error: "service unavailable" }, 503);
    }
  },
};
