import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTOCOL_VERSION = "2026-07-28"
TOOL_NAME = "query_workspace"


def load_env(path=".env"):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

ANYLLM_BASE_URL = os.environ.get("ANYLLM_BASE_URL", "http://localhost:3001").rstrip("/")
ANYLLM_API_KEY = os.environ.get("ANYLLM_API_KEY", "")
WORKSPACE_SLUG = os.environ.get("WORKSPACE_SLUG", "")
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "8080"))

SERVER_INFO = {"name": "anythingllm-mcp", "version": "0.1.0"}

TOOL = {
    "name": TOOL_NAME,
    "title": "Query AnythingLLM workspace",
    "description": "Ask a question and get an answer extracted from the configured AnythingLLM workspace (RAG over its documents).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to answer from the workspace content"}
        },
        "required": ["question"],
    },
}


def chat_workspace(question):
    body = json.dumps({"message": question, "mode": "query"}).encode()
    req = urllib.request.Request(
        f"{ANYLLM_BASE_URL}/api/v1/workspace/{WORKSPACE_SLUG}/chat",
        data=body,
        headers={
            "Authorization": f"Bearer {ANYLLM_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("textResponse") or data.get("response") or ""


class Handler(BaseHTTPRequestHandler):
    server_version = "anyllm-mcp"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, obj=None, raw=None):
        self.send_response(status)
        if raw is not None:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _send_202(self):
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._send(405, {"jsonrpc": "2.0", "error": {"code": -32601, "method": "GET", "message": "GET not supported"}})

    def do_DELETE(self):
        self._send(405, {"jsonrpc": "2.0", "error": {"code": -32601, "method": "DELETE", "message": "DELETE not supported"}})

    def do_POST(self):
        if self.path != "/mcp":
            self._send(404, {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Not found: {self.path}"}})
            return
        origin = self.headers.get("Origin")
        if origin:
            host = urllib.parse.urlsplit(origin).hostname
            if host not in ("localhost", "127.0.0.1"):
                self._send(403, {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Forbidden origin"}})
                return
        proto = self.headers.get("MCP-Protocol-Version")
        if proto != PROTOCOL_VERSION:
            self._send(400, {"jsonrpc": "2.0", "error": {
                "code": -32022, "message": "Unsupported protocol version",
                "data": {"supported": [PROTOCOL_VERSION], "requested": proto}}})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            msg = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send(400, {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}})
            return
        self._dispatch(msg)

    def _dispatch(self, msg):
        params = msg.get("params") or {}
        rid = msg.get("id")
        method = msg.get("method")

        header_method = self.headers.get("Mcp-Method")
        if header_method != method:
            self._send(400, {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32020, "message": f"Mcp-Method header mismatch: {header_method!r} != {method!r}"}})
            return

        meta = (params.get("_meta") or {}).get("io.modelcontextprotocol")
        if method in ("server/discover", "tools/list", "tools/call"):
            body_proto = (meta or {}).get("protocolVersion")
            if body_proto != PROTOCOL_VERSION:
                self._send(400, {"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32020, "message": "MCP-Protocol-Version header does not match body _meta"}})
                return

        if method == "server/discover":
            result = {
                "resultType": "complete",
                "supportedVersions": [PROTOCOL_VERSION],
                "capabilities": {"tools": {}},
                "instructions": "Ask questions to be answered from a single AnythingLLM workspace using the query_workspace tool.",
                "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO},
            }
            self._send(200, {"jsonrpc": "2.0", "id": rid, "result": result})
            return

        if method == "tools/list":
            self._send(200, {"jsonrpc": "2.0", "id": rid,
                             "result": {"resultType": "complete", "tools": [TOOL]}})
            return

        if method == "tools/call":
            self._tools_call(rid, params)
            return

        if method and method.startswith("notifications/"):
            self._send_202()
            return

        self._send(404, {"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -32601, "message": f"Method not found: {method}"}})

    def _tools_call(self, rid, params):
        name = params.get("name")
        mcp_name = self.headers.get("Mcp-Name")
        if mcp_name is not None and mcp_name != name:
            self._send(400, {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32020, "message": f"Mcp-Name header mismatch: {mcp_name!r} != {name!r}"}})
            return
        if name != TOOL_NAME:
            self._send(404, {"jsonrpc": "2.0", "id": rid,
                             "error": {"code": -32602, "message": f"Unknown tool: {name}"}})
            return
        question = (params.get("arguments") or {}).get("question")
        if not question:
            self._tool_error(rid, "Missing required argument: question")
            return
        try:
            text = chat_workspace(question)
        except Exception as exc:
            self._tool_error(rid, f"AnythingLLM error: {exc}")
            return
        self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
            "resultType": "complete",
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }})

    def _tool_error(self, rid, message):
        self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
            "resultType": "complete",
            "content": [{"type": "text", "text": message}],
            "isError": True,
        }})


def main():
    print(f"AnyLLM MCP server listening on http://{HOST}:{PORT}/mcp")
    print(f"Workspace: {WORKSPACE_SLUG or '(not set, set WORKSPACE_SLUG in .env)'}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()