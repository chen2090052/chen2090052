import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080/mcp"
V = "2026-07-28"


def meta():
    return {"io.modelcontextprotocol": {"protocolVersion": V}}


def call(method, params, name=None, extra_headers=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    headers = {"Content-Type": "application/json", "MCP-Protocol-Version": V, "Mcp-Method": method}
    if name:
        headers["Mcp-Name"] = name
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


r = call("server/discover", {"_meta": meta()})
print("discover:", r[0])
print(json.dumps(json.loads(r[1]), indent=1, ensure_ascii=False)[:900])
print()

r = call("tools/list", {"_meta": meta()})
print("tools/list:", r[0], r[1][:500])
print()

r = call("tools/call", {"name": "query_workspace", "arguments": {"question": "test question"}, "_meta": meta()}, name="query_workspace")
print("tools/call:", r[0], r[1][:500])
print()

r = call("server/discover", {"_meta": meta()}, extra_headers={"MCP-Protocol-Version": "2025-11-25"})
print("bad version: expected 400")
print(r[0], r[1][:300])
print()

r = call("server/discover", {"_meta": meta()}, extra_headers={"Mcp-Method": "tools/list"})
print("mcp-method mismatch: expected 400")
print(r[0], r[1][:300])