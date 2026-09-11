"""Archive configuration must refuse before touching primary files."""
import importlib
import os
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
    """A linked destination LEAF is the same hazard one level down: copy2 follows it.

    The control arm runs first, for the same reason the directory arm carries one: without it every
    assertion below is satisfied by an archive that does nothing at all, and a refusal that is really
    a no-op reads exactly like a refusal that is really a check.
    """
    primary, image, mount = _staged(tmp_path, monkeypatch)
    vi.archive(str(primary))
    assert not image.exists(), 'CONTROL: an ordinary run archives and clears the primary'
    landed = mount / 'archive' / 'source' / 'companions' / 'fixture.jpg'
    assert landed.exists(), 'CONTROL: it landed'

    # Same fixture again, but now the destination LEAF itself is a link onto someone else's file.
    keeps = primary / 'vision' / 'keeps'
    keeps.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b'the only copy')
    victim = tmp_path / 'victim.bin'
    victim.write_bytes(b'SOMEONE ELSE FILE')
    landed.unlink()
    landed.symlink_to(victim)

    vi.archive(str(primary))

    assert victim.read_bytes() == b'SOMEONE ELSE FILE', 'a linked leaf must not be written through'
    assert image.exists(), 'the primary must not be deleted when its destination was refused'
    assert image.read_bytes() == b'the only copy', 'and the primary must survive'


def test_archive_refuses_a_linked_companions_json_destination(monkeypatch, tmp_path):
    """The metadata copy is the fifth destination, and it had no arm of its own.

    The source passes an explicit leaf here rather than a directory, so the same walk that catches a
    linked image leaf catches this one. That is an argument from reading the code, and a guard nobody
    exercises is a guard nobody knows the state of: the previous review round named this path and
    there was nothing to point at. This arm points at it.
    """
    primary, image, mount = _staged(tmp_path, monkeypatch)
    manifest = primary / 'vision' / 'companions.json'
    manifest.write_text('[{"tc": "00:00:01"}]', encoding='utf-8')
    vi.archive(str(primary))
    assert not image.exists(), 'CONTROL: an ordinary run archives and clears the primary'
    meta = mount / 'archive' / 'source' / 'companions.json'
    assert meta.is_file(), 'CONTROL: the metadata copy landed as a real file'

    # Same fixture again, with the metadata destination pointing at someone else's file.
    keeps = primary / 'vision' / 'keeps'
    keeps.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b'the only copy')
    manifest.write_text('[{"tc": "00:00:01"}]', encoding='utf-8')
    victim = tmp_path / 'victim.json'
    victim.write_text('{"someone else": true}', encoding='utf-8')
    meta.unlink()
    meta.symlink_to(victim)

    vi.archive(str(primary))

    assert victim.read_text(encoding='utf-8') == '{"someone else": true}', \
        'the metadata copy must not be written through a linked destination'


def test_archive_refuses_a_linked_synthesis_destination(monkeypatch, tmp_path):
    """The round-1 blocker itself, which the repair shipped WITHOUT an arm of its own.

    A reviewer demonstrated the gap by reverting the one-line join back to the directory form and
    running the suite: it stayed green, so the headline fix of that commit was pinned by nothing.
    That is precisely the state the same commit message calls out as unacceptable for the metadata
    path, which makes leaving it here indefensible rather than merely untidy.

    The defect is that shutil.copy2 accepts a DIRECTORY and chooses the leaf itself from the
    source's basename, so checking the directory says nothing about the file that gets written.
    This arm plants the link on that leaf.
    """
    primary, image, mount = _staged(tmp_path, monkeypatch)
    syn = primary / 'vision' / 'SYNTHESIS_run.md'
    syn.write_text('the synthesis\n', encoding='utf-8')
    vi.archive(str(primary))
    assert not image.exists(), 'CONTROL: an ordinary run archives and clears the primary'
    landed = mount / 'archive' / 'source' / 'SYNTHESIS_run.md'
    assert landed.is_file() and landed.read_text(encoding='utf-8') == 'the synthesis\n', \
        'CONTROL: the synthesis copy landed as a real file, so the leaf path is the one under test'

    # Same fixture again, with the synthesis LEAF replaced by a link onto someone else's file.
    keeps = primary / 'vision' / 'keeps'
    keeps.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b'the only copy')
    syn.write_text('a second synthesis\n', encoding='utf-8')
    victim = tmp_path / 'victim.md'
    victim.write_text('SOMEONE ELSE FILE', encoding='utf-8')
    landed.unlink()
    landed.symlink_to(victim)

    vi.archive(str(primary))

    assert victim.read_text(encoding='utf-8') == 'SOMEONE ELSE FILE', \
        'the synthesis copy must not be written through a linked destination leaf'


