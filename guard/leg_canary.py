"""Daily, artifact-based reachability checks for fleet legs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Callable


# ⚠ The accepted token must NOT appear in the prompt (fix 2026-08-03). It used to: the prompt said
# "reply with exactly CANARY-OK" and the checker accepted "CANARY-OK", so a leg that merely ECHOED its
# prompt back — or any wrapper that emitted the prompt on an error path — passed as ALIVE. The leg must now
# produce something it cannot copy: a trivial computation whose ANSWER is absent from the text it was sent.
CANARY_TOKEN = "CANARY-42"
CANARY_PROMPT = ("Reply with exactly one token and nothing else: the word CANARY, then a hyphen, then the "
                 "result of multiplying six by seven.")


@dataclass(frozen=True)
class Leg:
    name: str
    argv: list
    timeout: int = 120


@dataclass(frozen=True)
class Probe:
    leg: str
    outcome: str
    evidence: str
    rc: object


LEGS = [
    Leg("kimi", ["kimi-cli"]),
    Leg("grok", ["grok-dispatch.sh"]),
    Leg("codex", ["codex-luna"]),
    Leg("gemini36", ["agy-flash"]),
]


Runner = Callable[[list, str, int], tuple[int, str]]


def _default_runner(argv: list, prompt: str, timeout: int) -> tuple[int, str]:
    """Run a real leg and return its exit status and response artifact."""
    def run(command: list):
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )

    if not argv:
        raise ValueError("empty command")
    command = argv[0]
    if command == "kimi-cli":
        result = run([*argv, prompt])
        return result.returncode, result.stdout

    if command in {"grok-dispatch.sh", "codex-luna", "agy-flash"}:
        with tempfile.TemporaryDirectory(
            prefix="leg_canary_", dir="."
        ) as directory:
            brief = Path(directory) / "brief.md"
            response = Path(directory) / "response.txt"
            brief.write_text(prompt, encoding="utf-8")
            result = run([*argv, str(brief), str(response)])
            text = response.read_text(encoding="utf-8") if response.exists() else result.stdout
            return result.returncode, text

    result = run([*argv, prompt])
    return result.returncode, result.stdout


def _has_token(text: object) -> bool:
    if not isinstance(text, str):
        return False
    return "".join(CANARY_TOKEN.lower().split()) in "".join(text.lower().split())


def probe(leg: Leg, *, runner: Runner | None = None) -> Probe:
    """Measure one leg; operational failures become UNMEASURED, never exceptions."""
    try:
        chosen_runner = _default_runner if runner is None else runner
        result = chosen_runner(leg.argv, CANARY_PROMPT, leg.timeout)
        rc, text = result
    except Exception as exc:
        return Probe(leg.name, "UNMEASURED", str(exc)[:200], None)

    if _has_token(text):
        return Probe(leg.name, "ALIVE", str(text).strip()[:200], rc)
    evidence = str(text).strip()[:200] or "<empty response>"
    return Probe(leg.name, "DEAD", evidence, rc)


def probe_all(legs=None, *, runner: Runner | None = None) -> list:
    return [probe(leg, runner=runner) for leg in (LEGS if legs is None else legs)]


def load_state(path) -> dict:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_state(path, state) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def stale(state, legs, *, now, max_age_hours=26) -> list:
    stale_legs = []
    for leg in legs:
        try:
            last_alive = state[leg.name]["last_alive_seq"]
        except (KeyError, TypeError):
            stale_legs.append(leg.name)
            continue
        if not isinstance(last_alive, int) or now - last_alive > max_age_hours:
            stale_legs.append(leg.name)
    return sorted(stale_legs)


def _dry_runner(argv: list, prompt: str, timeout: int) -> tuple[int, str]:
    return 0, CANARY_TOKEN


def main(argv=None) -> int:
    # Cron's default PATH lacks ~/.local/bin, where every fleet leg binary lives — the canary's
    # first 5 days logged only UNMEASURED [Errno 2] (cron-PATH class, second instance).
    os.environ["PATH"] = "~/.local/bin:" + os.environ.get("PATH", "")
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="leg_canary_state.json")
    parser.add_argument("--legs")
    parser.add_argument("--max-age-hours", type=int, default=26)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    selected = LEGS
    if args.legs:
        wanted = {name.strip() for name in args.legs.split(",") if name.strip()}
        selected = [leg for leg in LEGS if leg.name in wanted]
    now = int(time.time() // 3600)
    state = load_state(args.state)
    runner = _dry_runner if args.dry_run else None
    if args.dry_run:
        print("DRY RUN: built-in fake runner; no cloud calls made")
    results = probe_all(selected, runner=runner)

    for result in results:
        print(f"{result.outcome:<10} {result.leg}  rc={result.rc}  {result.evidence}")
        if result.outcome == "ALIVE":
            state[result.leg] = {"last_alive_seq": now}
    # ⚠ NEVER persist a dry run (fix 2026-08-03). `_dry_runner` returns a hardcoded success, so writing
    # state here stamped every leg 'freshly alive' on fabricated evidence — and because run_guards.sh
    # invokes this with --dry-run by DEFAULT, the staleness check could never fire. The instrument was
    # manufacturing the positive result that suppressed its own alarm.
    if args.dry_run:
        print("DRY RUN: state NOT written (a fake probe must never count as evidence of liveness)")
    else:
        save_state(args.state, state)

    if args.dry_run:
        # Wiring proved, nothing measured. 2 = UNMEASURED, and 2 DOMINATES 1 in this subsystem.
        print("DRY RUN: wiring proved; NO leg was probed -> UNMEASURED")
        return 2

    stale_legs = stale(state, selected, now=now, max_age_hours=args.max_age_hours)
    for name in stale_legs:
        last_alive = state.get(name, {}).get("last_alive_seq")
        age = "never" if last_alive is None else f"{now - last_alive}h ago"
        print(f"STALE: {name} (last alive {age})")

    if any(result.outcome == "UNMEASURED" for result in results):
        return 2
    if any(result.outcome == "DEAD" for result in results) or stale_legs:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
