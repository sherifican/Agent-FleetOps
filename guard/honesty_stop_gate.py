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
import fnmatch, json, os, re, shutil, sys, time

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
    # ── file-written claims (2026-09-21) ──
    # A claim written INTO A FILE outlives the turn: the reader of the report never sees the
    # transcript. Bodies of these tool calls are scanned exactly like prose, against what had been
    # verified at the moment of the write. Measured over the private transcript corpus this was
    # built against (not published): heredoc `.md` writes outnumbered Write+Edit 1,998 to 728,
    # so the shell channel is covered too.
    "write_tools": {"Write": ["content"], "Edit": ["new_string"], "NotebookEdit": ["new_source"]},
    "write_path_keys": ["file_path", "notebook_path"],
    "heredoc_sinks": ["cat", "tee", "dd", "sponge"],   # commands whose stdin lands in a file
    "arg_sinks": ["echo", "printf"],                   # `echo 'claim' > f` — the arguments land
    # Source files carry claims in comments and strings that are not reports — skipped.
    # Not skipped: .md .txt .rst .log .html .json .csv — the formats status documents come in.
    "write_skip_suffixes": [".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".c", ".cc",
                            ".cpp", ".h", ".hpp", ".java", ".rb", ".sh", ".css", ".yml", ".yaml", ".toml"],
    # Files that legitimately QUOTE claim phrases (this gate, its spec/skill, changelogs). Exact
    # names or narrow globs only — a pattern with no literal name (`*`, `*.md`, `**/*`) is refused
    # at load, because it would silently turn the file scan off.
    "write_skip_paths": ["honesty_stop_gate.py", "honesty-stop-gate.md", "skills/honesty-stop-gate/*",
                         "CHANGELOG", "CHANGELOG.*", "HISTORY", "HISTORY.*"],
    "write_max_bytes": 65536,          # one body above this → CANNOT CHECK block (never a prefix scan)
    "write_max_total_bytes": 262144,   # per turn, all bodies
}

_WRITE_CAP_FLOOR = 32768        # write_max_bytes cannot be configured below this
_WRITE_CAP_CEILING = 1048576    # nor above this: a body that large is a CANNOT CHECK, not a scan
_REPORT_SUFFIXES = {".md", ".txt", ".rst", ".log", ".html", ".htm", ".json", ".csv"}  # never skippable


def _skip_pattern_ok(pat):
    """A skip pattern must NAME something: a literal basename (globs may surround it, and a
    dotfile like `.env` is a name) or a literal directory segment. A bare extension (`*.md`),
    `*`, `**/*`, `*/*`, `.*` and `[!.]*` carry no name and are refused — each would turn the
    file scan off while reading as configured."""
    if not isinstance(pat, str) or not pat.strip():
        return False
    segs = [x for x in pat.replace("\\", "/").split("/") if x]
    if not segs:
        return False
    glob = re.compile(r"\[[^\]]*\]|[*?]")
    last = segs[-1]
    literal = glob.sub("", last)
    if re.search(r"[A-Za-z0-9]", literal) and not (literal.startswith(".") and last != literal):
        return True
    return any(re.search(r"[A-Za-z0-9]", glob.sub("", seg)) for seg in segs[:-1])


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    refused = []
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
                    if k == "write_skip_paths":
                        if not isinstance(v, list):
                            continue
                        bad = [x for x in v if not _skip_pattern_ok(x)]
                        refused += [f"write_skip_paths: {x!r} names nothing — dropped" for x in bad]
                        v = [x for x in v if _skip_pattern_ok(x)]
                    if k == "write_skip_suffixes":
                        if not isinstance(v, list):
                            continue
                        bad = [x for x in v if str(x).lower() in _REPORT_SUFFIXES]
                        refused += [f"write_skip_suffixes: {x!r} is a report format — dropped" for x in bad]
                        v = [x for x in v if str(x).lower() not in _REPORT_SUFFIXES]
                    if k in ("write_max_bytes", "write_max_total_bytes"):
                        if isinstance(v, bool) or not isinstance(v, int):
                            refused.append(f"{k}: {v!r} is not an integer — default kept")
                            continue
                        clamped = min(max(v, _WRITE_CAP_FLOOR), _WRITE_CAP_CEILING)
                        if clamped != v:
                            refused.append(f"{k}: {v} clamped to {clamped}")
                        v = clamped
                    if k == "write_tools" and (not isinstance(v, dict) or not v):
                        refused.append("write_tools: empty or not an object — default kept (an empty one turns the file scan off)")
                        continue
                    if k in ("write_path_keys", "heredoc_sinks", "arg_sinks") and (not isinstance(v, list) or not v):
                        refused.append(f"{k}: empty or not a list — default kept")
                        continue
                    cfg[k] = v
    except Exception:
        pass  # a broken override must not disable the gate
    cfg["_refused"] = refused   # named by --check-config; a silent drop reads as configured
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


