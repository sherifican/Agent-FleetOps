"""Execute the shipped compose body headlessly; this is not a Textual layout test."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_tui import paths


@pytest.mark.parametrize('configured,items', [
    (False, []), (True, []), (True, [{'title': 'fixture', 'name': 'entry'}]),
], ids=['unconfigured-empty', 'configured-empty', 'configured-populated'])
def test_passback_notice_once_without_empty_static(monkeypatch, configured, items):
    tree = ast.parse((Path(__file__).parents[1] / 'fleet_tui' / 'app.py').read_text())
    modal = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PassbackModal')
    compose = next(n for n in modal.body if isinstance(n, ast.FunctionDef) and n.name == 'compose')
    compose.returns = None
    # Stand-ins capture yielded content only; run the actual production function.
    class Container:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def static(text, **kwargs):
        return text

    keys = ('passback_docs_glob', 'comms_inbound_glob')
    monkeypatch.setattr(paths, '_read_config', lambda: {})
    for key in keys:
        if configured:
            monkeypatch.setenv('FLEET_TUI_' + key.upper(), 'fixture/*')
        else:
            monkeypatch.delenv('FLEET_TUI_' + key.upper(), raising=False)
    namespace = dict(Vertical=Container, VerticalScroll=Container, Static=static,
                     notice=paths.notice, missing=paths.missing,
                     PEER_BOX_LABEL='peer orchestrator', escape=lambda s: s)
    exec(compile(ast.Module(body=[compose], type_ignores=[]), 'app.py', 'exec'), namespace)
    output = list(namespace['compose'](SimpleNamespace(_items=items)))
    assert '' not in output
    for key in keys:
        assert sum(f'not configured: {key}' in text for text in output) == (0 if configured else 1)
    if configured and not items:
        assert output.count('No passback files yet. ✓') == 1
