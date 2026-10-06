#!/usr/bin/env python3
"""population_arm.py — measure a candidate guard's breadth BEFORE it lands.

guard-target-correctness covers the predicate that is too NARROW. This arm covers the
mirror direction: a guard that flags everything is never turned off in code — it is
switched off socially. Its alerts get acknowledged on reflex, then batched, then ignored,
and the end state is no guard at all. A guard that never fires and a guard that always
fires are the same defect: zero information.

So the population is measured before the invariant ships:

  1. PIN the corpus — a manifest of the files the guard will police, fixed at review time
     so the denominator cannot drift under the measurement.
  2. RUN the candidate checker over every corpus file; record flagged/scanned.
  3. FAIL the guard's own review when the flagged share exceeds a configured ceiling
     (default: half the corpus).
  4. The ratio measures BREADTH only, so the review also names the labelled positives it
     checked — a guard can be narrow and still wrong. A labelled positive the checker
     does not flag fails the review too.

CHECKER CONTRACT: the candidate guard is invoked once per file with `{}` replaced by the
path. ONE exit code is the semantic flag — exit 1, FLAG_EXIT below. Exit 0 is clean.
EVERY other exit code, and a timeout, is an ERROR, not a flag.

    Counting every nonzero as a successful flag was measured wrong in the direction that
    looks like success: a checker returning 70 only on the labelled positive — a crash on
    exactly the file it was supposed to detect — produced flagged=1 of 7, cleared the
    breadth ceiling, and cleared positive-recall. The measurement was of a checker that
    never worked. A crash, a usage error, and a timeout say nothing about the population,
    so any of them makes the whole review CANNOT CHECK; the stderr is retained per path so
    the cause is visible rather than inferred.

Exit codes: 0 review pass · 1 review FAIL (over-broad, or a labelled positive missed) ·
2 CANNOT CHECK (empty/absent corpus — a ratio over nothing proves nothing — a checker
that errored on any corpus file, or an invalid ceiling: not a finite number in [0, 1]).
Exit 2 also covers every other input review() cannot use: an unusable checker
command (not a string, or one that cannot be split into arguments, or that splits into none);
an unusable corpus or positives input (not an exact list or tuple, or a path that is not a
string); and a corpus path that cannot be passed to a process (a NUL, or a character the file
system encoding cannot encode), which is an error for that file.
Gate: guard/tests/test_population_arm.py. Red demos: PA1 disables the breadth ceiling;
PA2 counts every nonzero exit as a flag again.
"""
import argparse
import math
import os
import shlex
import subprocess
import sys
import tempfile


FLAG_EXIT = 1        # the ONE exit code that means "this file is flagged"
TIMEOUT_S = 120
STDERR_LIMIT = 200   # characters of a checker's stderr shown per errored corpus file
_NAMED_ESCAPES = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}


