#!/usr/bin/env python3
"""Claude Session Hub — launch the server."""
import os
import sys
import uvicorn


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7777
    host = "0.0.0.0" if os.environ.get("DOCKER") else "127.0.0.1"

    os.makedirs(os.path.join(os.path.dirname(__file__), "data"), exist_ok=True)

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
