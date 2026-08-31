"""Serve the Fleet Fleet TUI over HTTP so it can be opened in a browser — including the owner's PHONE
on the home wifi. Wraps `textual-serve` (a websocket→subprocess bridge); each browser session spawns its
own `python -m fleet_tui` instance, so this is the SAME monitor, just reachable from a browser.

EXPOSURE: binds 0.0.0.0 by design (so the phone can reach it) but is firewall-scoped by the houselan-fw
rules to loopback + the PC-link (192.0.2.0/24) + the home LAN (198.51.100.10/22) — DROP for anything
off the home network. It is NEVER on the public internet. (Same posture as gitea/karakeep — see
project-fleet-network-exposure-hardening.) Run via ./serve.sh.
"""
import os
import sys
from textual_serve.server import Server

HOST = os.environ.get("FLEET_TUI_SERVE_HOST", "0.0.0.0")     # 0.0.0.0 = LAN-reachable; firewall scopes it
PORT = int(os.environ.get("FLEET_TUI_SERVE_PORT", "8011"))


def main() -> None:
    # use THIS venv's python so the served subprocess has textual + our package
    command = f"{sys.executable} -m fleet_tui"
    server = Server(command, host=HOST, port=PORT, title="Fleet Fleet TUI")
    print(f"Serving the Fleet Fleet TUI on http://{HOST}:{PORT}  "
          f"(loopback + home-LAN only; open it from the phone at http://<fleet-LAN-ip>:{PORT})")
    server.serve()


if __name__ == "__main__":
    main()
