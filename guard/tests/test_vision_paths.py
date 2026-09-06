"""Archive configuration must refuse before touching primary files."""
import importlib
from unittest.mock import Mock

import pytest
import vision_ingest as vi


def test_archive_unconfigured_no_io(monkeypatch, capsys):
    for key in ('VI_BACKUP_ROOT', 'VI_BACKUP_MOUNT', 'VI_ALERT_SCRIPT'):
        monkeypatch.delenv(key, raising=False)
    importlib.reload(vi)
    with monkeypatch.context() as io:
        spy = Mock(side_effect=AssertionError('unexpected source I/O'))
        for name in ('os.path.ismount', 'os.path.realpath', 'os.makedirs', 'glob.glob', 'shutil.copy2'):
            io.setattr(name, spy)
        vi.archive('fixture/source')
        spy.assert_not_called()
    assert 'not configured' in capsys.readouterr().out
    assert vi._configured_path('VI_ALERT_SCRIPT') is None


def test_archive_mount_and_hash_control(monkeypatch, tmp_path):
    primary = tmp_path / 'source'
    keeps = primary / 'vision' / 'keeps'
    keeps.mkdir(parents=True)
    image = keeps / 'fixture.jpg'
    image.write_bytes(b'fixture image')
    mount = tmp_path / 'backup'
    monkeypatch.setattr(vi, 'BACKUP_MOUNT', str(mount))
    monkeypatch.setattr(vi, 'BACKUP_ROOT', str(mount / 'archive'))
    monkeypatch.setattr(vi.os.path, 'ismount', lambda p: False)
    vi.archive(str(primary))
    assert image.exists() and not mount.exists()
    monkeypatch.setattr(vi.os.path, 'ismount', lambda p: p == str(mount))
    vi.archive(str(primary))
    assert not image.exists()
    assert (mount / 'archive' / 'source' / 'companions' / image.name).read_bytes() == b'fixture image'
    # Corrupt the read-back: a failed hash must retain the primary bytes.
    keeps.mkdir()
    image.write_bytes(b'primary retained')
    monkeypatch.setattr(vi.shutil, 'copy2', lambda src, dst: __import__('pathlib').Path(dst).write_bytes(b'corrupt'))
    vi.archive(str(primary))
    assert image.read_bytes() == b'primary retained'


def test_archive_root_outside_mount_refused(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vi, 'BACKUP_ROOT', str(tmp_path / 'elsewhere'))
    monkeypatch.setattr(vi, 'BACKUP_MOUNT', str(tmp_path / 'backup'))
    vi.archive('fixture/source')
    assert 'outside configured mount' in capsys.readouterr().out
    assert not (tmp_path / 'elsewhere').exists()
