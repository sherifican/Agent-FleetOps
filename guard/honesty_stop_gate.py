#!/usr/bin/env python3
"""honesty_stop_gate — a Stop hook that refuses to end a turn which asserts live
state the turn never measured.

WHY A HOOK AND NOT A MEMORY RULE. The rule ("never assert without checking") can
already exist in your agent's instructions and still be violated, because the failure
is not a knowledge gap — it is reporting an INTENTION as an OBSERVATION, which feels
identical from the inside. "I started the job" silently becomes "the job is running."
Advisory text cannot catch that; a check that runs regardless of what the agent
believes can. This gate reads the turn's own transcript and blocks the turn from
ending when a live-state claim in it has no same-turn, same-subject verifying command.

WHAT IT DOES AND DOES NOT PROVE. It enforces that a probe which OBSERVES the claimed
subject's state was RUN this turn and NAMED that subject. It does NOT read the probe's
output — a lightweight Stop hook cannot adjudicate whether `pgrep` returned a live pid.
Restating the fact honestly from what the probe returned remains the agent's job; this
gate removes the case where no probe was run at all, which is the common failure.

WHY CONDITIONAL. It inspects ONLY the current turn (since the last human message) and
stays silent unless a claim lacks a same-turn measurement. A gate that fires every turn
carries the same zero information as one that never fires.

WHAT IS MECHANISM VS CONFIG. Everything here is the general mechanism. The three things
specific to YOUR stack live in CONFIG (a JSON file, schema in honesty_gate.config.example.json):
  - claim_patterns       : how a live-state claim reads in your domain
  - verification_commands: which shell commands actually OBSERVE that state on your box
  - subjects             : the named things whose state you assert (jobs, services, …)
Adapt those three, not the mechanism. Validate a config with `--check-config`: it flags
any verification command whose binary does not resolve on this box (a stair to nowhere)
and any empty required list. See specs/honesty-stop-gate.md and skills/honesty-stop-gate.

TEETH. `--self-test` plants claims that MUST block (unbacked running claim; a claim about
subject B backed only by a probe of subject A; a subjectless "both are still running"
backed only by an unrelated probe) and claims that MUST pass (backed claim; in-turn
completion prose; a quoted claim). A guard that cannot be shown to fail is not a guard.

Never blocks twice: `stop_hook_active` short-circuits so a genuine disagreement cannot
trap the turn in a loop. A broken or degenerate config falls back to the built-in
defaults rather than silently disabling the gate.

GUARD-CLASS: guard — a turn asserting unmeasured live state must be blocked
"""
import json, os, re, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Keys whose empty value would DISABLE the gate (empty alternation → match-everything or
# match-nothing). An empty one is ignored in favour of the default, never applied.
REQUIRED_LISTS = ("claim_patterns", "verification_commands", "subjects")

DEFAULT_CONFIG = {
    "claim_patterns": [
        r"still running", r"is running", r"are running", r"currently running",
        r"running in the background", r"in flight", r"now running",
        r"still (?:in progress|pending|working)",
        r"has(?:n'?t| not) (?:been )?started", r"not yet started", r"never started",
        r"(?:has|have|is|are|was|were)\s+(?:completed|finished|(?:complete|done)(?=\s*(?:[.;!?—–·•\n]|$)))",
        r"(?:job|task|build|deploy|service|process)s?\s+(?:are|is)\s+(?:live|running|up)",
    ],
    # Matches ONLY completion claims (linking verb + done/finished, clause-final for the
    # adjective-shaped words so "is complete garbage" / "is done wrong" do not match).
    "completion_pattern":
        r"(?:has|have|is|are|was|were)\s+(?:completed|finished|(?:complete|done)(?=\s*(?:[.;!?—–·•\n]|$)))",
    # Commands that ACTUALLY OBSERVE live process/service state. A claim is verified only
    # if the turn ran one AND it names the claim's subject. Anchored at command start
    # (after VAR=val assignments). File-metadata reads (ls/stat) and log reads are NOT
    # here: they observe existence or content, not liveness — an adopter adds those
    # deliberately, knowing what they do and do not prove.
    "verification_commands": [
        r"pgrep\b", r"ps\s+aux\b", r"ps\s+-ef\b",
        r"systemctl\s+status\b", r"systemctl\s+is-active\b",
        r"docker\s+ps\b", r"kubectl\s+get\b", r"jobs(?:\s|$)",
        r"curl\b[^\n]*(?:/health|/status)\b",
    ],
    "subjects": [
        r"job[\w-]*", r"task[\w-]*", r"build[\w-]*", r"deploy[\w-]*",
        r"service[\w-]*", r"worker[\w-]*", r"process[\w-]*", r"container[\w-]*",
    ],
    "non_subjects": ["the", "a", "an", "both", "it", "nothing", "none", "neither",
                     "everything", "this", "that", "they", "these", "those"],
    "verify_hint": "the command that observes this subject's real state "
                   "(e.g. `pgrep -af <name>`, `systemctl status <svc>`); a log or artifact read is "
                   "NOT credited by default — add it to verification_commands first if you want it to count",
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    path = os.environ.get("HONESTY_GATE_CONFIG") or os.path.join(HERE, "honesty_gate.config.json")
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf8") as fh:
                override = json.load(fh)
            if isinstance(override, dict):
                for k, v in override.items():
                    if k not in DEFAULT_CONFIG:
                        continue
                    # An empty required list disables the gate — ignore it and keep
                    # the default rather than compile a match-everything/nothing regex.
                    if k in REQUIRED_LISTS and (not isinstance(v, list) or not v):
                        continue
                    cfg[k] = v
    except Exception:
        pass  # a broken override must not disable the gate
    return cfg


def compile_config(cfg):
    """Compile the regexes, falling back to defaults for any key whose patterns are
    invalid — a valid-JSON-but-bad-regex override must not crash the hook to exit 1
    (which, for a Stop hook, silently lets the turn end)."""
    def try_compile(build, *keys):
        try:
            return build(cfg)
        except re.error:
            fb = dict(cfg)
            for k in keys:
                fb[k] = DEFAULT_CONFIG[k]
            return build(fb)

    claim = try_compile(lambda c: re.compile(r"\b(?:" + "|".join(c["claim_patterns"]) + r")\b", re.I),
                        "claim_patterns")
    completion = try_compile(lambda c: re.compile(c["completion_pattern"], re.I),
                             "completion_pattern")
    measurement = try_compile(
        lambda c: re.compile(r"^\s*(?:[A-Za-z_]\w*=\S+\s+)*(?:/\S*/)?(?:" +
                             "|".join(c["verification_commands"]) + r")", re.I),
        "verification_commands")
    subj = try_compile(lambda c: re.compile(r"\b(" + "|".join(c["subjects"]) + r")\b", re.I),
                       "subjects")
    return claim, completion, measurement, subj, set(w.lower() for w in cfg["non_subjects"])


def strip_quoted(text):
    """Remove QUOTED material before scanning. A quotation is not an assertion — without
    this, writing ABOUT the gate (quoting its alert, discussing a test case) trips it.
    Strips fenced code, inline code, blockquotes, and double-quoted fragments (bounded by
    a newline, so a long quotation is still fully removed)."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r"(?m)^\s*>.*$", " ", text)
    text = re.sub(r"[“\"][^”\"\n]*[”\"]", " ", text)
    return text


def subjects(t, subj_re):
    out = {m.group(1).lower().split("-")[0] for m in subj_re.finditer(t)}
    # A log/artifact FILENAME names its subject too: `tail worker3_run.log` verifies the
    # worker, but \bworker\b cannot match inside "worker3_run" (underscore is a word char).
    for m in re.finditer(r"([\w.-]+)\.(?:log|json|txt|out)\b", t):
        out.add(m.group(1).lower().split("_")[0].split("-")[0])
    return out


def claim_subjects(text, subj_re, non_subjects):
    """Subjects named in a claim clause. An unknown noun in a RUNNING-type claim is an
    uncovered subject (the danger: "widgetsvc is still running" must not inherit an
    unrelated check). Completion-type prose is not widened this way."""
    out = subjects(text, subj_re)
    for m in re.finditer(
            r"\b([A-Za-z][\w.-]*?)\.?\s+(?:job\s+|task\s+|service\s+)?"
            r"(?:is|are|has|have)\s+(?:still\s+)?(?:running|working|in progress|pending)\b",
            text, re.I):
        candidate = m.group(1).lower().split("-")[0]
        if candidate not in non_subjects:
            out.add(candidate)
    return out


def claim_clause(txt, start, end, subj_re, non_subjects):
    """The CLAUSE containing a claim, not a fixed character window. A window binds
    subjects from ADJACENT sentences; a clause boundary (sentence end, semicolon,
    em-dash aside, bullet, newline) is where a claim's subject stops."""
    seps = re.compile(r"(?<=[.;!?])\s+|\s+[—–]\s+|\n+|\s+[·•]\s+")
    left = 0
    for sm in seps.finditer(txt, 0, start):
        left = sm.end()
    rm = seps.search(txt, end)
    right = rm.start() if rm else len(txt)
    clause = txt[left:right]
    if not claim_subjects(clause, subj_re, non_subjects):
        nm = seps.search(txt, right + 1) if right < len(txt) else None
        clause = txt[left:(nm.start() if nm else len(txt))]
    return clause


_FILTER_HEAD = re.compile(r"^\s*(?:/\S*/)?(?:grep|egrep|fgrep|rgrep|rg|awk|gawk|sed)\b")
_NON_OBSERVING = {"--help", "--version", "-V"}


def split_shell(cmd, statements=True):
    """Split a shell command on UNQUOTED operators. Total: never raises.

    statements=True  cuts at the list operators  ;  &  &&  ||  newline
    statements=False cuts at the pipeline operators  |  |&
    Single and double quotes, backslash escapes and # comments are tracked, so a
    separator inside quotes does not split and a comment cannot name a subject.
    `2>&1`, `>&2`, `&>f`, `<&0` are redirections, not list operators.
    NOT modelled: heredoc bodies, $(...) and (...) subshells — a separator inside
    them still splits. That can only LOSE a credit (false-block), never grant one.
    An unclosed quote runs to the end of the string; nothing is raised."""
    parts, buf = [], []
    quote = None
    word_start = True
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if quote == "'":
            buf.append(ch)
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if ch == "\\" and i + 1 < n:
                buf.append(ch); buf.append(cmd[i + 1]); i += 2
                continue
            buf.append(ch)
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch == "\\":
            buf.append(ch)
            if i + 1 < n:
                buf.append(cmd[i + 1])
            i += 2
            word_start = False
            continue
        if ch in "'\"":
            quote = ch; buf.append(ch); i += 1; word_start = False
            continue
        if ch == "#" and word_start:
            j = cmd.find("\n", i)
            i = n if j < 0 else j
            continue
        two = cmd[i:i + 2]
        if statements:
            if two in ("&&", "||"):
                parts.append("".join(buf)); buf = []; i += 2; word_start = True
                continue
            if ch in ";\n":
                parts.append("".join(buf)); buf = []; i += 1; word_start = True
                continue
            if ch == "&":
                prev = cmd[i - 1] if i else ""
                nxt = cmd[i + 1] if i + 1 < n else ""
                if prev in "<>" or nxt == ">":
                    buf.append(ch); i += 1; word_start = False
                    continue
                parts.append("".join(buf)); buf = []; i += 1; word_start = True
                continue
        else:
            if two == "||":
                buf.append(two); i += 2; word_start = False
                continue
            if two == "|&":
                parts.append("".join(buf)); buf = []; i += 2; word_start = True
                continue
            if ch == "|":
                parts.append("".join(buf)); buf = []; i += 1; word_start = True
                continue
        buf.append(ch)
        word_start = ch.isspace()
        i += 1
    parts.append("".join(buf))
    return [p for p in (x.strip() for x in parts) if p]


def measurement_subjects(tool_input, measurement_re, subj_re):
    """Subjects credited by this tool call, or None if it is not a measurement.

    Unit of observation: a pipeline STAGE that starts with a verification command,
    plus the grep/rg/awk/sed filter stages that follow it — that is where the subject
    of `ps aux | grep deploy` lives. Every statement in the command is examined, so
    `cd /x && pgrep deploy` and `pgrep a; pgrep b` both credit; `pgrep a; echo b`,
    `pgrep a | tee b.log` and `pgrep a # b` do not. A stage carrying --help/--version
    observes nothing and is not a measurement. Any exception credits nothing: a
    Stop hook that raises is fail-open, so this function must be total."""
    try:
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str):
            return None
        credited, found = set(), False
        for stmt in split_shell(command, statements=True):
            stages = split_shell(stmt, statements=False)
            i = 0
            while i < len(stages):
                stage = stages[i]
                i += 1
                if not measurement_re.search(stage) or _NON_OBSERVING & set(stage.split()):
                    continue
                found = True
                credited |= subjects(stage, subj_re)
                while i < len(stages) and _FILTER_HEAD.search(stages[i]):
                    credited |= subjects(stages[i], subj_re)
                    i += 1
        return credited if found else None
    except Exception:
        return None



def current_turn(path):
    """Entries since the last genuine human message (tool results are type 'user' too)."""
    out = []
    try:
        with open(path) as fh:
            for raw in fh:
                try:
                    d = json.loads(raw)
                except Exception as e:
                    raise ValueError("malformed transcript JSON") from e
                if d.get("type") == "user":
                    c = d.get("message", {}).get("content", [])
                    human = True if isinstance(c, str) else \
                        "tool_result" not in {x.get("type") for x in c if isinstance(x, dict)}
                    if human:
                        out = []
                        continue
                out.append(d)
    except OSError as e:
        raise ValueError(f"cannot read transcript: {e}") from e
    return out


def scan_turn(turn, claim_re, completion_re, measurement_re, subj_re, non_subjects):
    """Return the list of unbacked claims (empty = clean)."""
    verified, pending, bad = set(), [], []
    for d in turn:
        content = d.get("message", {}).get("content", [])
        if d.get("type") == "assistant":
            for c in content:
                if c.get("type") == "tool_use":
                    measured = measurement_subjects(c.get("input", {}), measurement_re, subj_re)
                    if measured is not None:
                        pending.append((c.get("id"), measured))
                elif c.get("type") == "text":
                    txt = strip_quoted(c.get("text", ""))
                    for m in claim_re.finditer(txt):
                        ctx = claim_clause(txt, m.start(), m.end(), subj_re, non_subjects)
                        cs = claim_subjects(ctx, subj_re, non_subjects)
                        completion = bool(completion_re.fullmatch(m.group(0).strip()))
                        if cs:
                            missing = cs - verified
                            ok = not missing
                        elif completion:
                            # In-turn prose naming no background subject ("the edits are
                            # finished"); its output is already in the transcript.
                            ok, missing = True, set()
                        else:
                            # A subjectless RUNNING claim ("both are still running") has no
                            # subject that could have been checked. It must NOT pass off an
                            # unrelated probe — that is the exact over-claim this gate exists
                            # to stop. Uncovered → block.
                            ok, missing = False, set()
                        if not ok:
                            bad.append((m.group(0), sorted(missing), " ".join(ctx.split())[:160]))
        elif d.get("type") == "user":
            for c in content if isinstance(content, list) else []:
                if c.get("type") != "tool_result" or not pending:
                    continue
                result_id = c.get("tool_use_id")
                index = next((i for i, item in enumerate(pending)
                              if result_id and item[0] == result_id), None)
                if index is None:
                    continue
                _, measured = pending.pop(index)
                verified |= measured
    return bad


def block_message(bad, verify_hint):
    lines = ["⚠ UNVERIFIED LIVE-STATE CLAIM IN THIS TURN — do not end the turn as-is.",
             "", "You asserted state this turn without measuring it this turn:"]
    for claim, missing, ctx in bad[:4]:
        tag = f"  [never checked: {', '.join(missing)}]" if missing else "  [no subject named / not measured]"
        lines.append(f'  • "{claim}"{tag}')
        lines.append(f"      …{ctx}…")
    lines += [
        "",
        "Do ONE of these before finishing:",
        f"  1. RUN THE CHECK NOW — {verify_hint} — and restate the fact from what it returned.",
        "  2. DELETE the claim.",
        "  3. REPLACE it with a plain statement that you have not verified it",
        "     (\"I have not verified X\"). The claim phrase itself must go — appending a",
        "     label leaves the assertion in place and this gate will still block it.",
        "",
        "Launching is not evidence. An unconditional command (`echo done`, a bare `&`) cannot",
        "fail, so its output confirms nothing. A check from earlier in this turn is stale —",
        "processes exit. Verifying one subject does not license a claim about another.",
    ]
    return "\n".join(lines)


def check_config():
    """Validate the active config for stairs to nowhere: every verification command's
    binary must resolve on THIS box, and no required list may be empty. Exit 0 clean,
    1 if any problem — the adaptation skill makes passing this an acceptance gate."""
    cfg = load_config()
    problems = []
    for k in REQUIRED_LISTS:
        if not cfg.get(k):
            problems.append(f"required list '{k}' is empty — the gate would be disabled")
    for pat in cfg.get("verification_commands", []):
        # Head token = the binary the command starts with (strip regex noise).
        head = re.match(r"[A-Za-z0-9_./-]+", pat)
        name = head.group(0) if head else pat
        if name in ("jobs",):
            continue  # shell builtin, always present
        if shutil.which(name) is None:
            problems.append(f"verification command '{name}' does not resolve on this box "
                            f"(stair to nowhere — it would read as coverage and verify nothing)")
    if problems:
        print("check-config: PROBLEMS")
        for p in problems:
            print(f"  ✗ {p}")
        print("Fix these before trusting the gate: drop unresolved commands, fill empty lists.")
        return 1
    print(f"check-config: OK — {len(cfg['verification_commands'])} verification command(s) resolve, "
          f"{len(cfg['subjects'])} subject pattern(s), no empty required list")
    return 0


def self_test():
    # The fixtures below hard-code their own vocabulary ("deploy", "build", `pgrep -af deploy`), so
    # this must run against the BUILT-IN config, not the operator's. Using load_config() here made a
    # perfectly legal narrowing -- e.g. subjects: ["celery[\\w-]*"] with a systemctl probe -- print
    # SELF-TEST FAIL while --check-config printed OK, so the two acceptance commands the skill asks
    # for disagreed and a correctly-configured adopter was told the gate was broken. This checks that
    # the MECHANISM is intact; --check-config is what validates the operator's own config.
    cfg = dict(DEFAULT_CONFIG)
    claim_re, completion_re, measurement_re, subj_re, non_subjects = compile_config(cfg)

    def scan(turn):
        return scan_turn(turn, claim_re, completion_re, measurement_re, subj_re, non_subjects)

    def txt(t):
        return {"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}}

    def cmd(command, tid="t1"):
        return [{"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": tid, "input": {"command": command}}]}},
                {"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": tid}]}}]

    cases = [
        # (name, turn, must_block)
        ("unbacked running claim",
         [txt("The deploy is still running in the background.")], True),
        ("claim backed by pgrep",
         cmd("pgrep -af deploy") + [txt("The deploy is still running in the background.")], False),
        ("in-turn completion prose",
         [txt("The edits are finished.")], False),
        ("quoted claim (short)",
         [txt('The alert reads "the job is still running" verbatim.')], False),
        ("quoted claim (long >200 chars)",
         [txt('The docs say "' + "x" * 230 + ' the job is still running here" and nothing else.')], False),
        ("subjectless running claim off an unrelated probe MUST block",
         cmd("pgrep -af build") + [txt("Both are still running.")], True),
        ("claim about subject B backed only by a probe of subject A MUST block",
         cmd("pgrep -af build") + [txt("The deploy is still running.")], True),
        ("adjectival 'complete'/'done' is not a completion claim (no false block)",
         [txt("The build is complete garbage and the deploy is done wrong.")], False),
        ("a non-probe command (echo) is not verification — claim still blocks",
         cmd("echo the deploy is running") + [txt("The deploy is still running.")], True),
        # ── unit of observation: statements and pipeline stages (2026-09-21) ──
        # Each arm below was RED on the previous splitter (single `|` split, first statement only,
        # whole-command `^`, no `&`/`#`/newline) — the control that proves these can fail.
        ("D2: the subject of `ps aux | grep deploy` lives on the filter stage — PASS",
         cmd("ps aux | grep deploy") + [txt("The deploy is still running.")], False),
        ("D3: a trailing `; echo deploy` after a probe of build must not vouch — BLOCK",
         cmd("pgrep -af build; echo deploy") + [txt("The deploy is still running.")], True),
        ("D3 twin: `&&` list", cmd("pgrep -af build && echo deploy") + [txt("The deploy is still running.")], True),
        ("D3 twin: single `&` is a list operator too", cmd("pgrep -af build & echo deploy") + [txt("The deploy is still running.")], True),
        ("D3 twin: newline", cmd("pgrep -af build\necho deploy") + [txt("The deploy is still running.")], True),
        ("D3 twin: `# deploy` in a comment cannot vouch", cmd("pgrep -af build # deploy") + [txt("The deploy is still running.")], True),
        ("D3 twin: `| echo deploy` after a probe is not a filter", cmd("pgrep -af build | echo deploy") + [txt("The deploy is still running.")], True),
        ("D3 twin: `| tee build.log` is not a filter", cmd("pgrep -af deploy | tee build.log") + [txt("The build is still running.")], True),
        ("D3 twin: a $(...) body does not vouch", cmd("echo $(pgrep -af build; echo deploy)") + [txt("The deploy is still running.")], True),
        ("a probe after `cd /x &&` is still a probe — PASS", cmd("cd /tmp && pgrep -af deploy") + [txt("The deploy is still running.")], False),
        ("a probe on a downstream pipe stage — PASS", cmd("echo dummy | pgrep -af deploy") + [txt("The deploy is still running.")], False),
        ("two probes in one call both credit — PASS",
         cmd("pgrep -af deploy && pgrep -af build") + [txt("Both deploy and build are still running.")], False),
        ("`2>&1` is a redirect, not a list operator — PASS", cmd("pgrep -af deploy 2>&1 | grep -v grep") + [txt("The deploy is still running.")], False),
        ("an unclosed quote does not raise (a raising Stop hook is fail-open) — PASS",
         cmd('ps aux | grep deploy "unclosed') + [txt("The deploy is still running.")], False),
        ("`pgrep --help deploy` observes nothing — BLOCK", cmd("pgrep --help deploy") + [txt("The deploy is still running.")], True),
    ]
    ok = True
    for name, turn, must_block in cases:
        blocked = bool(scan(turn))
        if blocked != must_block:
            print(f"SELF-TEST FAIL: {name} — expected {'BLOCK' if must_block else 'PASS'}, "
                  f"got {'BLOCK' if blocked else 'PASS'}")
            ok = False
    if ok:
        print(f"SELF-TEST PASS: {len(cases)}/{len(cases)} cases "
              "(blocks unbacked/cross-subject/subjectless-running/non-probe/trailing-statement vouch; "
              "passes backed/prose/quoted/adjectival/pipe-filter/later-statement probes)")
    return 0 if ok else 1


def main():
    if "--self-test" in sys.argv:
        return self_test()
    if "--check-config" in sys.argv:
        return check_config()
    try:
        inp = json.load(sys.stdin)
    except Exception:
        return 0
    if inp.get("stop_hook_active"):
        return 0
    tp = inp.get("transcript_path") or ""
    if not tp or not os.path.exists(tp):
        return 0

    cfg = load_config()
    claim_re, completion_re, measurement_re, subj_re, non_subjects = compile_config(cfg)

    try:
        turn = current_turn(tp)
    except ValueError as e:
        print(json.dumps({"decision": "block", "reason": f"CANNOT CHECK — {e}"}))
        return 0

    bad = scan_turn(turn, claim_re, completion_re, measurement_re, subj_re, non_subjects)
    if not bad:
        return 0
    print(json.dumps({"decision": "block", "reason": block_message(bad, cfg["verify_hint"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