def _group_end(cmd, i):
    """Index just past the `$( … )`, `( … )` or backtick group starting at i — nesting and
    quotes respected; an unclosed group runs to the end (total, never raises)."""
    n = len(cmd)
    if cmd[i] == "`":
        j = i + 1
        while j < n:
            if cmd[j] == "\\":
                j += 2
                continue
            if cmd[j] == "`":
                return j + 1
            j += 1
        return n
    depth = 0
    quote = None
    j = i
    while j < n:
        ch = cmd[j]
        if quote:
            if ch == "\\" and quote == '"':
                j += 2
                continue
            if ch == quote:
                quote = None
            j += 1
            continue
        if ch == "\\":
            j += 2
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def split_shell(cmd, statements=True):
    """Split a shell command on UNQUOTED operators. Total: never raises.

    statements=True  cuts at the list operators  ;  &  &&  ||  newline
    statements=False cuts at the pipeline operators  |  |&
    Single and double quotes, backslash escapes and # comments are tracked, so a
    separator inside quotes does not split and a comment cannot name a subject.
    `2>&1`, `>&2`, `&>f`, `<&0` are redirections, not list operators.
    A `$( … )` command substitution, a `( … )` subshell and a backtick group are copied
    verbatim as part of the statement they sit in — a `;` inside one never starts a
    statement, so `echo $(true; pgrep x)` cannot credit `x` (it used to). Nothing inside
    such a group is credited: a probe run that way blocks, and the fix is to run it
    plainly. Heredoc bodies are not split here either: callers remove them first
    (`without_heredoc_bodies`), because a body left in place CAN grant a credit.
    An unclosed quote runs to the end of the string; nothing is raised."""
    if not isinstance(cmd, str):
        return []
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
        if statements and (cmd.startswith("$(", i) or (ch == "(" and word_start) or ch == "`"):
            # A command substitution, a subshell or a backtick group is ONE word to the outer
            # list: `echo $(true; pgrep x)` must not yield a statement that starts with `pgrep`.
            # Nothing inside is credited (the spec says so); the group is copied verbatim.
            j = _group_end(cmd, i)
            buf.append(cmd[i:j])
            i = j
            word_start = False
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
            if ch == "|" and buf and buf[-1] == ">":   # `>|` is a redirect, not a pipe
                buf.append(ch)
                i += 1
                word_start = False
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


def _non_observing(stage):
    """A stage carrying --help/--version/-V (quoted or not) observes nothing."""
    return bool(_NON_OBSERVING & {_unquote(w) for w in _WORD_RE.findall(stage)})


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
        if "<<" in command:
            command = without_heredoc_bodies(command)   # a body is data, never a probe (arm H8)
        for stmt in split_shell(command, statements=True):
            stages = split_shell(stmt, statements=False)
            i = 0
            while i < len(stages):
                stage = stages[i]
                i += 1
                if not measurement_re.search(stage) or _non_observing(stage):
                    continue
                found = True
                credited |= subjects(stage, subj_re)
                while i < len(stages) and _FILTER_HEAD.search(stages[i]) and not _non_observing(stages[i]):
                    credited |= subjects(stages[i], subj_re)
                    i += 1
        return credited if found else None
    except Exception:
        return None





def _file_skipped(path, cfg):
    if os.path.splitext(path)[1].lower() in set(cfg.get("write_skip_suffixes") or []):
        return True
    norm = path.replace("\\", "/")
    if norm.startswith("/dev/") or norm in ("-", "/dev/stdout"):
        return True   # a stream, not a file a reader opens
    base = norm.rsplit("/", 1)[-1]
    for pat in cfg.get("write_skip_paths") or []:
        if not _skip_pattern_ok(pat):
            continue
        if "/" in pat:
            if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(norm, "*/" + pat):
                return True
        elif fnmatch.fnmatch(base, pat):
            return True
    return False


_HEREDOC_OPEN = re.compile(r"<<(-?)\s*(?:(['\"])([^'\"]+)\2|\\?([^\s;&|<>()'\"]+))")
_SINK_WRAPPERS = {"sudo", "doas", "env", "nohup", "command", "builtin", "exec", "nice", "stdbuf",
                  "timeout", "time", "busybox", "ionice", "chronic"}
_SHELL_KEYWORDS = {"if", "then", "do", "else", "elif", "while", "until", "!", "{", "("}
_WORD_RE = re.compile(r"""(?:[^\s'"\\]|\\.|'[^']*'|"(?:[^"\\]|\\.)*")+""")
_REDIR_RE = re.compile(r"^(?:\d+|&)?(>>|>\||>|<<<|<<-|<<|<)(.*)$")


