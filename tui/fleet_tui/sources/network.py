"""Pure readers for network-bridge status in the Fleet fleet."""
import os
import subprocess

# Defaults are RFC5737 documentation addresses — set these to your real link before use.
PC_IP = os.environ.get("FLEET_PC_IP", "192.0.2.1")
LINK_IP = os.environ.get("FLEET_LINK_IP", "192.0.2.2")
TELEGRAM_SEEN = os.path.expanduser("~/.claude/curation/.telegram_seen")


def read_ip_addr() -> str:
    """Read stdout of `ip -br addr` (subprocess, timeout 5); `""` on any error."""
    try:
        result = subprocess.run(
            ["ip", "-br", "addr"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout
    except Exception:
        return ""


def read_pc_reachable(ip: str = PC_IP) -> bool:
    """`ping -c1 -W1 <ip>` returncode == 0; `False` on any error."""
    try:
        result = subprocess.run(
            ["ping", "-c1", "-W1", ip],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def read_gateway() -> bool:
    """`systemctl --user is-active hermes-gateway` stdout stripped == "active"; `False` on error."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "hermes-gateway"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def read_cron_list() -> str:
    """stdout of `hermes cron list` (timeout 10); `""` on any error."""
    try:
        result = subprocess.run(
            ["hermes", "cron", "list"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout
    except Exception:
        return ""


def read_telegram_seen_mtime(path: str = TELEGRAM_SEEN) -> float:
    """`os.path.getmtime(path)`; `0.0` on any error."""
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0


def build_network(ip_addr: str, pc_reachable: bool, gateway: bool, cron_list: str, tg_seen_mtime: float) -> dict:
    """PURE (no I/O). Returns EXACTLY the specified shape."""
    return {
        "pc": {
            "link_up": (LINK_IP in ip_addr),
            "reachable": bool(pc_reachable),
            "ip": PC_IP
        },
        "telegram": {
            "gateway_up": bool(gateway),
            "poller": ("telegram-inbound" in cron_list),
            "last_seen_mtime": float(tg_seen_mtime or 0)
        }
    }


def status() -> dict:
    """Call the readers and pass their results to build_network(...); return its dict."""
    return build_network(
        read_ip_addr(),
        read_pc_reachable(),
        read_gateway(),
        read_cron_list(),
        read_telegram_seen_mtime()
    )
