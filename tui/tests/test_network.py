"""Gate for sources/network.py (authored by Claude; the local lane implements the source to pass THIS).
Bridge-status source: PC↔Fleet direct link + Telegram bridge. Pure build_network + safe readers."""
from fleet_tui.sources import network


def test_build_network_all_up():
    d = network.build_network(
        ip_addr="enp42s0 UP 198.51.100.10/22\nenx9c69d3806283 UP 192.0.2.2/24\n",
        pc_reachable=True,
        gateway=True,
        cron_list="  Name:  telegram-inbound\n  Script: telegram_poller_cron.sh\n",
        tg_seen_mtime=1750000000.0,
    )
    assert d["pc"]["link_up"] is True and d["pc"]["reachable"] is True
    assert d["pc"]["ip"] == "192.0.2.1"
    assert d["telegram"]["gateway_up"] is True
    assert d["telegram"]["poller"] is True
    assert d["telegram"]["last_seen_mtime"] == 1750000000.0


def test_build_network_all_down():
    d = network.build_network(ip_addr="", pc_reachable=False, gateway=False, cron_list="", tg_seen_mtime=0)
    assert d["pc"]["link_up"] is False and d["pc"]["reachable"] is False
    assert d["pc"]["ip"] == "192.0.2.1"          # the PC ip is a constant, always reported
    assert d["telegram"]["gateway_up"] is False and d["telegram"]["poller"] is False
    assert d["telegram"]["last_seen_mtime"] == 0.0


def test_link_up_but_pc_down():
    # link interface present but the PC itself unreachable (the current real state — PC off)
    d = network.build_network("x 192.0.2.2/24", pc_reachable=False, gateway=True,
                              cron_list="telegram-inbound", tg_seen_mtime=1.0)
    assert d["pc"]["link_up"] is True and d["pc"]["reachable"] is False


def test_readers_never_raise():
    assert isinstance(network.read_ip_addr(), str)
    assert network.read_pc_reachable("192.0.2.1") in (True, False)     # TEST-NET, unroutable → False, no raise
    assert network.read_gateway() in (True, False)
    assert isinstance(network.read_cron_list(), str)
    assert network.read_telegram_seen_mtime("/no/such/file/xyz") == 0.0


def test_status_shape():
    s = network.status()
    assert isinstance(s, dict) and "pc" in s and "telegram" in s
    assert set(s["pc"]) == {"link_up", "reachable", "ip"}
    assert set(s["telegram"]) == {"gateway_up", "poller", "last_seen_mtime"}
