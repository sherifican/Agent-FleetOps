"""Claude-authored gate for Wave 5 — WinClaude passback inbox source.
Pure headless reader (glob + mtime + a small seen-state JSON); no textual. Never raises."""
import os
import json
import time
from fleet_tui.sources import passback


def _mk(path, text, mtime):
    with open(path, "w") as f:
        f.write(text)
    os.utime(path, (mtime, mtime))


def _wire(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"; docs.mkdir()
    pc = tmp_path / "pc-passback"; pc.mkdir()
    seen = tmp_path / "seen.json"
    monkeypatch.setattr(passback, "DOCS_GLOB", str(docs / "PASSBACK_*.md"))
    monkeypatch.setattr(passback, "PC_GLOB", str(pc / "PC_CLAUDE_*.md"))
    monkeypatch.setattr(passback, "SEEN_FILE", str(seen))
    return docs, pc, seen


def test_newest_first_and_title_extraction(tmp_path, monkeypatch):
    docs, pc, _ = _wire(tmp_path, monkeypatch)
    now = time.time()
    _mk(docs / "PASSBACK_a.md", "# Title A heading\n\nbody", now - 300)
    _mk(pc / "PC_CLAUDE_b.md", "\n\nfirst non-empty line B\n", now - 100)   # no heading → first non-empty
    _mk(docs / "PASSBACK_c.md", "# Title C\n", now - 50)                    # newest
    items = passback.list_passback()
    assert [i["title"] for i in items] == ["Title C", "first non-empty line B", "Title A heading"]
    assert all("path" in i and "age" in i and "new" in i for i in items)


def test_new_flag_and_ack(tmp_path, monkeypatch):
    docs, pc, seen = _wire(tmp_path, monkeypatch)
    now = time.time()
    _mk(docs / "PASSBACK_x.md", "# X", now - 10)
    items = passback.list_passback()
    assert items[0]["new"] is True                 # never seen → new
    passback.mark_seen(items[0]["path"])           # ack it
    assert passback.list_passback()[0]["new"] is False
    # modifying the file after it was seen → new again
    _mk(docs / "PASSBACK_x.md", "# X v2", now + 100)
    assert passback.list_passback()[0]["new"] is True


def test_mark_all_seen(tmp_path, monkeypatch):
    docs, pc, _ = _wire(tmp_path, monkeypatch)
    now = time.time()
    _mk(docs / "PASSBACK_1.md", "# one", now - 20)
    _mk(pc / "PC_CLAUDE_2.md", "# two", now - 10)
    assert passback.new_count() == 2
    passback.mark_all_seen()
    assert passback.new_count() == 0
    assert len(passback.list_passback()) == 2      # still visible, just not "new"


def test_missing_dirs_and_corrupt_seen_never_raise(tmp_path, monkeypatch):
    # globs point at nonexistent dirs; seen file is corrupt
    monkeypatch.setattr(passback, "DOCS_GLOB", str(tmp_path / "nope" / "PASSBACK_*.md"))
    monkeypatch.setattr(passback, "PC_GLOB", str(tmp_path / "nope2" / "PC_CLAUDE_*.md"))
    seen = tmp_path / "corrupt.json"; seen.write_text("{not json")
    monkeypatch.setattr(passback, "SEEN_FILE", str(seen))
    assert passback.list_passback() == []
    assert passback.new_count() == 0
    passback.mark_all_seen()                        # must not raise even with nothing to mark
