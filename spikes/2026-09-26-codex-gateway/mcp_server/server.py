"""Tiny host-side MCP server (official `mcp` SDK 2.x, streamable HTTP) for the spike.

Usage: .venv/bin/python server.py [bind_host] [port]
Token comes from env IHS_MCP_TOKEN (default matches run_agent.sh).
"""
import os, sys, time, logging
import uvicorn
from pydantic import BaseModel, Field
from mcp.server.mcpserver import MCPServer, Context
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.elicitation import render_elicitation_schema

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
TOKEN = os.environ.get("IHS_MCP_TOKEN", "spike-mcp-token-123")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("spike-mcp")

mcp = MCPServer(name="ihs-data", instructions="Spike IHS data service.")


@mcp.tool()
def echo_query(sql: str) -> dict:
    """Run a read-only SQL query against the (fake) IHS database and return a preview."""
    log.info("echo_query called: %r", sql[:200])
    return {"sql": sql, "columns": ["cohort", "n"], "rows": [["IHS-2024", 1234], ["IHS-2025", 987]],
            "row_count": 2, "note": "canned spike result"}


@mcp.tool()
async def slow_tool(seconds: float) -> dict:
    """Sleep for the given number of seconds, then return (timeout control test)."""
    import anyio
    log.info("slow_tool sleeping %.1fs", seconds)
    await anyio.sleep(seconds)
    log.info("slow_tool done")
    return {"slept": seconds}


class Approval(BaseModel):
    approve: bool = Field(description="Approve sending this question to the research helper?")
    edited_question: str = Field(default="", description="Optionally edit the question text")


@mcp.tool()
async def ask_helper(question: str, ctx: Context) -> dict:
    """Ask the research helper a question. Requires the user's approval first."""
    t0 = time.monotonic()
    log.info("ask_helper: eliciting approval for %r", question)
    # GOTCHA (Codex 0.157.1): Codex's typed elicitation-schema parser rejects a
    # top-level "title" (which pydantic/ctx.elicit always emits) and silently
    # auto-CANCELs. Send a hand-built schema with only type/properties/required.
    schema = render_elicitation_schema(Approval)
    schema = {k: v for k, v in schema.items() if k in ("$schema", "type", "properties", "required")}
    raw = await ctx.request_context.session.elicit_form(
        message=f"Approve research-helper question?\n\n{question}",
        requested_schema=schema, related_request_id=ctx.request_id)
    waited = time.monotonic() - t0
    data = Approval.model_validate(raw.content) if raw.action == "accept" and raw.content else None
    log.info("ask_helper: elicitation result action=%s data=%s after %.1fs", raw.action, data, waited)
    if data and data.approve:
        q = data.edited_question or question
        return {"status": "approved", "answer": f"(helper answer for: {q}) The answer is 42.",
                "approval_wait_seconds": round(waited, 1)}
    return {"status": "declined", "approval_wait_seconds": round(waited, 1)}


class BearerGate:
    """Pure-ASGI bearer check (BaseHTTPMiddleware would break streaming)."""

    def __init__(self, app, token):
        self.app, self.expected = app, f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope.get("headers") or []).get(b"authorization", b"")
            if auth != self.expected:
                log.warning("401 %s %s (auth header present=%s)", scope.get("method"), scope.get("path"), bool(auth))
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    host=HOST,
    # Host header arrives as whatever the gateway sets (we set 127.0.0.1:PORT).
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"127.0.0.1:{PORT}", f"localhost:{PORT}"],
        allowed_origins=[],
    ),
)

if __name__ == "__main__":
    uvicorn.run(BearerGate(app, TOKEN), host=HOST, port=PORT, log_level="info",
                timeout_keep_alive=3600)
