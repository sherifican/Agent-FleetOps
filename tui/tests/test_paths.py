"""Configuration boundaries: absent paths never become accidental filesystem targets."""
import importlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fleet_tui import paths


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in paths.KEYS:
        monkeypatch.delenv('FLEET_TUI_' + key.upper(), raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))


def test_no_configuration():
    assert all(paths.resolve(key) is None for key in paths.KEYS)
    assert paths.resolve('curation_dir', '.trigger') is None


def test_env_override(monkeypatch):
    monkeypatch.setenv('FLEET_TUI_CURATION_DIR', 'fixture/curation')
    assert paths.resolve('curation_dir', '.trigger') == 'fixture/curation/.trigger'


def test_json_and_env_precedence(monkeypatch, tmp_path):
    config = tmp_path / 'fleet_tui' / 'paths.json'
    config.parent.mkdir()
    config.write_text(json.dumps({'curation_dir': 'fixture/json'}))
    assert paths.resolve('curation_dir') == 'fixture/json'
    monkeypatch.setenv('FLEET_TUI_CURATION_DIR', 'fixture/env')
    assert paths.resolve('curation_dir') == 'fixture/env'
    monkeypatch.setenv('FLEET_TUI_CURATION_DIR', '')
    assert paths.resolve('curation_dir') is None  # explicit disable beats JSON


@pytest.mark.parametrize('body', ['{', '[]', 'null', '{"curation_dir": 5}', '{"curation_dir": ""}', '{"curation_dir": "\\u0000"}'])
def test_bad_config_fails_closed(tmp_path, body):
    config = tmp_path / 'fleet_tui' / 'paths.json'
    config.parent.mkdir()
    config.write_text(body)
    assert paths.resolve('curation_dir') is None


def test_unknown_key():
    with pytest.raises(KeyError):
        paths.resolve('typo_dir')


def test_example_covers_every_key():
    p = Path(__file__).parents[1] / 'paths.example.json'
    assert "authors' instance" in p.read_text().splitlines()[0]
    data = json.loads(p.read_text())
    assert set(data) - {'_comment'} == set(paths.KEYS)
    assert all(isinstance(data[k], str) and data[k] for k in paths.KEYS)


def test_unconfigured_sources_do_not_touch_filesystem(monkeypatch):
    names = ('actions', 'curation', 'failures', 'focus', 'health', 'inbox',
             'joboutput', 'jobs', 'network', 'passback', 'posture')
    modules = {n: importlib.reload(importlib.import_module('fleet_tui.sources.' + n)) for n in names}
    # Configuration discovery is allowed; external source I/O is not.
    monkeypatch.setattr(paths, '_read_config', lambda: {})
    with monkeypatch.context() as io:
        spy = Mock(side_effect=AssertionError('unexpected filesystem access'))
        io.setattr('builtins.open', spy)
        for target in ('os.path.exists', 'os.path.getmtime', 'os.stat', 'os.makedirs',
                       'os.remove', 'glob.glob', 'sqlite3.connect', 'shutil.disk_usage'):
            io.setattr(target, spy)
        a, c, f = (modules[n] for n in ('actions', 'curation', 'focus'))
        assert a.pending_count() == 0 and a.request_action('test', 'test') is False
        assert c.recent_passes() == [] and c.trigger_status()['pending'] is False
        assert c.queue_pass() is False
        assert f.is_on() is False and f.read_state().on is False
        assert f.turn_on().on is False
        f.turn_off()
        assert modules['failures'].recent_failures() == []
        assert modules['inbox'].list_inbox() == []
        for source in ('github', 'dep', 'curation', 'automation', 'hive', 'backup', 'supply'):
            assert modules['inbox'].ack(source) is False
        assert modules['joboutput'].job_output_tail('fixture-job') == ''
        assert modules['joboutput'].recent_outputs([]) == []
        assert modules['jobs'].read_hermes_jobs() == []
        assert modules['jobs'].build_jobs([], '') == []
        assert modules['passback'].list_passback() == []
        assert modules['posture'].snapshot()['backup']['last'] is None
        h = modules['health']
        assert h.read_reliability_tail() == '' and h.read_disk() == {}
        assert h.build_snapshot({}, [], {}).loaded == []
        n = modules['network']
        for name, value in [('read_ip_addr', ''), ('read_pc_reachable', False),
                            ('read_gateway', False), ('read_cron_list', '')]:
            io.setattr(n, name, lambda v=value: v)
        assert n.status()['telegram']['last_seen_mtime'] == 0
        spy.assert_not_called()  # caught exceptions alone cannot make this test pass


@pytest.mark.parametrize('route', ['env', 'json'])
def test_configured_reader_positive_control(monkeypatch, tmp_path, route):
    from fleet_tui.sources import jobs
    cron = tmp_path / 'cron'
    cron.mkdir()
    p = cron / 'jobs.json'
    p.write_text('{"jobs": [{"id": "fixture-job"}]}')
    with monkeypatch.context() as config_env:
        config_env.setenv('XDG_CONFIG_HOME', str(tmp_path))
        if route == 'env':
            config_env.setenv('FLEET_TUI_HERMES_CRON_DIR', str(cron))
        else:
            config = tmp_path / 'fleet_tui' / 'paths.json'
            config.parent.mkdir()
            config.write_text(json.dumps({'hermes_cron_dir': str(cron)}))
        try:
            importlib.reload(jobs)
            assert jobs.HERMES_JOBS_PATH == paths.resolve('hermes_cron_dir', 'jobs.json') == str(p)
            assert jobs.read_hermes_jobs() == [{'id': 'fixture-job'}]
        finally:
            if route == 'json':
                config.unlink()
    # Restore the import-time default so later tests cannot inherit this fixture.
    importlib.reload(jobs)


def test_playlist_panel_tracks_only_external_notification_dependency(monkeypatch):
    # Playlist config/state/requests are TUI-owned. Only the notification button
    # needs the external curation script; research_dir is not a panel dependency.
    assert paths.PANEL_KEYS['research_playlists'] == ('curation_dir',)
    assert paths.panel_notices()['research_playlists'] == paths.notice(['curation_dir'])
    monkeypatch.setenv('FLEET_TUI_CURATION_DIR', 'fixture/curation')
    assert paths.resolve('research_dir') is None
    assert paths.panel_notices()['research_playlists'] == ''


def test_muted_panel_notice():
    assert paths.notice(['curation_dir']) == '[dim]not configured: curation_dir[/]'
    assert paths.notice([]) == ''
