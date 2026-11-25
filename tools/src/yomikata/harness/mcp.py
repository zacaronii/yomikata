"""The harness as an MCP server on standard input and output: one JSON-RPC message a line.

Only what a tool server needs of the protocol is here (initialize, tools/list, tools/call, ping), so that the
harness needs no network and no library beyond the tools' own."""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
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
    """A tool's result as MCP content. What goes wrong comes back as an error result, for the agent to read.
    Every call of a tool the harness has goes into scratch's record of calls."""
    known = tools(session)
    if name not in known:
        return {"content": [{"type": "text", "text": f"no such tool: {name}"}], "isError": True}
    session.begin_call()
    began = time.monotonic()
    answer = _call(known[name][2], arguments)
    session.record_call(name, arguments, not answer.get("isError"), time.monotonic() - began)
    return answer


def _call(tool, arguments: dict) -> dict:
    try:
        result = tool(**arguments)
    except HarnessError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except Exception as e:                      # whatever goes wrong in a tool is that call's, not the session's
        return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    picture = lambda png: {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}  # noqa: E731
    if isinstance(result, tuple) and result[0] == "image":
        return {"content": [picture(result[1])]}
    if isinstance(result, tuple) and result[0] == "both":
        return {"content": [{"type": "text", "text": json.dumps(result[1], ensure_ascii=False)}, picture(result[2])]}
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


GRACE = 60.0       # seconds past a call's own time before the harness gives it up


class Watch:
    """Gives up a call that never comes back. A call stops by itself when its time is up (session.py), but a
    core that hangs inside one frame can't be stopped from here: then the caller is told, and the harness
    ends. What was saved to scratch (states, snaps) stays."""

    def __init__(self, write: IO[str], lock: threading.Lock, seconds: float, leave=os._exit, tick: float = 1.0):
        self.write, self.lock, self.seconds, self.leave, self.tick = write, lock, seconds, leave, tick
        self.current: tuple | None = None       # the id of the call being answered, and when it started
        self.gave_up: list = []                 # the ids of calls given up: their late answers aren't sent
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            time.sleep(self.tick)
            current = self.current
            if current is not None and time.monotonic() - current[1] > self.seconds:
                text = (f"the game didn't come back within {round(self.seconds)} seconds, so the harness is "
                        "stopping. States and snaps already in scratch are kept: start it again and load a state.")
                answer = {"jsonrpc": "2.0", "id": current[0],
                          "result": {"content": [{"type": "text", "text": text}], "isError": True}}
                with self.lock:
                    self.gave_up.append(current[0])
                    self.write.write(json.dumps(answer, ensure_ascii=False) + "\n")
                    self.write.flush()
                self.leave(3)
                return


def serve(session: Session, read: IO[str] = sys.stdin, write: IO[str] = sys.stdout, give_up: float | None = None,
          leave=os._exit, tick: float = 1.0) -> None:
    """Answers each line. `give_up` is how long one call may take in all before the harness ends (default: the
    session's own limit and a grace); 0 turns that off."""
    lock = threading.Lock()
    seconds = session.call_seconds + GRACE if give_up is None else give_up
    watch = Watch(write, lock, seconds, leave, tick) if seconds > 0 else None
    for line in read:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            answer = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}}
        else:
            if watch is not None and isinstance(message, dict):
                watch.current = (message.get("id"), time.monotonic())
            answer = handle(session, message) if isinstance(message, dict) else None
            if watch is not None:
                watch.current = None
        if answer is not None:
            with lock:
                if watch is None or answer.get("id") not in watch.gave_up:
                    write.write(json.dumps(answer, ensure_ascii=False) + "\n")
                    write.flush()
    session.close()
