const { spawnSync } = require("child_process");
const fs = require("fs");
const pathModule = require("path");

const EIDOCS = process.env.EIDOCS_CLI || "/opt/eidocs/current/.venv/bin/eidocs";
const ROOT = process.env.EIDOCS_ROOT || "/var/lib/eidocs";
const WORKSPACE = process.env.OPENCLAW_WORKSPACE || "/home/darrow/.openclaw/workspace";
const RECENT_STATE = pathModule.join(WORKSPACE, "runtime", "eidocs-recent.json");
const RECENT_MARKDOWN = pathModule.join(WORKSPACE, "runtime", "eidocs-status.md");

function run(args, timeoutMs = 30000) {
  const proc = spawnSync(EIDOCS, args.concat(["--storage", ROOT]), {
    encoding: "utf8",
    timeout: timeoutMs,
    maxBuffer: 1024 * 1024
  });
  if (proc.error) {
    return { ok: false, error: proc.error.message };
  }
  const output = proc.stdout || proc.stderr || "";
  try {
    return JSON.parse(output);
  } catch (err) {
    return { ok: proc.status === 0, status: proc.status, output };
  }
}

function documentIngest({ path, collection = "default" }) {
  const args = ["job", "submit", path, "--source", "openclaw", "--collection", collection];
  if (String(path || "").toLowerCase().endsWith(".pdf")) {
    args.push("--use-raganything");
  }
  return run(args, 2000);
}

function documentStatus({ job_id }) {
  return run(["job", "status", job_id], 2000);
}

function documentQuery({ query, top_k = 8, doc_id = [], mode = "" }) {
  const selectedMode = mode || ((doc_id || []).length === 1 ? "raganything" : "local");
  const args = ["query", query, "--top-k", String(Math.min(Math.max(Number(top_k) || 8, 1), 8)), "--mode", selectedMode];
  for (const id of doc_id || []) {
    args.push("--doc-id", id);
  }
  return run(args, 30000);
}

function documentSummarize({ query, doc_id = [], mode = "" }) {
  return documentQuery({ query, top_k: 5, doc_id, mode });
}

function extractPdfPaths(payload) {
  const paths = new Set();
  collectPdfPaths(payload, paths, 0);
  return Array.from(paths);
}

function collectPdfPaths(value, paths, depth) {
  if (depth > 8 || value === null || value === undefined) return;
  if (typeof value === "string") {
    for (const path of pathsFromString(value)) paths.add(path);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectPdfPaths(item, paths, depth + 1);
    return;
  }
  if (typeof value === "object") {
    for (const item of Object.values(value)) collectPdfPaths(item, paths, depth + 1);
  }
}

function pathsFromString(value) {
  const found = [];
  const pattern = /\/home\/darrow\/\.openclaw\/media\/inbound\/[^\s\]\|\)]+?\.pdf\b/gi;
  for (const match of value.matchAll(pattern)) {
    const path = cleanPath(match[0]);
    if (isAllowedPdf(path)) found.push(path);
  }
  return found;
}

