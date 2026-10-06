"""Pin category membership to the official UCD 16.0.0, independent of Python age."""
import ast
import hashlib
import os
from pathlib import Path
import unicodedata

import pytest


HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / 'honesty_stop_gate.py'
CATEGORIES = {'Cc', 'Cf', 'Cs', 'Zs', 'Zl', 'Zp', 'Mn', 'Me'}


def _points():
    # Reuse the config suite's source seam for external baseline/mutation proofs.
    source = Path(os.environ.get('HONESTY_CONFIG_TEST_SOURCE', HOOK))
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tables = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name)
                      and target.id == '_NON_TEXT_CATEGORY_RANGES' for target in node.targets)]
    assert len(tables) == 1
    points = set()
    previous = -1
    for start, end in ast.literal_eval(tables[0]):
        assert type(start) is int and type(end) is int
        assert previous < start <= end <= 0x10FFFF, 'unordered/overlapping/invalid range'
        points.update(range(start, end + 1))
        previous = end
    return points


def test_category_table_has_ucd16_membership_digest():
    pin = HERE / 'fixtures' / 'unicode16_nontext.sha256'
    expected, = [line for line in pin.read_text().splitlines()
                 if line and not line.startswith('#')]
    encoded = ''.join(f'{point:06X}\n' for point in sorted(_points())).encode('ascii')
    assert hashlib.sha256(encoded).hexdigest() == expected


@pytest.mark.skipif(unicodedata.unidata_version != '16.0.0',
                    reason='direct category oracle requires Unicode 16.0.0 exactly')
def test_category_table_agrees_with_unicode16_runtime():
    expected = {point for point in range(0x110000)
                if unicodedata.category(chr(point)) in CATEGORIES}
    assert _points() == expected


def test_blank_policy_membership_digest():
    """Pin explicit policy exclusions separately from Unicode category ranges."""
    source = Path(os.environ.get('HONESTY_CONFIG_TEST_SOURCE', HOOK))
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tables = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name)
                      and target.id == '_BLANK_CODE_POINTS' for target in node.targets)]
    assert len(tables) == 1
    points = ast.literal_eval(tables[0])
    assert type(points) is set
    assert all(type(point) is int and 0 <= point <= 0x10FFFF for point in points)
    pin = HERE / 'fixtures' / 'unicode16_blank.sha256'
    expected, = [line for line in pin.read_text().splitlines()
                 if line and not line.startswith('#')]
    encoded = ''.join(f'{point:06X}\n' for point in sorted(points)).encode('ascii')
    assert hashlib.sha256(encoded).hexdigest() == expected
