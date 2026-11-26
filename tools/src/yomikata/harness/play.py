"""`yomikata play`: the harness's own tools run from a plan, without an agent at the other end, for playing
and exploring a game outside an MCP session (docs/harness.md).

Playing is playing wherever it runs: each call of a plan goes into scratch's record of calls as a line of its
own (`offline_play`, with the tool it was, its frames and seconds), so it costs what the same call costs in the
harness, and a run is one run. A plan is refused before anything runs when its calls don't fit in what is
left of the game's budget. A plan can't set a budget, and doesn't measure a script (`compare`): those are the
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


def run(session: Session, plan: list, out: Path | None = None, say=print) -> list[dict]:
    """Runs the plan's calls on the session in order, stopping at the first that fails. Gives the record's
    lines for the calls made."""
    if not plan or not isinstance(plan[0], (list, tuple)) or plan[0][0] != "load":
        raise HarnessError("a plan begins with a load: [\"load\", {\"game\": …, \"core\": …, \"platform\": …}]")
    for name, _ in plan:
        if name in NOT_IN_A_PLAN:
            raise HarnessError(f"{name} isn't for a plan: run it in the harness")
    game = plan[0][1].get("game")
    limit = session.budget_of(game)
    if limit is not None:
        used = session.calls_so_far(game)
        if used + len(plan) > limit:
            raise HarnessError(f"the plan's {len(plan)} calls don't fit in the budget for {game}: {used} calls in "
                               f"the record, {limit} allowed. Nothing was run")
    known = tools(session)
    lines = []
    for number, (name, arguments) in enumerate(plan):
        if name not in known:
            raise HarnessError(f"no such tool: {name}")
        session.begin_call()
        began = time.monotonic()
        answer = _call(known[name][2], arguments or {})
        ok, machine = not answer.get("isError"), session.machine
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session": session.session_id,
                 "game": session.game or game, "tool": TOOL, "arguments": {"tool": name, "call": number},
                 "ok": ok, "seconds": round(time.monotonic() - began, 2),
                 "outcome": {"frames": machine.frames if machine is not None else 0}}
        calls_record.append(session.roots.scratch, entry)
        lines.append(entry)
        for k, part in enumerate(answer["content"]):
            if part["type"] == "text":
                say(json.dumps({"call": number, "tool": name, "text": part["text"]}, ensure_ascii=False))
            elif part["type"] == "image" and out is not None:
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{number:03d}-{name}-{k}.png").write_bytes(base64.b64decode(part["data"]))
        if not ok:
            break
    return lines
