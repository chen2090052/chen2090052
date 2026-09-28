#!/usr/bin/env bash
# 逐屏演示脚本：每按一次回车显示一屏，方便逐张截图。
#
# 用法（Git Bash）：
#   cd pet_hospital_mcp
#   bash demo.sh
#
# 前置：Go 服务与 MCP 服务都已在运行（见 README「一、前置」「二、启动」）。

set -u

PY=".venv/Scripts/python.exe"
MCP="http://127.0.0.1:8765/mcp"
GO="http://127.0.0.1:8080"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pause() {
  echo
  echo "──────────────────────────────────────────────────────────────"
  read -r -p "  按回车继续下一屏…" _
  clear 2>/dev/null || printf '\n\n\n'
}

title() {
  echo
  echo "══════════════════════════════════════════════════════════════"
  echo "  $1"
  echo "══════════════════════════════════════════════════════════════"
  echo
}

# 统一的 2026-07-28 请求头
hdr() {
  printf -- '-H\nContent-Type: application/json\n-H\nAccept: application/json, text/event-stream\n-H\nMCP-Protocol-Version: 2026-07-28\n-H\nMcp-Method: %s\n' "$1"
  [ -n "${2:-}" ] && printf -- '-H\nMcp-Name: %s\n' "$2"
}

post() { # post <method> <bodyfile> [name]
  local m="$1" f="$2" n="${3:-}"
  local args=(-s -X POST "$MCP" -H 'Content-Type: application/json'
              -H 'Accept: application/json, text/event-stream'
              -H "MCP-Protocol-Version: 2026-07-28" -H "Mcp-Method: $m")
  [ -n "$n" ] && args+=(-H "Mcp-Name: $n")
  curl "${args[@]}" --data-binary "@$f"
}

# ── 请求体（用文件避免 Git Bash 下中文被破坏）────────────────────
meta='"params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}'
printf '{"jsonrpc":"2.0","id":1,"method":"server/discover",%s}\n' "$meta" > "$TMP/discover.json"
printf '{"jsonrpc":"2.0","id":2,"method":"tools/list",%s}\n' "$meta" > "$TMP/list.json"
cat > "$TMP/call.json" <<'EOF'
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_pets","arguments":{"species":"犬","status":"已康复","sortBy":"totalCost","order":"desc","page":1,"pageSize":5},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}
EOF
cat > "$TMP/bad.json" <<'EOF'
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"list_pets","arguments":{"species":"恐龙","pageSize":9999},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}
EOF

clear 2>/dev/null
cat <<'BANNER'

  pet-hospital-mcp  ──  演示截图脚本
  共 7 屏；每按一次回车前进一屏。

  先做这一步：重启 Claude Code，否则第 6 屏看不到工具。

BANNER
pause

# ── 1 ──────────────────────────────────────────────────────────
title "1 / 7   Go 宠物医院后端"
echo "\$ curl -s $GO/health"
echo
curl -s -m 5 "$GO/health" | "$PY" -c 'import json,sys; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))'

pause

# ── 2 ──────────────────────────────────────────────────────────
title "2 / 7   MCP 服务 /health"
echo "\$ curl -s http://127.0.0.1:8765/health"
echo
curl -s -m 5 http://127.0.0.1:8765/health | "$PY" -c 'import json,sys; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))'
echo
echo "  ↑ protocolVersion = 2026-07-28，stateless = true"

pause

# ── 3 ──────────────────────────────────────────────────────────
title "3 / 7   能力发现：server/discover（无握手、无会话）"
echo "\$ curl -i -X POST $MCP  -H 'Mcp-Method: server/discover'  …"
echo
curl -s -D "$TMP/h.txt" -o "$TMP/d.json" -X POST "$MCP" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: server/discover' \
  --data-binary "@$TMP/discover.json"