function cleanPath(value) {
  return String(value || "").replace(/[.,;:'"）】]+$/g, "");
}

function isAllowedPdf(path) {
  const value = String(path || "");
  return value.startsWith("/home/darrow/.openclaw/media/inbound/") && value.toLowerCase().endsWith(".pdf");
}

function safeReadJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (_err) {
    return null;
  }
}

function jobStoreDir() {
  return pathModule.join(ROOT, "jobs");
}

function listRecentJobs({ limit = 8, source = "" } = {}) {
  let files = [];
  try {
    files = fs.readdirSync(jobStoreDir())
      .filter((name) => name.endsWith(".json"))
      .map((name) => {
        const file = pathModule.join(jobStoreDir(), name);
        return { file, mtimeMs: fs.statSync(file).mtimeMs };
      })
      .sort((a, b) => b.mtimeMs - a.mtimeMs);
  } catch (_err) {
    return [];
  }
  const rows = [];
  for (const item of files) {
    const job = safeReadJson(item.file);
    if (!job) continue;
    if (source && job.source !== source) continue;
    rows.push({
      job_id: job.job_id,
      status: job.status,
      source: job.source,
      collection: job.collection,
      source_path: job.source_path,
      use_raganything: Boolean(job.use_raganything),
      doc_id: job.doc_id || null,
      error: job.error || "",
      created_at: job.created_at,
      updated_at: job.updated_at,
    });
    if (rows.length >= limit) break;
  }
  return rows;
}

function documentRecent({ limit = 8, source = "" } = {}) {
  const jobs = listRecentJobs({ limit: Math.min(Math.max(Number(limit) || 8, 1), 20), source });
  return { ok: true, jobs, note: "Use this before answering whether recent PDFs/documents went through eidocs." };
}

function summarizeEvent(event) {
  const text = [];
  collectStrings(event, text, 0);
  return text.join(" ").replace(/\s+/g, " ").slice(0, 500);
}

function collectStrings(value, out, depth) {
  if (depth > 5 || value === null || value === undefined) return;
  if (typeof value === "string") {
    if (value.trim()) out.push(value.trim());
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectStrings(item, out, depth + 1);
    return;
  }
  if (typeof value === "object") {
    for (const item of Object.values(value)) collectStrings(item, out, depth + 1);
  }
}

function rememberAutoIngest(jobs, event) {
  if (!jobs.length) return;
  const enriched = jobs.map((item) => {
    const job = item && item.result && item.result.job ? item.result.job : null;
    return {
      path: item.path,
      job_id: job && job.job_id,
      status: job && job.status,
      source: job && job.source,
      collection: job && job.collection,
      use_raganything: Boolean(job && job.use_raganything),
      doc_id: (job && job.doc_id) || null,
      ok: Boolean(item.result && item.result.ok),
      error: (item.result && item.result.error) || "",
    };
  });
  const payload = {
    updated_at: new Date().toISOString(),
    cognition: "OpenClaw PDF auto-ingest submitted these documents to eidocs. Do not tell the user they did not go through eidocs without checking status.",
    event_hint: summarizeEvent(event),
    jobs: enriched,
    recent_jobs: listRecentJobs({ limit: 8 }),
  };
  try {
    fs.mkdirSync(pathModule.dirname(RECENT_STATE), { recursive: true });
    fs.writeFileSync(RECENT_STATE, JSON.stringify(payload, null, 2) + "\n", "utf8");
    fs.writeFileSync(RECENT_MARKDOWN, renderRecentMarkdown(payload), "utf8");
  } catch (_err) {
    // Cognitive sidecar state is best-effort; ingest itself should not fail because of it.
  }
}

function renderRecentMarkdown(payload) {
  const lines = [
    "# eidocs Recent Document State",
    "",
    `Updated: ${payload.updated_at}`,
    "",
    "Operational rule: recent PDF/document attachments may be auto-submitted to eidocs in the background. Before saying a PDF did not go through eidocs, check eidocs_recent/eidocs_status and this file.",
    "",
    "## Auto-ingest submissions",
  ];
  for (const job of payload.jobs || []) {
    lines.push(`- ${job.path}: job=${job.job_id || "unknown"}, status=${job.status || "unknown"}, collection=${job.collection || ""}, rag=${job.use_raganything}`);
  }
  lines.push("", "## Recent eidocs jobs");
  for (const job of payload.recent_jobs || []) {
    lines.push(`- ${job.job_id}: status=${job.status}, doc=${job.doc_id || "pending"}, source=${job.source}, collection=${job.collection}, path=${job.source_path}`);
  }
  return lines.join("\n") + "\n";
}

function buildPromptContext() {
  const recent = safeReadJson(RECENT_STATE) || { recent_jobs: listRecentJobs({ limit: 5 }), jobs: [] };
  const jobs = (recent.recent_jobs && recent.recent_jobs.length ? recent.recent_jobs : listRecentJobs({ limit: 5 })).slice(0, 5);
  if (!jobs.length) return "";
  const lines = [
    "eidocs document cognition:",
    "- For PDFs/project materials, assume background eidocs auto-ingest may already be running or completed.",
    "- Before saying '没有走 eidocs', check eidocs_recent/eidocs_status or cite the job status below.",
  ];
  for (const job of jobs) {
    lines.push(`- recent job ${job.job_id}: status=${job.status}, doc_id=${job.doc_id || "pending"}, source=${job.source}, collection=${job.collection}, rag=${job.use_raganything}, path=${job.source_path}`);
  }
  return lines.join("\n");
}

async function autoIngestPdfs(event) {
  const paths = extractPdfPaths(event);
  const jobs = [];
  for (const path of paths) {
    jobs.push({ path, result: documentIngest({ path, collection: "feishu-pdf-auto" }) });
  }
  rememberAutoIngest(jobs, event);
  return { ok: true, jobs };
}

async function safeAutoIngest(event) {
  try {
    return autoIngestPdfs(event);
  } catch (err) {
    return { ok: false, error: err && err.message ? err.message : String(err) };
  }
}

function registerTool(api, spec) {
  if (!api || typeof api.registerTool !== "function") return false;
  api.registerTool(spec);
  return true;
}

function registerAutoIngest(api) {
  if (!api || typeof api.on !== "function") return false;
  api.on("message_received", safeAutoIngest);
  return true;
}

const pathParam = {
  type: "object",
  additionalProperties: false,
  required: ["path"],
  properties: {
    path: { type: "string", description: "Absolute path to a document already available on the host." },
    collection: { type: "string", description: "Logical collection name.", default: "default" }
  }
};

const statusParam = {
  type: "object",
  additionalProperties: false,
  required: ["job_id"],
  properties: {
    job_id: { type: "string" }
  }
};

const queryParam = {
  type: "object",
  additionalProperties: false,
  required: ["query"],
  properties: {
    query: { type: "string" },
    top_k: { type: "number", default: 8 },
    mode: { type: "string", enum: ["local", "raganything"], default: "" },
    doc_id: { type: "array", items: { type: "string" }, default: [] }
  }
};

const recentParam = {
  type: "object",
  additionalProperties: false,
  properties: {
    limit: { type: "number", default: 8 },
    source: { type: "string", default: "" }
  }
};

module.exports.default = {
  id: "eidocs-tools",
  name: "eidocs Tools",
  description: "Submit document ingest jobs and query completed eidocs indexes without blocking OpenClaw.",
  configSchema: {
    type: "object",
    additionalProperties: false,
    properties: {}
  },
  register(api) {
    registerAutoIngest(api);
    if (api && typeof api.on === "function") {
      api.on("before_prompt_build", async () => {
        const context = buildPromptContext();
        return context ? { prependContext: context } : {};
      });
    }
    registerTool(api, {
      name: "eidocs_ingest",
      label: "EI Docs Ingest",
      description: "Submit a document ingest job. Returns quickly with a job id and does not parse synchronously.",
      parameters: pathParam,
      async execute(_toolCallId, params) {
        return documentIngest(params || {});
      }
    });
    registerTool(api, {
      name: "eidocs_status",
      label: "EI Docs Status",
      description: "Check an eidocs ingest job status.",
      parameters: statusParam,
      async execute(_toolCallId, params) {
        return documentStatus(params || {});
      }
    });
    registerTool(api, {
      name: "eidocs_recent",
      label: "EI Docs Recent",
      description: "List recent eidocs jobs, including background OpenClaw PDF auto-ingest state. Call before answering whether a recent PDF/document went through eidocs.",
      parameters: recentParam,
      async execute(_toolCallId, params) {
        return documentRecent(params || {});
      }
    });
    registerTool(api, {
      name: "eidocs_query",
      label: "EI Docs Query",
      description: "Query completed eidocs local indexes and return concise cited hits.",
      parameters: queryParam,
      async execute(_toolCallId, params) {
        return documentQuery(params || {});
      }
    });
    registerTool(api, {
      name: "eidocs_summarize",
      label: "EI Docs Summarize",
      description: "Summarize indexed document evidence for a question.",
      parameters: queryParam,
      async execute(_toolCallId, params) {
        return documentSummarize(params || {});
      }
    });
  }
};

module.exports.eidocs_ingest = documentIngest;
module.exports.eidocs_status = documentStatus;
module.exports.eidocs_recent = documentRecent;
module.exports.eidocs_query = documentQuery;
module.exports.eidocs_summarize = documentSummarize;
module.exports._extractPdfPaths = extractPdfPaths;
module.exports._autoIngestPdfs = autoIngestPdfs;
module.exports._buildPromptContext = buildPromptContext;