def _heredoc_openers(line):
    r"""The `<<TAG` operators on a line as the shell sees them — outside quotes, before an
    unquoted `#`, not a here-string (`<<<`), not the shift inside `$(( ))`/`(( ))`/`let` — as
    [(strip_tabs, tag)]. Tags: `<<'EOF'`, `<<"END TAG"`, `<<\EOF`, `<<-EOF`, `<<0`, `<<.EOF`."""
    out = []
    if re.match(r"^\s*let\b", line):
        return out
    quote = None
    word_start = True
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if quote:
            if quote == '"' and ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "\\":
            i += 2
            word_start = False
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            word_start = False
            continue
        if ch == "#" and word_start:
            break
        if line.startswith("$((", i) or (word_start and line.startswith("((", i)):
            j = line.find("))", i + 2)
            i = n if j < 0 else j + 2
            word_start = False
            continue
        if line.startswith("<<<", i):
            i += 3
            word_start = False
            continue
        if line.startswith("<<", i):
            m = _HEREDOC_OPEN.match(line, i)
            if m:
                out.append((m.group(1) == "-", m.group(3) or m.group(4)))
                i = m.end()
                word_start = False
                continue
        word_start = ch.isspace() or ch in ";&|("
        i += 1
    return out


def _heredoc_blocks(command):
    """Every heredoc as (opener_line_index, opener_line, body_lines, terminated, ordinal,
    body_start_line). The
    closing tag must be the whole line, exactly — an indented `  EOF` is body, as in the shell —
    except under `<<-`, where leading tabs are stripped. An UNTERMINATED body runs to the end of
    the command: the shell warns and still feeds it to the command, so the file is written.
    Bodies for several openers on one line are consumed in order."""
    out = []
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        opens = _heredoc_openers(lines[i])
        if not opens:
            i += 1
            continue
        j = i + 1
        for ordinal, (strip_tabs, tag) in enumerate(opens):
            body = []
            start = j
            while j < len(lines):
                line = lines[j].lstrip("\t") if strip_tabs else lines[j]
                if line == tag:
                    break
                body.append(line)
                j += 1
            terminated = j < len(lines)
            out.append((i, lines[i], body, terminated, ordinal, start))
            if not terminated:
                break
            j += 1
        i = j
    return out


def _unquote(tok):
    out = []
    i, n = 0, len(tok)
    while i < n:
        ch = tok[i]
        if ch == "\\" and i + 1 < n:
            out.append(tok[i + 1])
            i += 2
        elif ch == "'":
            k = tok.find("'", i + 1)
            k = n if k < 0 else k
            out.append(tok[i + 1:k])
            i = k + 1
        elif ch == '"':
            i += 1
            while i < n and tok[i] != '"':
                if tok[i] == "\\" and i + 1 < n:
                    out.append(tok[i + 1])
                    i += 2
                else:
                    out.append(tok[i])
                    i += 1
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _stage_head(stage):
    """(head, args, targets, herestrings) of one pipeline stage: the command word after leading
    shell keywords (`then`, `{`, `(`), VAR=val assignments, redirects (`> f cat`, `2>/dev/null`)
    and sudo-style wrappers (`sudo -n`, `timeout 5`, `nice -n 5`); its arguments with redirects
    and the heredoc operator removed; the `>`/`>>`/`>|`/`&>` targets, unquoted, minus process
    substitutions; and any `<<<` here-string words."""
    words = _WORD_RE.findall(stage)
    head, args, targets, heres = None, [], [], []
    k = 0
    while k < len(words):
        w = words[k]
        k += 1
        if head is None:
            stripped = w.lstrip("{(!")
            if w in _SHELL_KEYWORDS or not stripped:
                continue
            w = stripped
        m = _REDIR_RE.match(w)
        if m:
            op, rest = m.group(1), m.group(2)
            if not rest and k < len(words):
                rest = words[k]
                k += 1
            if op == "<<<" and rest:
                heres.append(_unquote(rest))
            elif op in (">", ">>", ">|") and rest and not rest.startswith(("&", "(")):
                targets.append(_unquote(rest))
            continue
        if head is None:
            if re.match(r"^[A-Za-z_]\w*=", w):
                continue
            name = _unquote(w).rsplit("/", 1)[-1]
            if name in _SINK_WRAPPERS:
                while k < len(words) and words[k].startswith("-"):
                    k += 1
                if name in ("timeout", "nice", "ionice") and k < len(words) and re.match(r"^\d+(?:\.\d+)?[smhd]?$", words[k]):
                    k += 1
                continue
            head = name
            continue
        args.append(_unquote(w))
    return head, args, targets, heres


