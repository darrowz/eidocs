const { spawnSync } = require("child_process");

const EIDOCS = process.env.EIDOCS_CLI || "/home/darrow/.local/bin/eidocs";
const ROOT = process.env.EIDOCS_ROOT || "/home/darrow/.local/share/eidocs";

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
  return run(["job", "submit", path, "--source", "openclaw", "--collection", collection], 2000);
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

function registerTool(api, spec) {
  if (!api || typeof api.registerTool !== "function") return false;
  api.registerTool(spec);
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
module.exports.eidocs_query = documentQuery;
module.exports.eidocs_summarize = documentSummarize;
