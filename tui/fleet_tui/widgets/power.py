"""Pure wattage display banded by a configured device cap."""
_BANDS = ((10.0, "springgreen"), (35.0, "green"), (60.0, "yellow"), (85.0, "orange"))


def power_cell(watts, cap=None):
    try:
        watts = float(watts)
    except (TypeError, ValueError):
        return ""
    if watts <= 0:
        return ""
    try:
        cap = float(cap)
    except (TypeError, ValueError):
        cap = 0.0
    if cap <= 0:
        return f" {watts:.1f}W"
    color = "red"
    for ceiling, candidate in _BANDS:
        if watts * 100.0 / cap <= ceiling:
            color = candidate
            break
    return f" [{color}]{watts:.1f}W[/]"
