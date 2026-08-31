"""Gate for the web-serve launcher — construct-only (never actually binds a socket in the test)."""
import fleet_tui.serve as serve


def test_defaults_bind_lan_and_scoped_port():
    # 0.0.0.0 so the phone can reach it; 8011 is the firewall-scoped port (houselan-fw)
    assert serve.HOST == "0.0.0.0"
    assert serve.PORT == 8011


def test_main_builds_server_with_our_python_and_module(monkeypatch):
    captured = {}

    class _FakeServer:
        def __init__(self, command, host, port, title=None, **kw):
            captured.update(command=command, host=host, port=port, title=title)

        def serve(self, *a, **k):
            captured["served"] = True

    monkeypatch.setattr(serve, "Server", _FakeServer)
    serve.main()
    assert "-m fleet_tui" in captured["command"]        # serves this package
    assert captured["host"] == "0.0.0.0" and captured["port"] == 8011
    assert captured["served"] is True                   # main() actually calls serve()


def test_env_overrides(monkeypatch):
    # host/port are env-overridable without editing code
    monkeypatch.setenv("FLEET_TUI_SERVE_HOST", "127.0.0.1")
    monkeypatch.setenv("FLEET_TUI_SERVE_PORT", "9099")
    import importlib
    importlib.reload(serve)
    try:
        assert serve.HOST == "127.0.0.1" and serve.PORT == 9099
    finally:
        monkeypatch.delenv("FLEET_TUI_SERVE_HOST", raising=False)
        monkeypatch.delenv("FLEET_TUI_SERVE_PORT", raising=False)
        importlib.reload(serve)   # restore module defaults for other tests
