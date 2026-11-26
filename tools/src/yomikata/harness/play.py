"""`yomikata play`: the harness's own tools run from a plan, without an agent at the other end, for playing
and exploring a game outside an MCP session (docs/harness.md).

Playing is playing wherever it runs: a run goes into scratch's record of calls as a call of its own
(`offline_play`), with its frames and seconds, and counts against the game's budget like any call. A game whose
budget is spent isn't run. A plan can't set a budget, and doesn't measure a script (`compare`): those are the
harness's.

A plan is a list of [tool, arguments] as the harness takes them, the first a `load`. What the tools answer is
printed a JSON line each; their pictures go into the out folder, named by the call's place in the plan."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from . import record as calls_record
from .mcp import _call
from .session import HarnessError, Session, tools

TOOL = "offline_play"
NOT_IN_A_PLAN = ("budget", "compare")


def run(session: Session, plan: list, out: Path | None = None, say=print) -> dict:
    """Runs the plan's calls on the session in order, stopping at the first that fails. Gives what the record
    keeps of the run."""
    if not plan or not isinstance(plan[0], (list, tuple)) or plan[0][0] != "load":
        raise HarnessError("a plan begins with a load: [\"load\", {\"game\": …, \"core\": …, \"platform\": …}]")
    game = plan[0][1].get("game")
    refusal = session.over_budget(game)
    if refusal:
        raise HarnessError(refusal)
    known = tools(session)
    began, done, ok, last = time.monotonic(), [], True, None
    try:
        for number, (name, arguments) in enumerate(plan):
            if name in NOT_IN_A_PLAN:
                raise HarnessError(f"{name} isn't for a plan: run it in the harness")
            if name not in known:
                raise HarnessError(f"no such tool: {name}")
            session.begin_call()
            answer = _call(known[name][2], arguments or {})
            done.append(name)
            for k, part in enumerate(answer["content"]):
                if part["type"] == "text":
                    say(json.dumps({"call": number, "tool": name, "text": part["text"]}, ensure_ascii=False))
                elif part["type"] == "image" and out is not None:
                    out.mkdir(parents=True, exist_ok=True)
                    (out / f"{number:03d}-{name}-{k}.png").write_bytes(base64.b64decode(part["data"]))
            if answer.get("isError"):
                ok, last = False, name
                break
    finally:
        machine = session.machine
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session": session.session_id,
                 "game": session.game or game, "tool": TOOL,
                 "arguments": {"calls": len(done), "tools": ", ".join(sorted(set(done)))},
                 "ok": ok, "seconds": round(time.monotonic() - began, 2),
                 "outcome": {"frames": machine.frames if machine is not None else 0,
                             **({"failed_at": last} if last else {})}}
        calls_record.append(session.roots.scratch, entry)
    return entry
