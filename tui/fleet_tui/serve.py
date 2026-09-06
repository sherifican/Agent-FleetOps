"""Serve the TUI over HTTP using textual-serve; each browser session spawns
its own monitor process. Run ./serve.sh from tui/ after installing dependencies.

EXPOSURE: the default bind is 127.0.0.1 (loopback only). This wrapper provides no
authentication and does not install or verify firewall rules. Set
FLEET_TUI_SERVE_HOST=0.0.0.0 explicitly to bind all interfaces; restrict ingress to
trusted clients with a host firewall. Add authenticated access before allowing
untrusted clients to connect. Network restrictions are the adopter's responsibility.
"""
import os
import sys
from textual_serve.server import Server

HOST = os.environ.get("FLEET_TUI_SERVE_HOST", "127.0.0.1")  # loopback unless explicitly configured
PORT = int(os.environ.get("FLEET_TUI_SERVE_PORT", "8011"))


def main() -> None:
    # use THIS venv's python so the served subprocess has textual + our package
    command = f"{sys.executable} -m fleet_tui"
    server = Server(command, host=HOST, port=PORT, title="Fleet Fleet TUI")
    print(f"Serving the Fleet TUI on http://{HOST}:{PORT}  "
          f"(no authentication; restrict access with bind configuration and a host firewall)")
    server.serve()


if __name__ == "__main__":
    main()
