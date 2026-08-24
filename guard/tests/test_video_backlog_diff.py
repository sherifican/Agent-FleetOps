import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from video_backlog_diff import main  # noqa: E402


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_overlap_is_removed(tmp_path, capsys):
    known = write(tmp_path / "known.txt", "dQw4w9WgXcQ\n")
    source = write(tmp_path / "source.txt", "dQw4w9WgXcQ|Known\noHg5SJYRHA0|New\n")
    assert main(["--known", known, "--source", source]) == 0
    assert capsys.readouterr().out == "oHg5SJYRHA0|New\n"


def test_empty_known_passes_every_source_entry(tmp_path, capsys):
    known = write(tmp_path / "known.txt", "")
    source = write(tmp_path / "source.txt", "dQw4w9WgXcQ|One\noHg5SJYRHA0|Two\n")
    assert main(["--known", known, "--source", source]) == 0
    assert capsys.readouterr().out == "dQw4w9WgXcQ|One\noHg5SJYRHA0|Two\n"


def test_empty_source_yields_nothing(tmp_path, capsys):
    known = write(tmp_path / "known.txt", "dQw4w9WgXcQ\n")
    source = write(tmp_path / "source.txt", "")
    assert main(["--known", known, "--source", source]) == 0
    assert capsys.readouterr().out == ""


def test_normalizes_and_deduplicates_supported_forms(tmp_path, capsys):
    known = write(tmp_path / "known.txt", "")
    source = write(tmp_path / "source.txt", "https://youtu.be/dQw4w9WgXcQ|Short\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ|Watch\ndQw4w9WgXcQ|Bare\n")
    assert main(["--known", known, "--source", source]) == 0
    assert capsys.readouterr().out == "dQw4w9WgXcQ|Short\n"


def test_missing_file_returns_one(tmp_path, capsys):
    source = write(tmp_path / "source.txt", "dQw4w9WgXcQ|One\n")
    assert main(["--known", str(tmp_path / "missing.txt"), "--source", source]) == 1
    assert "error: missing input file:" in capsys.readouterr().err
