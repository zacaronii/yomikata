"""The harness as an MCP server on standard input and output: one JSON-RPC message a line.

Only what a tool server needs of the protocol is here (initialize, tools/list, tools/call, ping), so that the
harness needs no network and no library beyond the tools' own."""

from __future__ import annotations

import base64
import json
import sys
from typing import IO

from .session import HarnessError, Session, tools

PROTOCOL = "2024-11-05"


def describe(session: Session) -> list[dict]:
    out = []
    for name, (description, properties, _) in tools(session).items():
        schema = {"type": "object", "properties": {k: {"type": t, "description": d} for k, (t, d, _) in properties.items()},
                  "required": [k for k, (_, _, needed) in properties.items() if needed]}
        out.append({"name": name, "description": description, "inputSchema": schema})
    return out


def call(session: Session, name: str, arguments: dict) -> dict:
    """A tool's result as MCP content. What goes wrong comes back as an error result, for the agent to read."""
    known = tools(session)
    if name not in known:
        return {"content": [{"type": "text", "text": f"no such tool: {name}"}], "isError": True}
    try:
        result = known[name][2](**arguments)
    except HarnessError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except (TypeError, ValueError, OSError, RuntimeError) as e:
        return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    if isinstance(result, tuple) and result[0] == "image":
        return {"content": [{"type": "image", "data": base64.b64encode(result[1]).decode(), "mimeType": "image/png"}]}
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}


def handle(session: Session, message: dict) -> dict | None:
    """The answer to one message, or None for a notification."""
    method, ident = message.get("method"), message.get("id")
    if ident is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "yomikata-harness", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": describe(session)}
    elif method == "tools/call":
        params = message.get("params") or {}
        result = call(session, params.get("name", ""), params.get("arguments") or {})
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"no such method: {method}"}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def serve(session: Session, read: IO[str] = sys.stdin, write: IO[str] = sys.stdout) -> None:
    for line in read:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            answer = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}}
        else:
            answer = handle(session, message) if isinstance(message, dict) else None
        if answer is not None:
            write.write(json.dumps(answer, ensure_ascii=False) + "\n")
            write.flush()
    session.close()