def _shown(text, limit=None, undecodable_bytes=False):
    r"""Outside text as ONE printable line.

    Text from outside the guard — a corpus or positive path, a
    ceiling's type name, the candidate checker's own stderr — reached the report unescaped. The
    stderr was stripped of "\n" only, so the very program under review could print "\r" or
    U+2028 followed by "review pass — ..." and forge a passing line: rc stayed right, but the
    reader of the report was deceived. A lone surrogate made the CLI's print() raise.

    Every character str.isprintable() rejects is SHOWN as a visible escape (\n, \x1b, \u2028,
    \U000e0001), never dropped, so the text on either side stays and the reader can see that
    something stood between. Printable text, non-ASCII included, passes through unchanged. With
    `limit`, the shown text is at most `limit` characters, cut only between escapes. When it is
    cut, the marker "… (N characters)" (N = the length of `text`) is appended AFTER it, so the
    return value can be longer than `limit` by the marker's length (measured: 220 characters for a
    limit of 200 and a 10000-character text). A value that is not an exact str is formatted with
    %s first, as the report always did.
    On the errored line, `text` is the checker's stderr WITHOUT its final line
    ending, decoded with one character per undecodable byte (surrogateescape), so N is the length
    of that: 1000 bytes of 0xff give "… (1000 characters)", not the 4000 characters of \xff shown.

    For a real checker process this now holds byte for byte. run_checker captures
    its stderr as bytes and decodes it itself, with no newline translation, so a "\r" arrives here
    as "\r" and is shown as \r. Each byte that is not valid UTF-8 arrives as ONE lone surrogate
    (surrogateescape: U+DC80-U+DCFF stands for byte 0x80-0xFF), and with undecodable_bytes=True it
    is shown as \xNN, its value. It is one piece, like every other escape, so the limit never
    splits it: text that already spelled "\xff" would be cut after its "\" (measured: a
    backslashreplace decode left a dangling "\" or "\x" at the 200-character cut). Before C5,
    text mode rewrote "\r" as "\n", and undecodable bytes crashed the review before any line was
    built, so this held only for the in-process stand-in.

    A byte and a character are never shown the same. \xNN above \x7f means a byte
    and nothing else. A non-printable CHARACTER U+0080-U+00FF is shown as \u00NN; it used to be
    \xNN, so the lone byte 0x85 and the character U+0085 both showed as \x85. Below 0x80 the byte
    and the character are the same thing, and \xNN names both. One ambiguity remains, by design:
    printable text passes through unchanged, so text that literally spells the four characters
    \xff looks the same as the byte 0xff.

    Nothing is dropped at the edges either. This function removes nothing. The
    ONE thing any report line removes from outside text is a checker stderr's final line ending
    ("\r\n", "\n" or "\r" — one of them, once, at the very end), which the errored line takes off
    with _without_final_line_ending() before calling this. It used to call .strip(), which also
    removed every other kind of whitespace at both edges: "\x1c" alone showed as nothing, and a
    no-break space or U+2028 around "X" showed only "X".
    """
    if type(text) is not str:
        text = "%s" % (text,)
    out, used = [], 0
    for ch in text:
        if ch.isprintable():
            piece = ch
        else:
            piece = _NAMED_ESCAPES.get(ch)
            if piece is None:
                c = ord(ch)
                if undecodable_bytes and 0xDC80 <= c <= 0xDCFF:
                    piece = "\\x%02x" % (c - 0xDC00)  # a BYTE that is not valid UTF-8
                elif c < 0x80:
                    piece = "\\x%02x" % c             # ASCII: the byte and the character agree
                elif c < 0x10000:
                    piece = "\\u%04x" % c             # a CHARACTER, U+0080-U+00FF included (C7)
                else:
                    piece = "\\U%08x" % c
        if limit is not None and used + len(piece) > limit:
            out.append("… (%d characters)" % len(text))
            break
        out.append(piece)
        used += len(piece)
    return "".join(out)


