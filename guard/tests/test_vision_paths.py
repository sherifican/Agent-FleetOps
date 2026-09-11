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


def _staged(tmp_path, monkeypatch, payload=b'fixture image'):
    """A configured, mounted archive with one companion waiting on the primary."""
    primary = tmp_path / 'source'
    keeps = primary / 'vision' / 'keeps'
    keeps.mkdir(parents=True)
    image = keeps / 'fixture.jpg'
    image.write_bytes(payload)
    mount = tmp_path / 'backup'
    monkeypatch.setattr(vi, 'BACKUP_MOUNT', str(mount))
    monkeypatch.setattr(vi, 'BACKUP_ROOT', str(mount / 'archive'))
    monkeypatch.setattr(vi.os.path, 'ismount', lambda p: p == str(mount))
    return primary, image, mount


def test_archive_refuses_a_linked_companions_directory(monkeypatch, tmp_path):
    """The destination is built by appending to a checked root, and the appended parts are not checked.

    The mount predicate is satisfied and the hash read-back passes, because the read-back follows the
    same link the write did. So the bytes land outside the configured mount and the primary is then
    DELETED: verified means "the destination holds these bytes", never "they reached the backup drive".
    The control arm runs first so this cannot pass by the archive declining to do anything at all.
    """
    primary, image, mount = _staged(tmp_path, monkeypatch)
    vi.archive(str(primary))
    assert not image.exists(), 'CONTROL: an ordinary run archives and clears the primary'
    assert (mount / 'archive' / 'source' / 'companions' / 'fixture.jpg').exists(), 'CONTROL: it landed'

    # Same fixture again, but the companions directory is a link off the mount.
    keeps = primary / 'vision' / 'keeps'
    keeps.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b'the only copy')
    outside = tmp_path / 'not_the_backup'
    outside.mkdir()
    linked = mount / 'archive' / 'source' / 'companions'
    for stale in linked.iterdir():
        stale.unlink()
    linked.rmdir()
    linked.symlink_to(outside, target_is_directory=True)

    vi.archive(str(primary))

    assert image.exists(), \
        'the primary must not be deleted when the destination is a link off the configured mount'
    assert image.read_bytes() == b'the only copy', 'and it must be untouched'
    assert not (outside / 'fixture.jpg').exists(), \
        'and nothing may be written through that link'


def test_archive_refuses_a_linked_image_destination(monkeypatch, tmp_path):
    """A linked destination LEAF is the same hazard one level down: copy2 follows it."""
    primary, image, mount = _staged(tmp_path, monkeypatch, payload=b'the only copy')
    victim = tmp_path / 'victim.bin'
    victim.write_bytes(b'SOMEONE ELSE FILE')
    dest_dir = mount / 'archive' / 'source' / 'companions'
    dest_dir.mkdir(parents=True)
    (dest_dir / 'fixture.jpg').symlink_to(victim)

    vi.archive(str(primary))

    assert victim.read_bytes() == b'SOMEONE ELSE FILE', 'a linked leaf must not be written through'
    assert image.read_bytes() == b'the only copy', 'and the primary must survive'
