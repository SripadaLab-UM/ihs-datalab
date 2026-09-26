// Request-shape allowlist for POST /v1/responses (data sessions).
// Only what Codex itself sends is allowed; hosted tools / remote fetches / server-side state are refused.
// Written for the classic njs engine (no for..of, no Set).
function set(a) { const o = {}; for (let i = 0; i < a.length; i++) { o[a[i]] = true; } return o; }
const ALLOWED_TOP = set(["model", "instructions", "input", "tools", "tool_choice", "parallel_tool_calls",
  "reasoning", "store", "stream", "include", "prompt_cache_key", "text", "client_metadata", "service_tier",
  "max_output_tokens", "temperature", "top_p", "truncation"]);
const ALLOWED_TOOL_TYPES = set(["function", "custom"]);
const ALLOWED_INCLUDE = set(["reasoning.encrypted_content"]);

function bad(r, why) {
  r.error("responses filter rejected: " + why);
  r.headersOut["Content-Type"] = "application/json";
  r.return(403, JSON.stringify({ error: { message: "DataLab gateway: request refused (" + why + ")",
    type: "datalab_gateway_refused" } }));
}

function checkContent(parts, path) {
  if (!Array.isArray(parts)) return null;
  for (let i = 0; i < parts.length; i++) {
    const c = parts[i] || {};
    if (c.type === "input_image") {
      const u = c.image_url || "";
      if (c.file_id) return path + ": input_image.file_id";
      if (typeof u === "string" && !u.startsWith("data:")) return path + ": input_image with non-data URL";
    }
    if (c.type === "input_file") {
      if (c.file_url || c.file_id) return path + ": input_file url/file_id";
    }
  }
  return null;
}

function check(r) {
  let body;
  try { body = JSON.parse(r.requestText); } catch (e) { return bad(r, "body not JSON or too large"); }
  const keys = Object.keys(body);
  for (let i = 0; i < keys.length; i++) { if (!ALLOWED_TOP[keys[i]]) return bad(r, "field '" + keys[i] + "' not allowed"); }
  if (body.store !== false) return bad(r, "store must be false");
  const inc = body.include || [];
  for (let i = 0; i < inc.length; i++) { if (!ALLOWED_INCLUDE[inc[i]]) return bad(r, "include '" + inc[i] + "'"); }
  const tools = body.tools || [];
  for (let i = 0; i < tools.length; i++) {
    const t = tools[i];
    const ty = t && t.type;
    if (ALLOWED_TOOL_TYPES[ty]) continue;
    if (ty === "tool_search" && t.execution === "client") continue;   // Codex's client-side deferred tool search
    return bad(r, "tool type '" + ty + "' not allowed");
  }
  const tc = body.tool_choice;
  if (tc && typeof tc === "object" && !ALLOWED_TOOL_TYPES[tc.type] && tc.type !== "allowed_tools")
    return bad(r, "tool_choice type");
  const input = Array.isArray(body.input) ? body.input : [];
  for (let i = 0; i < input.length; i++) {
    const it = input[i] || {};
    if (it.type === "item_reference") return bad(r, "item_reference");
    const why = checkContent(it.content, "input[" + i + "]") || checkContent(it.output, "input[" + i + "].output");
    if (why) return bad(r, why);
  }
  r.internalRedirect("@umgpt_responses");
}

export default { check };