def _sink_paths(head, args, targets, stages, k):
    """Where a body handed to this stage lands: its own `>` targets, the sink's file argument
    (`tee f`, `dd of=f`, `sponge f`), and a downstream `| tee f` / `| sudo tee f` stage."""
    paths = list(targets)
    if head == "tee":
        paths += [a for a in args if not a.startswith("-")]
    elif head == "dd":
        paths += [a[3:] for a in args if a.startswith("of=")]
    elif head == "sponge":
        paths += [a for a in args if not a.startswith("-")]
    for st in stages[k + 1:]:
        h2, a2, t2, _ = _stage_head(st)
        if h2 == "tee":
            paths += [a for a in a2 if not a.startswith("-")] + t2
    return paths


def _heredoc_bodies(command, sinks):
    """[(path, body)] for every heredoc whose own pipeline stage is a sink that lands in a file:
    `cat > f <<EOF`, `> f cat <<EOF`, `if …; then cat > f <<EOF`, `tee f <<EOF > /dev/null`,
    `sudo tee f <<EOF`, `cat <<EOF | tee f`, `dd of=f <<EOF`, in any statement position.
    Redirects belong to their own stage, so `cat <<EOF | git commit -F - > log.txt` writes a
    commit message, not log.txt. An interpreter's stdin (`python3 - <<PY`) is not a file
    write and is not extracted; an unterminated body is (the shell still writes it)."""
    out = []
    if not sinks:
        return out
    for _, opener, body, _terminated, ordinal, _start in _heredoc_blocks(command):
        slots = []
        for stmt in split_shell(opener, statements=True):
            stages = split_shell(stmt, statements=False)
            for n, st in enumerate(stages):
                slots += [(stages, n)] * len(_heredoc_openers(st))
        if ordinal >= len(slots):
            continue
        stages, k = slots[ordinal]
        head, args, targets, _ = _stage_head(stages[k])
        if head not in sinks:
            continue
        text = "\n".join(body)
        for path in _sink_paths(head, args, targets, stages, k):
            out.append((path, text))
    return out


def _arg_bodies(command, sinks, arg_sinks):
    """[(path, body, via)] for the non-heredoc write forms: `echo 'claim' > f`, `printf … >> f`,
    `echo 'claim' | tee f`, and a here-string into a sink (`cat <<< 'claim' > f`). The body is
    the command's unquoted arguments — what the file receives."""
    out = []
    for stmt in split_shell(without_heredoc_bodies(command), statements=True):
        stages = split_shell(stmt, statements=False)
        for k, st in enumerate(stages):
            head, args, targets, heres = _stage_head(st)
            if head in arg_sinks:
                paths = _sink_paths(head, [], targets, stages, k)
                if not paths:
                    continue
                words = args
                if head == "echo":
                    while words and re.fullmatch(r"-[neE]+", words[0]):
                        words = words[1:]
                text = " ".join(words)
                out += [(p, text, head) for p in paths]
            elif head in sinks and heres:
                paths = _sink_paths(head, args, targets, stages, k)
                out += [(p, "\n".join(heres), "here-string") for p in paths]
    return out


def without_heredoc_bodies(command):
    """The command with every heredoc body removed — a body is DATA handed to a command, never
    a statement, so `pgrep -af deploy` quoted inside one must not read as a probe."""
    blocks = _heredoc_blocks(command)
    if not blocks:
        return command
    lines = command.split("\n")
    drop = set()
    for _i, _, body, _t, _o, start in blocks:
        drop.update(range(start, start + len(body)))
    return "\n".join(l for k, l in enumerate(lines) if k not in drop)


def file_bodies(name, tool_input, cfg):
    """[(path, body, via)] — every file body this tool call writes that the gate must read as a
    report. Total: never raises (a raising Stop hook is fail-open)."""
    try:
        if not isinstance(tool_input, dict):
            return []
        out = []
        fields = (cfg.get("write_tools") or {}).get(name)
        if fields:
            path = next((tool_input.get(k) for k in cfg.get("write_path_keys") or [] if isinstance(tool_input.get(k), str)), None)
            if path:
                for f in fields:
                    body = tool_input.get(f)
                    if isinstance(body, str):
                        out.append((path, body, name))
        command = tool_input.get("command")
        if isinstance(command, str) and any(x in command for x in ("<<", ">", "|")):
            sinks = cfg.get("heredoc_sinks") or []
            if "<<" in command:
                for path, body in _heredoc_bodies(command, sinks):
                    out.append((path, body, "heredoc"))
            out += _arg_bodies(command, sinks, cfg.get("arg_sinks") or [])
        return [(p, b, v) for p, b, v in out if not _file_skipped(p, cfg)]
    except Exception:
        return []


_MAX_FINDINGS = 8   # per body; measured: 64 KiB of "is running " took 102 s unbounded


