"""Gate for the web-serve launcher — construct-only (never binds a socket)."""
import importlib
import importlib.util
import sys
from types import ModuleType

import pytest


@pytest.fixture
def serve(monkeypatch):
    # Stand in for the optional server dependency so configuration gates stay headless.
    with monkeypatch.context() as patch:
        if importlib.util.find_spec("textual_serve") is None:
            package = ModuleType("textual_serve")
            server = ModuleType("textual_serve.server")
            server.Server = object
            patch.setitem(sys.modules, "textual_serve", package)
            patch.setitem(sys.modules, "textual_serve.server", server)
        patch.delenv("FLEET_TUI_SERVE_HOST", raising=False)
        patch.delenv("FLEET_TUI_SERVE_PORT", raising=False)
        previous = sys.modules.pop("fleet_tui.serve", None)
        try:
            yield importlib.import_module("fleet_tui.serve")
        finally:
            sys.modules.pop("fleet_tui.serve", None)
            if previous is not None:
                sys.modules["fleet_tui.serve"] = previous


def test_defaults_bind_loopback_and_scoped_port(serve):
    # The launcher supplies a loopback default; host firewall policy is external.
    assert serve.HOST == "127.0.0.1"
    assert serve.PORT == 8011


def test_main_builds_server_with_our_python_and_module(monkeypatch, serve, capsys):
    captured = {}

    class _FakeServer:
        def __init__(self, command, host, port, title=None, **kw):
            captured.update(command=command, host=host, port=port, title=title)

        def serve(self, *a, **k):
            captured["served"] = True

    monkeypatch.setattr(serve, "Server", _FakeServer)
    serve.main()
    assert "-m fleet_tui" in captured["command"]        # serves our package
    assert captured["host"] == "127.0.0.1" and captured["port"] == 8011
    assert captured["served"] is True                   # main() actually calls serve()
    assert "Serving the Fleet TUI on http://127.0.0.1:8011" in capsys.readouterr().out


def test_env_overrides(monkeypatch, serve):
    # Bind-all is an explicit opt-in; neither setting relies on an ambient environment.
    with monkeypatch.context() as patch:
        patch.setenv("FLEET_TUI_SERVE_HOST", "0.0.0.0")
        patch.setenv("FLEET_TUI_SERVE_PORT", "9099")
        importlib.reload(serve)
        assert serve.HOST == "0.0.0.0" and serve.PORT == 9099
