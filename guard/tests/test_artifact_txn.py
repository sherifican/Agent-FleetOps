"""Claude-authored gate for guard/artifact_txn.py. The implementer must NOT edit this file."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guard.artifact_txn import Transaction, TransactionError  # noqa: E402

OLD = "<html>original hub</html>\n"
NEW = "<html>spliced hub</html>\n"


def live(tmp_path, name="hub.html", content=OLD):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_commit_replaces_content_and_keeps_a_prev(tmp_path):
    p = live(tmp_path)
    with Transaction() as t:
        t.stage(p, NEW)
        t.commit()
    assert open(p).read() == NEW
    assert open(p + ".prev").read() == OLD, "the one-step history the hub never had"


def test_staging_alone_never_touches_the_live_file(tmp_path):
    p = live(tmp_path)
    t = Transaction()
    t.stage(p, NEW)
    assert open(p).read() == OLD
    assert os.path.exists(p + ".dl.tmp")


def test_tmp_is_removed_after_commit(tmp_path):
    p = live(tmp_path)
    with Transaction() as t:
        t.stage(p, NEW)
        t.commit()
    assert not os.path.exists(p + ".dl.tmp")


def test_verify_runs_against_the_staged_copy_not_the_live_file(tmp_path):
    p = live(tmp_path)
    seen = []
    t = Transaction()
    t.stage(p, NEW)
    t.verify(p, lambda c: seen.append(c) or True)
    assert seen == [NEW], "validator must receive the STAGED content"


def test_failed_verification_leaves_the_live_file_untouched(tmp_path):
    p = live(tmp_path)
    t = Transaction()
    t.stage(p, NEW)
    with pytest.raises(TransactionError):
        t.verify(p, lambda c: "card count dropped")
    assert open(p).read() == OLD
    assert not os.path.exists(p + ".prev"), "nothing was committed, so nothing should be archived"


def test_validator_returning_false_fails(tmp_path):
    p = live(tmp_path)
    t = Transaction()
    t.stage(p, NEW)
    with pytest.raises(TransactionError):
        t.verify(p, lambda c: False)


def test_validator_raising_is_a_verification_failure_not_a_crash(tmp_path):
    p = live(tmp_path)
    t = Transaction()
    t.stage(p, NEW)
    with pytest.raises(TransactionError):
        t.verify(p, lambda c: 1 / 0)


def test_verifying_an_unstaged_path_is_an_error_not_a_silent_pass(tmp_path):
    p = live(tmp_path)
    t = Transaction()
    with pytest.raises(TransactionError):
        t.verify(p, lambda c: True)


def test_multi_file_commit_rolls_back_every_earlier_file_on_failure(tmp_path):
    a = live(tmp_path, "a.html", "AAA\n")
    b = live(tmp_path, "b.html", "BBB\n")
    t = Transaction()
    t.stage(a, "new-a\n")
    t.stage(b, "new-b\n")
    os.remove(b + ".dl.tmp")  # sabotage the second commit mid-flight
    with pytest.raises(TransactionError):
        t.commit()
    assert open(a).read() == "AAA\n", "the first file must be restored when the second fails"
    assert open(b).read() == "BBB\n"


def test_exception_inside_the_context_rolls_back(tmp_path):
    p = live(tmp_path)
    with pytest.raises(RuntimeError):
        with Transaction() as t:
            t.stage(p, NEW)
            t.commit()
            raise RuntimeError("something later blew up")
    assert open(p).read() == OLD, "a commit followed by a failure must not survive"


def test_context_exit_does_not_swallow_the_exception(tmp_path):
    p = live(tmp_path)
    with pytest.raises(ValueError):
        with Transaction() as t:
            t.stage(p, NEW)
            raise ValueError("boom")


def test_leaving_the_block_without_committing_cleans_up(tmp_path):
    p = live(tmp_path)
    with Transaction() as t:
        t.stage(p, NEW)
    assert open(p).read() == OLD
    assert not os.path.exists(p + ".dl.tmp"), "staged files must not be left lying around"


def test_rollback_deletes_a_file_that_did_not_exist_before(tmp_path):
    p = str(tmp_path / "brand-new.html")
    t = Transaction()
    t.stage(p, NEW)
    t.commit()
    t.rollback()
    assert not os.path.exists(p), "a file with no .prev must be removed, not left behind"


def test_rollback_is_idempotent(tmp_path):
    p = live(tmp_path)
    t = Transaction()
    t.stage(p, NEW)
    t.commit()
    t.rollback()
    t.rollback()
    assert open(p).read() == OLD


def test_commit_returns_paths_in_deterministic_order(tmp_path):
    paths = [live(tmp_path, f"{n}.html") for n in ("c", "a", "b")]
    t = Transaction()
    for p in paths:
        t.stage(p, NEW)
    assert t.commit() == sorted(paths)


def test_line_endings_survive_round_trip(tmp_path):
    """A tool that silently rewrites line endings once cost a sibling system a false regression hunt."""
    p = str(tmp_path / "crlf.html")
    body = "line1\r\nline2\r\n"
    with Transaction() as t:
        t.stage(p, body)
        t.commit()
    assert open(p, newline="").read() == body


def test_live_file_is_never_opened_for_writing_directly(tmp_path, monkeypatch):
    """Only os.replace may touch the live path — anything else can expose a partial file to a reader."""
    p = live(tmp_path)
    import builtins
    real_open = builtins.open
    bad = []

    def spy(path, mode="r", *a, **k):
        if str(path) == p and any(w in str(mode) for w in ("w", "a", "+", "x")):
            bad.append(str(mode))
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    with Transaction() as t:
        t.stage(p, NEW)
        t.commit()
    assert not bad, f"live path opened for writing with mode(s) {bad}"


def test_missing_parent_dir_fails_before_risking_anything(tmp_path):
    p = str(tmp_path / "nope" / "deep.html")
    t = Transaction()
    with pytest.raises(TransactionError):
        t.stage(p, NEW)


def test_realistic_hub_splice_with_a_card_count_validator(tmp_path):
    """The actual use: refuse a splice that loses cards."""
    hub = live(tmp_path, "hub.html", '<section class="report"></section>\n' * 5)

    def not_fewer_cards(content):
        before = open(hub).read().count('class="report"')
        got = content.count('class="report"')
        return True if got >= before else f"card count fell {before} -> {got}"

    t = Transaction()
    t.stage(hub, '<section class="report"></section>\n' * 3)
    with pytest.raises(TransactionError):
        t.verify(hub, not_fewer_cards)
    assert open(hub).read().count('class="report"') == 5, "the lossy splice must never land"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