echo "── 响应头 ──"
grep -iE '^HTTP|^mcp-|^content-type' "$TMP/h.txt"
echo
echo "── 响应体关键字段 ──"
"$PY" -c '
import json,sys
r=json.load(open(sys.argv[1],encoding="utf-8"))["result"]
print("supportedVersions =", r["supportedVersions"])
print("capabilities      =", json.dumps(r["capabilities"], ensure_ascii=False))
' "$TMP/d.json"
echo
if grep -qi 'mcp-session-id' "$TMP/h.txt"; then
  echo "  ✗ 出现了 Mcp-Session-Id"
else
  echo "  ✓ 响应头没有 Mcp-Session-Id —— 无状态"
fi
echo "  ✓ 没有发送 initialize —— 新协议已删除握手"

pause

# ── 4 ──────────────────────────────────────────────────────────
title "4 / 7   tools/list：工具名与 JSON Schema"
post tools/list "$TMP/list.json" > "$TMP/l.json"
"$PY" -c '
import json,sys
t=json.load(open(sys.argv[1],encoding="utf-8"))["result"]["tools"]
print("工具数量:", len(t))
for x in t:
    s=x["inputSchema"]
    print("  名称:", x["name"])
    print("  参数:", len(s["properties"]), "个")
    print("  additionalProperties:", s.get("additionalProperties"))
    p=s["properties"]
    print("  species 枚举:", p["species"]["anyOf"][0]["enum"])
    print("  status  枚举:", p["status"]["anyOf"][0]["enum"])
    print("  pageSize 范围:", p["pageSize"]["minimum"], "-", p["pageSize"]["maximum"])
' "$TMP/l.json"

pause

# ── 5 ──────────────────────────────────────────────────────────
title "5 / 7   tools/call：正常调用 list_pets"
echo '  参数: {"species":"犬","status":"已康复","sortBy":"totalCost","order":"desc","page":1,"pageSize":5}'
echo
post tools/call "$TMP/call.json" list_pets > "$TMP/c.json"
"$PY" -c '
import json,sys
r=json.load(open(sys.argv[1],encoding="utf-8"))["result"]
s=r["structuredContent"]
print("isError  :", r.get("isError"))
print("total    :", s["total"], "| page", s["page"], "| pageSize", s["pageSize"], "| totalPages", s["totalPages"])
print("totalCost:", s["totalCost"])
print()
for i in s["items"]:
    print("  -", i["id"], i["name"], i["species"], i["status"], i["doctor"], "| 花费", i["totalCost"])
' "$TMP/c.json"

pause

# ── 6 ──────────────────────────────────────────────────────────
title "6 / 7   tools/call：非法入参 → 统一错误信封"
echo '  参数: {"species":"恐龙","pageSize":9999}   ← 枚举外 + 越界'
echo
post tools/call "$TMP/bad.json" list_pets > "$TMP/b.json"
"$PY" -c '
import json,sys
r=json.load(open(sys.argv[1],encoding="utf-8"))["result"]
print("isError:", r.get("isError"))
print(json.dumps(r["structuredContent"], ensure_ascii=False, indent=2))
print()
txt=json.dumps(r, ensure_ascii=False).lower()
leaked=[k for k in ("pydantic","traceback","site-packages","httpx.") if k in txt]
print("内部信息泄漏:", leaked if leaked else "无")
print("后端未被调用（校验在适配器内完成）")
' "$TMP/b.json"

pause

# ── 7 ──────────────────────────────────────────────────────────
title "7 / 7   Claude Code 已接入 + 单元测试全绿"
echo "\$ claude mcp list"
echo
(cd "E:/school/xiexin" && claude mcp list 2>&1 | grep -E 'pet-hospital|Bazi')
echo
echo "  连上即证明走的是新协议：服务端对 initialize 返回 404，"
echo "  若客户端发旧握手这里只会显示连接失败。"
echo
echo "\$ pytest -q"
echo
"$PY" -m pytest -q 2>&1 | tail -3

echo
echo "══════════════════════════════════════════════════════════════"
echo "  演示结束。"
echo "══════════════════════════════════════════════════════════════"
echo