def scan_body(text, verified, claim_re, completion_re, subj_re, non_subjects, where=None):
    """The claim loop over one body of text (a prose block or a file body). Returns the
    unbacked claims as (claim, missing, ctx, where) — `where` is None for prose."""
    bad = []
    txt = strip_quoted(text)
    for m in claim_re.finditer(txt):
        ctx = claim_clause(txt, m.start(), m.end(), subj_re, non_subjects)
        cs = claim_subjects(ctx, subj_re, non_subjects)
        completion = bool(completion_re.fullmatch(m.group(0).strip()))
        if cs:
            missing = cs - verified
            ok = not missing
        elif completion and where is None:
            # In-turn prose naming no background subject ("the edits are finished"); its
            # output is already in the transcript. A file does not carry the transcript with
            # it, so the same words WRITTEN TO A FILE are an unbacked claim (arm W12b).
            ok, missing = True, set()
        else:
            # A subjectless RUNNING claim ("both are still running") has no subject that
            # could have been checked. It must NOT pass off an unrelated probe — that is the
            # exact over-claim this gate exists to stop. Uncovered → block.
            ok, missing = False, set()
        if not ok:
            bad.append((m.group(0), sorted(missing), " ".join(ctx.split())[:160], where))
            if len(bad) >= _MAX_FINDINGS:
                break   # the verdict is BLOCK already; claim_clause is O(claims × length)
    return bad


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
                    c = (d.get("message") or {}).get("content", [])
                    human = True if isinstance(c, str) else \
                        "tool_result" not in {x.get("type") for x in c if isinstance(x, dict)}
                    if human:
                        out = []
                        continue
                out.append(d)
    except OSError as e:
        raise ValueError(f"cannot read transcript: {e}") from e
    return out