def test_a_directory_named_like_the_file_is_not_a_usable_destination(monkeypatch, tmp_path):
    """Round-2 review, F1: checking the leaf is not enough while copy2 can reinterpret it.

    shutil.copy2 appends the source basename to ANY existing directory it is handed. Naming the leaf
    explicitly moved the problem one level down instead of removing it: if a DIRECTORY sits at the
    checked leaf path, copy2 appends the basename again and writes inside it, following a symlink
    there that containment never visited.

    This is a REGRESSION the leaf fix introduced, not a pre-existing hole. Before it, the same state
    raised IsADirectoryError and the victim survived; after it, the copy returned normally and the
    victim outside the mount held the payload. Measured on both revisions.
    """
    primary, image, mount = _staged(tmp_path, monkeypatch)
    syn = primary / 'vision' / 'SYNTHESIS_notes.md'
    syn.write_text('SYNTHESIS PAYLOAD', encoding='utf-8')
    (primary / 'vision' / 'companions.json').write_text('[{"tc": "00:00:01"}]', encoding='utf-8')

    outside = tmp_path / 'outside'
    outside.mkdir()
    victims = {}
    for name in ('SYNTHESIS_notes.md', 'companions.json'):
        victim = outside / f'victim_{name}'
        victim.write_text('ORIGINAL', encoding='utf-8')
        victims[name] = victim
        trap = mount / 'archive' / 'source' / name
        trap.mkdir(parents=True, exist_ok=True)
        (trap / name).symlink_to(victim)

    vi.archive(str(primary))

    for name, victim in victims.items():
        assert victim.read_text(encoding='utf-8') == 'ORIGINAL', (
            f'a directory standing where the {name} leaf belongs must be refused, not treated as a '
            'destination whose children copy2 may pick')
    assert syn.read_text(encoding='utf-8') == 'SYNTHESIS PAYLOAD', 'the primary synthesis survives'


def test_a_directory_standing_in_for_an_image_leaf_is_refused(monkeypatch, tmp_path):
    """Same mechanism on the per-image copy, which has its own loop and its own read-back."""
    primary, image, mount = _staged(tmp_path, monkeypatch, payload=b'the only copy')
    outside = tmp_path / 'outside'
    outside.mkdir()
    victim = outside / 'victim.jpg'
    victim.write_bytes(b'ORIGINAL')
    trap = mount / 'archive' / 'source' / 'companions' / 'fixture.jpg'
    trap.mkdir(parents=True, exist_ok=True)
    (trap / 'fixture.jpg').symlink_to(victim)

    vi.archive(str(primary))

    assert victim.read_bytes() == b'ORIGINAL', 'no image may be written through a linked child'
    assert image.exists() and image.read_bytes() == b'the only copy', \
        'and a refused destination must never cost the primary'


def test_a_directory_whose_name_begins_with_two_dots_is_still_inside_the_mount():
    """The `..archive` correction, which shipped with no arm of its own.

    A reviewer demonstrated the gap by reverting the fix to the old string-prefix form and running
    the suite: it stayed green. A prefix test reads a legitimate name like "..archive" as a parent
    traversal and silently refuses a real backup layout.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        mount = os.path.join(td, 'mount')
        inside = os.path.join(mount, '..archive', 'run')
        os.makedirs(inside)
        assert vi._contained(inside, mount) is True, \
            'a directory merely NAMED with two leading dots stays inside the mount'
        assert vi._contained(os.path.join(mount, os.pardir, 'elsewhere'), mount) is False, \
            'CONTROL: a real parent traversal is still an escape'
