#!/usr/bin/env python3
"""Claude Session Hub — launch the server."""
import os
import socket
import sys
import uvicorn


def _port_in_use(host: str, port: int) -> bool:
    """Return True if something is already bound to (host, port)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7777
    host = "0.0.0.0" if os.environ.get("DOCKER") else "127.0.0.1"

    os.makedirs(os.path.join(os.path.dirname(__file__), "data"), exist_ok=True)

    if _port_in_use(host, port):
        print(f"\n  Port {port} is already in use.")
        print(f"  Try a different port: python3 run.py {port + 1}\n",
              file=sys.stderr)
        sys.exit(1)

    # Setup logging before uvicorn starts
    from logging_config import setup_logging, log_startup_banner
    setup_logging()
    log_startup_banner(port, host)

    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=False,
        app_dir=os.path.dirname(os.path.abspath(__file__)),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