def scan_turn(turn, claim_re, completion_re, measurement_re, subj_re, non_subjects, cfg=None):
    """Return the list of unbacked claims (empty = clean). Each is
    (claim, missing_subjects, context, where) — `where` names the file a claim was written to,
    or None for prose. A "CANNOT CHECK" entry means a file body was in scope but too large to
    scan; that is a block, never a prefix scan that reads as coverage."""
    cfg = cfg if cfg is not None else DEFAULT_CONFIG
    def _cap(key):
        v = cfg.get(key)
        v = v if isinstance(v, int) and not isinstance(v, bool) else DEFAULT_CONFIG[key]
        return min(max(v, _WRITE_CAP_FLOOR), _WRITE_CAP_CEILING)
    cap, total_cap = _cap("write_max_bytes"), _cap("write_max_total_bytes")
    verified, pending, bad, total = set(), [], [], 0
    for d in turn:
        msg = d.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            content = []
        content = [c for c in content if isinstance(c, dict)]
        if d.get("type") == "assistant":
            for c in content:
                if c.get("type") == "tool_use":
                    tool_input = c.get("input", {})
                    measured = measurement_subjects(tool_input, measurement_re, subj_re)
                    if measured is not None:
                        pending.append((c.get("id"), measured))
                    # A file body is scanned against what was verified BEFORE this call: a
                    # probe in the same call has not returned yet, so it cannot vouch (arm H4).
                    for path, body, via in file_bodies(c.get("name"), tool_input, cfg):
                        where = f"{path} (via {via})"
                        size = len(body.encode("utf8", "replace"))
                        total += size
                        if size > cap or total > total_cap:
                            bad.append(("CANNOT CHECK", [],
                                        f"file body {size} bytes (cap {cap}; turn total {total} of {total_cap}) — "
                                        f"too large to scan; split the write or move claims into prose", where))
                            continue
                        try:
                            found = scan_body(body, verified, claim_re, completion_re, subj_re,
                                              non_subjects, where=where)
                            if measured:
                                # A probe in this same call has not returned when the file is written.
                                found = [(c, m, ctx + " ‖ a probe of " + ", ".join(sorted(measured)) +
                                          " in this same call had not returned yet — write the file AFTER its result", w)
                                         if set(m) & measured else (c, m, ctx, w) for c, m, ctx, w in found]
                            bad.extend(found)
                        except Exception:
                            pass  # never let a body take the hook down (fail-open otherwise)
                elif c.get("type") == "text":
                    bad.extend(scan_body(c.get("text", ""), verified, claim_re, completion_re,
                                         subj_re, non_subjects))
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
    for claim, missing, ctx, where in bad[:4]:
        if claim == "CANNOT CHECK":
            lines.append(f"  • CANNOT CHECK {where}: {ctx}")
            continue
        tag = f"  [never checked: {', '.join(missing)}]" if missing else "  [no subject named / not measured]"
        lines.append(f'  • "{claim}"{tag}')
        lines.append(f"      …{ctx}…")
        if where:
            lines.append(f"      written to {where} — the file still says this; edit it out or "
                         f"re-measure, then rewrite it")
    if any(w for *_, w in bad[:4]):
        lines += ["", "A claim written to a file outlives this turn: the reader never sees the",
                  "transcript, and a blocked turn does not undo a write — after one block the next",
                  "Stop is not inspected. Options 2 and 3 below mean REWRITING THE FILE without the claim."]
    lines += [
        "",
        "Do ONE of these before finishing:",
        f"  1. RUN THE CHECK NOW — {verify_hint} — and restate the fact from what it returned.",
        "  2. DELETE the claim.",
        "  3. REPLACE it with a plain statement that you have not verified it",
        "     (\"I have not verified X\"). The claim phrase itself must go — appending a",
        "     label leaves the assertion in place and this gate will still block it.",
        "     Quote another party's words in double quotes, backticks or a blockquote —",
        "     quoted text is not scanned; single quotes are not stripped.",
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
    problems += cfg.get("_refused", [])
    print(f"check-config: file scan — tools {sorted(cfg['write_tools'])} (path keys {cfg['write_path_keys']}), "
          f"sinks {cfg['heredoc_sinks']} + {cfg['arg_sinks']}, {len(cfg['write_skip_paths'])} skip path(s), "
          f"{len(cfg['write_skip_suffixes'])} skip suffix(es), cap {cfg['write_max_bytes']} B/body")
    if problems:
        print("check-config: PROBLEMS")
        for p in problems:
            print(f"  ✗ {p}")
        print("Fix these before trusting the gate: drop unresolved commands, fill empty lists.")
        return 1
    print(f"check-config: OK — {len(cfg['verification_commands'])} verification command(s) resolve, "
          f"{len(cfg['subjects'])} subject pattern(s), no empty required list, no refused narrowing")
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
        return scan_turn(turn, claim_re, completion_re, measurement_re, subj_re, non_subjects, cfg=cfg)

    def txt(t):
        return {"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}}

    def write(path, content, tid="w1"):
        return [{"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": tid, "name": "Write",
                     "input": {"file_path": path, "content": content}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid}]}}]

    def edit(path, new, old="x", tid="e1"):
        return [{"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": tid, "name": "Edit",
                     "input": {"file_path": path, "old_string": old, "new_string": new, "replace_all": False}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid}]}}]

    D = "The deploy is still running."

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
        # ── file-written claims (2026-09-21). Control: every BLOCK arm here was PASS (i.e. RED)
        # on the gate before file bodies were scanned — a claim written to a file was invisible.
        ("W1: Write body carrying an unbacked claim — BLOCK", write("/x/report.md", D), True),
        ("W2: probe, then Write the claim — PASS", cmd("pgrep -af deploy") + write("/x/report.md", D), False),
        ("W3: Write the claim, THEN probe — BLOCK (scanned against what was verified at write time)",
         write("/x/report.md", D) + cmd("pgrep -af deploy"), True),
        ("W4: restating a verified prose claim in a file — PASS",
         cmd("pgrep -af deploy") + [txt(D)] + write("/x/report.md", "Summary: " + D), False),
        ("W5: a quoted claim in a file body — PASS",
         write("/x/report.md", 'The alert reads "the job is still running" verbatim.'), False),
        ("W6: file claim about deploy off a probe of build — BLOCK", cmd("pgrep -af build") + write("/x/report.md", D), True),
        ("W7: subjectless running claim in a file — BLOCK", write("/x/report.md", "Both are still running."), True),
        ("W8: Edit new_string carrying the claim — BLOCK", edit("/x/report.md", D), True),
        ("W9: Edit old_string is the text being REMOVED — not scanned — PASS",
         edit("/x/report.md", "The deploy exited rc=1.", old=D), False),
        ("W10: a source file (.py) is skipped — PASS", write("/x/t.py", "x = 1  # the deploy is still running"), False),
        ("W11: 64 KiB of padding then a claim — BLOCK as CANNOT CHECK, never a prefix scan",
         write("/x/report.md", "x" * (64 * 1024 + 1) + " " + D), True),
        ("W11c: over the cap with NO claim — still CANNOT CHECK — BLOCK", write("/x/report.md", "x" * (64 * 1024 + 1)), True),
        ("W12: 'was completed' in a file has no transcript behind it — BLOCK",
         write("/x/handoff.md", "The deploy was completed."), True),
        ("H1: `cat > f <<EOF` heredoc body carrying the claim — BLOCK",
         cmd("cat > /x/report.md <<'EOF'\n" + D + "\nEOF"), True),
        ("H2: probe, then the heredoc — PASS",
         cmd("pgrep -af deploy") + cmd("cat > /x/report.md <<'EOF'\n" + D + "\nEOF", tid="t2"), False),
        ("H3: an interpreter's stdin heredoc is not a file write — PASS",
         cmd("python3 - <<'PY'\nprint('the deploy is still running')\nPY"), False),
        ("H4: a probe in the SAME call after the heredoc has not returned yet — BLOCK",
         cmd("cat > /x/r.md <<'EOF'\n" + D + "\nEOF\npgrep -af deploy"), True),
        ("H5: `tee f <<EOF` — BLOCK", cmd("tee /x/notes.md <<'EOF'\n" + D + "\nEOF"), True),
        ("H6 (revised): an UNTERMINATED heredoc is still written by the shell, so it is scanned — BLOCK",
         cmd("cat > /x/report.md <<'EOF'\n" + D), True),
        ("H7: a heredoc to a skip path (CHANGELOG) — PASS", cmd("cat > /x/CHANGELOG.md <<'EOF'\n" + D + "\nEOF"), False),
        ("H8: a probe line INSIDE a heredoc body is data, not a measurement — BLOCK",
         cmd("cat > /x/r.md <<'EOF'\npgrep -af deploy\nEOF") + [txt(D)], True),
        ("H9: `cd /x && cat > r.md <<EOF` — the sink is not at line start — BLOCK",
         cmd("cd /x && cat > r.md <<'EOF'\n" + D + "\nEOF"), True),
        ("H10: `cat <<EOF | tee f` — the sink is the pipe target — BLOCK",
         cmd("cat <<'EOF' | tee /x/r.md\n" + D + "\nEOF"), True),
        # ── refutation round (agy, 2026-09-21): each BLOCK/PASS below was wrong on the first candidate ──
        ("A-E1: `<<\\EOF` (backslash-quoted tag) is a heredoc — BLOCK", cmd("cat <<\\EOF > /x/r.md\n" + D + "\nEOF"), True),
        ("A-E2: a redirect BEFORE the sink (`> f cat <<EOF`) — BLOCK", cmd("> /x/r.md cat <<EOF\n" + D + "\nEOF"), True),
        ("A-E3: an indented `  EOF` is body, not the closing tag — BLOCK", cmd("cat <<EOF > /x/r.md\n  EOF\n" + D + "\nEOF"), True),
        ("A-E4: `tee f <<EOF > /dev/null` writes f — BLOCK", cmd("tee /x/r.md <<EOF > /dev/null\n" + D + "\nEOF"), True),
        ("A-E5: a numeric tag `<<0` — BLOCK", cmd("cat <<0 > /x/r.md\n" + D + "\n0"), True),
        ("A-E8: `sudo tee f <<EOF` — BLOCK", cmd("sudo tee /x/r.md <<EOF\n" + D + "\nEOF"), True),
        ("A-P4: a commented `# <<EOF` is not an opener, so the probe after it still credits — PASS",
         cmd("# <<EOF\npgrep -af deploy\nEOF") + [txt(D)], False),
        ("A-F1: `cat <<EOF | git commit -F - > log.txt` writes a commit message, not log.txt — PASS",
         cmd("cat <<EOF | git commit -F - > /x/log.txt\n" + D + "\nEOF"), False),
        ("A-F2: `> /dev/stderr` is a stream, not a file — PASS", cmd("cat <<EOF > /dev/stderr\n" + D + "\nEOF"), False),
        ("A-F4: the sink in ANOTHER statement does not own the heredoc — PASS",
         cmd("cat > /x/f.txt; python3 - <<EOF\n" + D + "\nEOF"), False),
        ("A-C1: a string `content` does not raise (a raising hook is fail-open) — BLOCK",
         [{"type": "assistant", "message": {"content": D}}], True),
        ("A-C2: `message: null` does not raise — PASS", [{"type": "assistant", "message": None}, txt("nothing claimed")], False),
        ("A-E6/F3: skip patterns — `[!.]*` and `[!]*.md` refused, `.env` accepted",
         None, not _skip_pattern_ok("[!.]*") and not _skip_pattern_ok("[!]*.md") and _skip_pattern_ok(".env")),
        # ── refutation round 2 (grok + Fable gate, 2026-09-21) ──
        ("G-E1: `echo 'claim' > f` — the arguments land in the file — BLOCK", cmd("echo '" + D + "' > /x/status.md"), True),
        ("G-E1c: `echo 'claim' | tee f` — BLOCK", cmd("echo '" + D + "' | tee /x/status.md"), True),
        ("G-E1b: `printf '%s\\n' 'claim' >> f` — BLOCK", cmd("printf '%s\\n' '" + D + "' >> /x/status.md"), True),
        ("W12b: a SUBJECTLESS completion claim in a file has no transcript behind it — BLOCK",
         write("/x/handoff.md", "The work was completed."), True),
        ("W12c: the same subjectless completion in PROSE is in-turn work — PASS", [txt("The work was completed.")], False),
        ("P-2 (publish gate, grok): `echo $(true; pgrep -af deploy)` — a `;` inside $( ) is not a statement break — BLOCK",
         cmd("echo $(true; pgrep -af deploy)") + [txt(D)], True),
        ("P-2b: a `( … )` subshell likewise — BLOCK", cmd("(true; pgrep -af deploy)") + [txt(D)], True),
        ("P-2c: a backtick group likewise — BLOCK", cmd("echo `true; pgrep -af deploy`") + [txt(D)], True),
        ("P-2d: a probe AFTER a closed group still credits — PASS", cmd("echo $(date); pgrep -af deploy") + [txt(D)], False),
        ("P-2e: an unclosed `$(` does not raise — BLOCK", cmd("echo $(true; pgrep -af deploy") + [txt(D)], True),
        ("P-1 (publish gate): the SECOND heredoc on a line is stripped too, so a probe quoted in it does not credit — BLOCK",
         cmd("cat <<A; cat <<B\nline_a\nA\npgrep -af deploy\nB") + [txt(D)], True),
        ("G-E1e: a here-string into a sink `cat <<< 'claim' > f` — BLOCK", cmd("cat <<< '" + D + "' > /x/status.md"), True),
        ("G-E1h: `echo 'claim'` with no file is prose already in the transcript — PASS", cmd("echo '" + D + "'"), False),
        ("G-E3: `if …; then cat > f <<EOF` — BLOCK", cmd("if true; then cat > /x/s.md <<EOF\n" + D + "\nEOF\nfi"), True),
        ("G-E4: `cat >| f <<EOF` — BLOCK", cmd("cat >| /x/s.md <<EOF\n" + D + "\nEOF"), True),
        ("G-E5: a quoted tag with a space `<<'END TAG'` — BLOCK", cmd("cat > /x/s.md <<'END TAG'\n" + D + "\nEND TAG"), True),
        ("G-E6: an UNTERMINATED heredoc is still written by the shell — BLOCK", cmd("cat > /x/s.md <<EOF\n" + D), True),
        ("G-E7: the second of two heredocs on one line — BLOCK", cmd("cat > /x/a.md <<A; cat > /x/b.md <<B\nalpha\nA\n" + D + "\nB"), True),
        ("G-E8: `dd of=f <<EOF` — BLOCK", cmd("dd of=/x/s.md <<EOF\n" + D + "\nEOF"), True),
        ("G-E9: a `.json`/`.html` status document is scanned — BLOCK", write("/x/status.html", D), True),
        ("G-P2: `$((1 << n))` is arithmetic, so the probe after it credits — PASS", cmd("echo $((1 << n))\npgrep -af deploy") + [txt(D)], False),
        ("G-P3/F-2: a here-string `<<<\"foo\"` is not an opener — PASS", cmd('grep -q x <<<"foo"\npgrep -af deploy') + [txt(D)], False),
        ("G-P7: `pgrep \"--help\" deploy` observes nothing — BLOCK", cmd('pgrep "--help" deploy') + [txt(D)], True),
        ("G-F1: `> -` is stdout, not a file — PASS", cmd("cat > - <<EOF\n" + D + "\nEOF"), False),
        ("G-F3: `tee >(proc)` is not a path — PASS", cmd("tee >(true) <<EOF\n" + D + "\nEOF"), False),
        ("G-F4: `.tsx` is source — PASS", write("/x/App.tsx", "// " + D), False),
        ("F-1: timing — 64 KiB of unpunctuated claims is SCANNED (blocks) in under 2 s (was 102 s)",
         None, (lambda t0: bool(scan(write("/x/r.md", ("is running " * 8000)[:65536]))) and time.perf_counter() - t0 < 2.0)(time.perf_counter())),
        ("A-S1: skip pattern `*.md` is refused (would silently disable the file scan)",
         None, not _skip_pattern_ok("*.md") and _skip_pattern_ok("skills/honesty-stop-gate/*")
             and _skip_pattern_ok("CHANGELOG*") and not _skip_pattern_ok("**/*") and not _skip_pattern_ok("*")),
    ]
    ok = True
    for name, turn, must_block in cases:
        if turn is None:            # a predicate arm: must_block IS the verdict of a check that can fail
            if must_block is not True:
                print(f"SELF-TEST FAIL: {name} — predicate false")
                ok = False
            continue
        blocked = bool(scan(turn))
        if blocked != must_block:
            print(f"SELF-TEST FAIL: {name} — expected {'BLOCK' if must_block else 'PASS'}, "
                  f"got {'BLOCK' if blocked else 'PASS'}")
            ok = False
    if ok:
        print(f"SELF-TEST PASS: {len(cases)}/{len(cases)} cases "
              "(blocks unbacked/cross-subject/subjectless-running/non-probe/trailing-statement vouch "
              "+ the same claims WRITTEN TO FILES via Write/Edit/heredoc; passes backed/prose/quoted/"
              "adjectival/pipe-filter/later-statement probes, source files, skip paths)")
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

    bad = scan_turn(turn, claim_re, completion_re, measurement_re, subj_re, non_subjects, cfg=cfg)
    if not bad:
        return 0
    print(json.dumps({"decision": "block", "reason": block_message(bad, cfg["verify_hint"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