def read_list(path):
    """Manifest lines, or None when the manifest could not be read.

    An unreadable manifest is the same fact as an absent corpus: nothing was measured. It
    used to be an uncaught FileNotFoundError, so a missing --positives path exited 1 with a
    traceback while a missing --corpus correctly returned 2.

    A manifest that is not UTF-8 is the same fact. It used to escape as a
    UnicodeDecodeError traceback, because only OSError was caught. ValueError covers it, and also
    a path open() refuses outright (an embedded NUL)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]
    except (OSError, ValueError):
        return None


def run_checker(checker, path):
    r"""Returns (outcome, rc, stderr). outcome is "clean", "flagged", or "error".

    The outcome comes from the exit code alone. The checker's output is display only: stdout
    is captured and discarded, stderr is kept as text for the report.

    Output is captured as BYTES and decoded here, never by subprocess. With
    text=True, a checker that wrote bytes that are not UTF-8 made subprocess.run raise
    UnicodeDecodeError, and the CLI exited 1 with a traceback. Exit 1 is "review FAIL", so a
    crash read as a verdict. Text mode also rewrote every "\r" and "\r\n" as "\n", so the report
    showed a character the checker never wrote. Now stderr is decoded as UTF-8 with
    errors="surrogateescape": valid UTF-8 is decoded as written ("\r" stays "\r"), and each byte
    that is not part of valid UTF-8 becomes one lone surrogate U+DC80-U+DCFF (0xDC00 + its value;
    a UTF-8 decode never yields a genuine surrogate, so there is no ambiguity). The report shows
    it with _shown(..., undecodable_bytes=True) as \xNN, its value in lower-case hex. It is never
    dropped or replaced, and it is never shown the way a CHARACTER is shown: since C7, a
    non-printable character U+0080-U+00FF is \u00NN, so a \xNN above \x7f can only be a byte. It
    CAN look like printable text that literally spells "\xNN", because printable text passes
    through unchanged (by design). Undecodable output does not make the run an error; the exit
    code decides. stderr is returned whole; the errored line removes only its final line ending
    (C6). That removal is also what the errored line's cut marker counts. In
    "… (N characters)", N is the length of the stderr without its final line ending, with one
    character per undecodable byte.

    A corpus path that cannot be passed to a process (a NUL, or a character the
    file system encoding cannot encode) made subprocess.run raise ValueError, which escaped as a
    traceback with exit 1, and exit 1 reads as REVIEW FAIL. It is now an error for that file,
    like a checker that could not be started (OSError), so the review is CANNOT CHECK. A checker
    command that cannot be split into arguments is refused earlier, in review(), before any file
    runs.
    """
    cmd = [path if a == "{}" else a for a in shlex.split(checker)]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "error", None, "timed out after %ds" % TIMEOUT_S
    except (OSError, ValueError) as exc:
        return "error", None, str(exc)
    p.stderr = p.stderr.decode("utf-8", errors="surrogateescape")
    if p.returncode == 0:
        return "clean", 0, p.stderr
    return ("flagged" if p.returncode == FLAG_EXIT else "error"), p.returncode, p.stderr


def _without_final_line_ending(text):
    r"""`text` without ONE final line ending: "\r\n", "\n" or "\r", once, at the very end.

    That line ending is the only thing an errored line may remove from a checker's
    stderr. It ends the checker's last line and is not content. Everything else at either edge is
    content and is shown, escaped by _shown() when it is not printable. That includes a further
    "\n" (a blank last line), a leading "\r", and whitespace such as "\x1c", U+00A0 or U+2028.
    The errored line's cut marker, "… (N characters)", counts what this returns:
    N is the length of the stderr without its final line ending, one character per undecodable byte.
    """
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith(("\n", "\r")):
        return text[:-1]
    return text


# The diagnostic for a value that could not be validated at all. It is a
# constant on purpose — it must not format, name or otherwise touch the value or its type,
# because touching them is exactly what just raised.
UNVALIDATABLE_CEILING = ("the ceiling could not be validated — examining it raised an "
                         "exception, so it is not a usable finite number in [0, 1]")

# A type's name, read past its metaclass. `type(x).__name__` goes through the
# metaclass, which can raise, return a non-str, or return 10000 characters. This descriptor reads
# the name the type actually carries (a str, or a str subclass that was assigned to it);
# str.__str__ copies that into an exact str without running any override, and it is then bounded.
_TYPE_NAME = type.__dict__["__name__"]
TYPE_NAME_LIMIT = 80


def _type_name(kind):
    # Shown as one printable line, and bounded AFTER escaping — an escape is up to
    # ten characters, so bounding the raw name alone would let 80 control characters become 800.
    return _shown(str.__str__(_TYPE_NAME.__get__(kind)), TYPE_NAME_LIMIT)


def invalid_ceiling(max_share):
    """Why `max_share` cannot be a breadth ceiling, or None when it is a valid one.

    Contract: The ceiling must be a finite real number in [0, 1] — a share cannot
    exceed 1 or fall below 0, and `share > nan` is False for every share, so NaN, ±inf, -1 and
    1.1 each used to silently pass (or, for a negative ceiling, misread every flag as an
    over-broad candidate). An invalid ceiling is a configuration error: CANNOT CHECK, decided
    before the candidate runs on a single file. bool is excluded — True is not a ceiling.

    This NEVER raises, for any value. Anything that still raises while
    validating is refused with a constant diagnostic. Exception, not BaseException — an
    interrupt still stops.

    A ceiling is judged by what it IS, never by what its own methods answer. See
    _judge_ceiling().
    """
    return _ceiling(max_share)[1]


def _ceiling(max_share):
    """(plain, problem). Never raises. When problem is None, plain is the exact int or float the
    ceiling holds — the value review() compares and prints. Otherwise plain is None."""
    try:
        plain, problem = _judge_ceiling(max_share)
    except Exception:  # noqa: BLE001 — any failure to validate is a refusal, never a traceback
        return None, UNVALIDATABLE_CEILING
    return (None, problem) if problem is not None else (plain, None)


def _judge_ceiling(max_share):
    """(plain, problem); may raise. Call _ceiling() or invalid_ceiling() instead.

    Refusing the values whose methods RAISE cannot see a value whose methods LIE.
    A float subclass holding 0.5 whose `__lt__` answers as if it were 2.0 makes `share >
    max_share` False for every share, so an over-broad candidate passed its review — the guard
    failed open, silently. So nothing here asks the value anything:
      - the type comes from type(), never isinstance() or __class__, which a value can claim;
      - issubclass() is called with the exact built-in as the second argument, so it walks the
        type's real MRO and consults no hook;
      - the number comes from the BASE type's own method (float.__float__, int.__index__), which
        returns an exact float or int without running any override;
      - the plain number is then validated, compared and printed exactly as a plain one would be.
    An int or float subclass is therefore judged exactly as the plain number it holds, and a
    value that only claims a numeric type through __class__ is not a number.
    """
    kind = type(max_share)
    if issubclass(kind, bool):
        # bool cannot be subclassed, so the value is one of the two singletons; `is` asks it nothing.
        return None, "the ceiling must be a number, got %s (bool)" % (
            "True" if max_share is True else "False")
    if issubclass(kind, float):
        plain = float.__float__(max_share)
    elif issubclass(kind, int):
        plain = int.__index__(max_share)
    else:
        # Describe what arrived WITHOUT formatting it — %r of a list holding a
        # 5000-digit int raises the same ValueError the int branch below avoids.
        # And without touching it at all. len(range(10**5000)) raises
        # OverflowError, and any operation on an arbitrary value (len, repr, str, format, iter,
        # truthiness, comparison, hashing) can raise. The diagnostic is built from the type's
        # name alone — read past any metaclass and bounded (C3).
        return None, "the ceiling must be a number, got a %s" % _type_name(kind)
    return plain, _plain_ceiling_problem(plain)


def _plain_ceiling_problem(plain):
    """The range check on an EXACT int or float (never a subclass; _judge_ceiling made it plain)."""
    if type(plain) is int:
        # Compare as an int. math.isfinite(10**400) raises OverflowError ("int too
        # large to convert to float"), which escaped review() as a traceback instead of CANNOT CHECK.
        # Never format the value in decimal — str(10**5000) raises ValueError under
        # the interpreter's default 4300-digit limit. Print it only when it is small; otherwise
        # describe it by sign and bit length, which is bounded.
        if not 0 <= plain <= 1:
            if plain.bit_length() <= 64:
                shown = "%d" % plain
            else:
                shown = "a %s integer of %d bits" % (
                    "negative" if plain < 0 else "positive", plain.bit_length())
            return "the ceiling must be within [0, 1]; got %s" % shown
        return None
    if not math.isfinite(plain):
        return "the ceiling must be a finite number in [0, 1]; got %r" % plain
    if not 0 <= plain <= 1:
        return "the ceiling must be within [0, 1]; got %r" % plain
    return None


def _checker_text(checker):
    """(the command as an exact str, None), or (None, why it cannot be run at all).

    shlex.split raised ValueError out of run_checker on an unbalanced quote or a
    trailing backslash, and the CLI exited 1 with a traceback. Exit 1 reads as REVIEW FAIL. A
    command that splits into no arguments (" ") made subprocess.run raise IndexError the same
    way. Like an invalid ceiling, each is a configuration error, refused as CANNOT CHECK before
    the candidate runs on any file.

    The command is judged by the text it STORES. Its real type, type(checker), not
    a __class__ that claims str, must be str or a str subclass. The command used from here on is
    str.__str__(checker), an exact copy, so no method of a subclass (split, iteration, ==) ever
    runs. An object whose __class__ claimed str used to pass isinstance() and then made
    shlex.split raise."""
    if not issubclass(type(checker), str):
        return None, "the checker command is a %s, not a string" % _type_name(type(checker))
    text = str.__str__(checker)
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        return None, "the checker command cannot be split into arguments (%s)" % _shown(str(exc))
    if not argv:
        return None, "the checker command is empty"
    return text, None


def _path_texts(values, what, each):
    """(a list of the paths as exact strs, None), or (None, why `values` cannot be used).

    Corpus and positives are judged by what they STORE. `values` must be an EXACT
    list or tuple. A subclass is refused, because its iteration can yield what it does not store.
    Every element's real type must be str or a str subclass, and it is used as str.__str__(p), an
    exact copy. So no ==, hash(), iteration or display method of a subclass decides which file is
    checked, whether it was flagged, or whether a positive was caught. A missed positive whose
    == and hash() claimed a flagged path used to be counted as caught, and the review PASSED. An
    element that is not a str (None, an int, a list, an object whose __class__ claims str) used to
    raise TypeError out of review()."""
    if type(values) is not list and type(values) is not tuple:
        return None, ("the %s is a %s, not a list or tuple of paths"
                      % (what, _type_name(type(values))))
    texts = []
    for i, p in enumerate(values):        # an exact list or tuple: iterating it runs no other code
        if not issubclass(type(p), str):
            return None, "%s #%d is a %s, not a string" % (each, i + 1, _type_name(type(p)))
        texts.append(str.__str__(p))
    return texts, None


def review(corpus, checker, max_share, positives):
    """Returns (rc, lines). rc: 0 pass · 1 fail · 2 cannot check. It never raises.

    Every line is ONE printable line (C4): outside text — paths, the checker's stderr, a type
    name — goes through _shown() where it is formatted. Only the display is escaped; the raw
    paths are what the checker runs on and what is matched.

    The argument contract:
      - max_share: judged as the plain number it holds (see _judge_ceiling);
      - checker: a str or a str subclass, judged by the text it stores (str.__str__), which must
        split into at least one argument (see _checker_text);
      - corpus: an EXACT list or tuple (not a subclass) of paths, each a str or a str subclass,
        judged by the text it stores (see _path_texts). None or an empty one is the empty corpus;
      - positives: the same, except that None is not accepted.
    Anything unusable is rc 2, "CANNOT CHECK — invalid configuration: ...", before the candidate
    runs on any file. A corpus path that cannot be passed to a process (a NUL, or a character the
    file system encoding cannot encode) is an error for that file, so the review is CANNOT CHECK.

    An errored file's line is "  <path> -> exit <rc>  <stderr>". The stderr is shown by _shown()
    without its final line ending: at most 200 shown characters (STDERR_LIMIT), cut only between
    escapes. When cut, "… (N characters)" follows, where N is the length of the stderr without its
    final line ending, with one character per undecodable byte."""
    lines = []
    # From here on the ceiling IS the plain number it holds. The parameter is
    # rebound, not shadowed, so the breadth comparison and the report line below read the plain
    # value, and they stay the exact lines the mutation harness anchors on (PA1).
    max_share, bad = _ceiling(max_share)
    if bad is not None:
        return 2, ["CANNOT CHECK — invalid configuration: %s. The candidate was not run; a "
                   "breadth ratio against an impossible ceiling measures nothing." % bad]
    # The checker and every path are rebound to exact-str copies of the text they
    # store, so nothing below (shlex, the outcome dict, the flagged set, the display) runs a
    # method of a caller's object.
    checker, bad = _checker_text(checker)
    if bad is None:
        corpus, bad = _path_texts([] if corpus is None else corpus, "corpus", "corpus path")
    if bad is None:
        positives, bad = _path_texts(positives, "positives", "positive")
    if bad is not None:
        return 2, ["CANNOT CHECK — invalid configuration: %s. The candidate was not run on any "
                   "corpus file." % bad]
    if not corpus:
        return 2, ["CANNOT CHECK — the pinned corpus is empty; a ratio over nothing proves nothing"]
    outcomes = {p: run_checker(checker, p) for p in corpus}
    errored = [(p, rc, err) for p, (o, rc, err) in outcomes.items() if o == "error"]
    if errored:
        lines.append("CANNOT CHECK — the candidate did not answer the question on %d of %d "
                     "corpus file(s). Exit %d is the flag; a crash, a usage error, or a "
                     "timeout is not a detection, and a breadth ratio built out of them "
                     "measures nothing." % (len(errored), len(corpus), FLAG_EXIT))
        for path, rc, err in errored:
            lines.append("  %s -> exit %s  %s" % (_shown(path), rc,
                                                    _shown(_without_final_line_ending(err or ""),
                                                           STDERR_LIMIT, undecodable_bytes=True)))
        return 2, lines
    flagged = [p for p in corpus if outcomes[p][0] == "flagged"]
    share = len(flagged) / len(corpus)
    lines.append("flagged %d / scanned %d (share %.2f, ceiling %.2f)"
                 % (len(flagged), len(corpus), share, max_share))
    rc = 0
    if share > max_share:
        rc = 1
        lines.append("REVIEW FAIL — over-broad: the candidate flags more than the configured "
                     "share of the pinned corpus. A guard that fires on everything is switched "
                     "off socially; the end state is no guard.")
    flagged_set = set(flagged)
    missed = [p for p in positives if p not in flagged_set]
    for p in positives:
        lines.append("labelled positive checked: %s -> %s"
                     % (_shown(p), "flagged" if p in flagged_set else "MISSED"))
    if missed:
        rc = 1
        lines.append("REVIEW FAIL — %d labelled positive(s) not flagged; a breadth pass "
                     "without its positives caught is not a pass." % len(missed))
    if rc == 0:
        lines.append("review pass — breadth under the ceiling and every labelled positive caught")
    return rc, lines


def selftest():
    """Teeth: an over-broad candidate and a positive-missing candidate must both FAIL the
    review; an exact candidate must pass. Exercised against a throwaway corpus."""
    failures = []
    with tempfile.TemporaryDirectory() as d:
        corpus = []
        for i in range(6):
            p = os.path.join(d, "f%d.txt" % i)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("PLANTED-DEFECT\n" if i == 0 else "clean line %d\n" % i)
            corpus.append(p)
        positives = [corpus[0]]
        exact = (sys.executable + " -c \"import sys;"
                 "sys.exit(1 if 'PLANTED-DEFECT' in open(sys.argv[1]).read() else 0)\" {}")
        flag_all = sys.executable + " -c \"import sys; sys.exit(1)\" {}"
        flag_none = sys.executable + " -c \"import sys; sys.exit(0)\" {}"
        rc, _ = review(corpus, exact, 0.5, positives)
        if rc != 0:
            failures.append("exact candidate must pass the review (got rc=%d)" % rc)
        rc, _ = review(corpus, flag_all, 0.5, positives)
        if rc != 1:
            failures.append("an over-broad candidate must FAIL the review (got rc=%d)" % rc)
        rc, _ = review(corpus, flag_none, 0.5, positives)
        if rc != 1:
            failures.append("a candidate missing its labelled positive must FAIL (got rc=%d)" % rc)
        rc, _ = review([], exact, 0.5, [])
        if rc != 2:
            failures.append("an empty corpus must be CANNOT CHECK, never a pass (got rc=%d)" % rc)
        crash_on_positive = (sys.executable + " -c \"import sys;"
                             "sys.exit(70 if 'PLANTED-DEFECT' in open(sys.argv[1]).read() "
                             "else 0)\" {}")
        rc, out = review(corpus, crash_on_positive, 0.5, positives)
        if rc != 2 or not any("CANNOT CHECK" in ln for ln in out):
            failures.append("a checker that CRASHES on the labelled positive must be "
                            "CANNOT CHECK, never a pass (got rc=%d %r)" % (rc, out))
        if read_list(os.path.join(d, "no-such-manifest.txt")) is not None:
            failures.append("an unreadable manifest must report nothing measured, not raise")
    for f in failures:
        print("  FAIL  %s" % f)
    if failures:
        print("population arm selftest: %d check(s) RED" % len(failures))
        return 1
    print("population arm selftest: over-broad FAILS, missed-positive FAILS, exact passes, "
          "empty corpus and a crashing checker are CANNOT CHECK")
    return 0


def _join_ceiling_value(argv):
    """Rewrite `--max-share <v>` as `--max-share=<v>` so a value that LOOKS like an option
    (-inf, -1) reaches review() and is refused there as CANNOT CHECK, instead of argparse
    reporting "expected one argument". CLI and direct-call behaviour must agree (A3)."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--max-share" and i + 1 < len(argv):
            out.append("--max-share=" + argv[i + 1])
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", help="manifest: one corpus path per line (# comments) — PINNED")
    ap.add_argument("--checker", help="candidate command; {} is replaced by each corpus path")
    ap.add_argument("--max-share", type=float, default=0.5,
                    help="ceiling on flagged/scanned (default 0.5)")
    ap.add_argument("--positives", help="manifest of labelled-positive paths the checker must flag")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(_join_ceiling_value(sys.argv[1:]))
    if args.selftest:
        return selftest()
    if not args.corpus or not args.checker:
        print("CANNOT CHECK — --corpus and --checker are required (or run --selftest)")
        return 2
    corpus = read_list(args.corpus)
    if corpus is None:
        print("CANNOT CHECK — corpus manifest could not be read: %s" % _shown(args.corpus))
        return 2
    positives = []
    if args.positives:
        positives = read_list(args.positives)
        if positives is None:
            print("CANNOT CHECK — positives manifest could not be read: %s" % _shown(args.positives))
            return 2
    rc, lines = review(corpus, args.checker, args.max_share, positives)
    for ln in lines:
        print(ln)
    return rc


if __name__ == "__main__":
    sys.exit(main())
