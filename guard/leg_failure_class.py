"""Classify a leg failure: TERMINAL vs RETRYABLE."""
import argparse
import sys
from dataclasses import dataclass
from typing import Optional

TERMINAL = "TERMINAL"
RETRYABLE = "RETRYABLE"
OK = "OK"


@dataclass(frozen=True)
class Verdict:
    outcome: str     # TERMINAL | RETRYABLE | OK
    reason: str      # human-readable, names the evidence that decided it
    signature: str   # short machine key, e.g. "quota-cap", "timeout", "short-artifact"


def classify(*, rc: int, stdout_bytes: int, stderr_bytes: int,
            artifact_bytes: int, elapsed_s: float, timeout_s: float,
            min_artifact_bytes: int = 6000) -> Verdict:
    # Rule 1: OK - artifact_bytes >= min_artifact_bytes
    if artifact_bytes >= min_artifact_bytes:
        return Verdict(outcome=OK, reason=f"artifact_bytes={artifact_bytes} >= {min_artifact_bytes}", signature="ok")

    # Rule 2: TERMINAL / "quota-cap"
    if (rc != 0 and stdout_bytes == 0 and stderr_bytes == 0 and elapsed_s < 60.0):
        return Verdict(
            outcome=TERMINAL,
            reason=f"rc={rc}, no stdout, no stderr, failed in {elapsed_s}s — the quota-cap signature; retrying cannot help",
            signature="quota-cap"
        )

    # Rule 3: TERMINAL / "timeout"
    if elapsed_s >= timeout_s * 0.95:
        return Verdict(
            outcome=TERMINAL,
            reason=f"elapsed_s={elapsed_s}s >= timeout_s*0.95 = {timeout_s*0.95}s — exhausted the time window; retrying cannot help",
            signature="timeout"
        )

    # Rule 4: TERMINAL / "no-such-command"
    if rc == 127:
        return Verdict(
            outcome=TERMINAL,
            reason=f"rc={rc} — no such command; this is a missing binary error that never fixes itself",
            signature="no-such-command"
        )

    # Rule 5: RETRYABLE / "short-artifact"
    if 0 < artifact_bytes < min_artifact_bytes:
        return Verdict(
            outcome=RETRYABLE,
            reason=f"artifact_bytes={artifact_bytes} < {min_artifact_bytes} — partial write; retry may help",
            signature="short-artifact"
        )

    # Rule 6: RETRYABLE / "empty-with-output"
    if artifact_bytes == 0 and (stdout_bytes > 0 or stderr_bytes > 0):
        return Verdict(
            outcome=RETRYABLE,
            reason=f"no artifact, but stdout={stdout_bytes} bytes and stderr={stderr_bytes} bytes — leg ran and said something; retry may help",
            signature="empty-with-output"
        )

    # Rule 7: RETRYABLE / "unclassified"
    return Verdict(
        outcome=RETRYABLE,
        reason=f"unclassified state: rc={rc}, stdout={stdout_bytes}, stderr={stderr_bytes}, artifact={artifact_bytes}, elapsed={elapsed_s}s, timeout={timeout_s}s — defaulting to retryable",
        signature="unclassified"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rc", type=int, required=True)
    parser.add_argument("--stdout-bytes", type=int, required=True)
    parser.add_argument("--stderr-bytes", type=int, required=True)
    parser.add_argument("--artifact-bytes", type=int, required=True)
    parser.add_argument("--elapsed", type=float, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--min-artifact-bytes", type=int, default=6000)

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print("Error: invalid arguments")
        return 1

    verdict = classify(
        rc=args.rc,
        stdout_bytes=args.stdout_bytes,
        stderr_bytes=args.stderr_bytes,
        artifact_bytes=args.artifact_bytes,
        elapsed_s=args.elapsed,
        timeout_s=args.timeout,
        min_artifact_bytes=args.min_artifact_bytes
    )

    print(f"{verdict.outcome} {verdict.signature} {verdict.reason}")
    return 0 if verdict.outcome == OK else (1 if verdict.outcome == RETRYABLE else 2)


if __name__ == "__main__":
    sys.exit(main())
