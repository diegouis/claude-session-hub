#!/usr/bin/env python3
"""Claude Session Hub — launch the server."""
import uvicorn
import sys
import os


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7777
    os.makedirs(os.path.join(os.path.dirname(__file__), "data"), exist_ok=True)
    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        app_dir=os.path.dirname(os.path.abspath(__file__)),
    )


if __name__ == "__main__":
    main()
