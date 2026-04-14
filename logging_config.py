"""Logging configuration for Claude Session Hub."""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path


class ColorFormatter(logging.Formatter):
    """Colored log output for terminal."""

    COLORS = {
        'DEBUG': '\033[36m',     # cyan
        'INFO': '\033[32m',      # green
        'WARNING': '\033[33m',   # yellow
        'ERROR': '\033[31m',     # red
        'CRITICAL': '\033[35m',  # magenta
    }
    RESET = '\033[0m'

    def format(self, record):
        color = self.COLORS.get(record.levelname, '')
        record.levelname = f"{color}{record.levelname:<8}{self.RESET}"
        return super().format(record)


def setup_logging():
    """Configure logging based on environment variables.

    Env vars:
        LOG_LEVEL: DEBUG, INFO, WARNING, ERROR (default: INFO)
        LOG_FILE: Path to log file (optional, logs to file + console)
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    # Root logger
    root = logging.getLogger()
    root.setLevel(level)

    # Console handler with colors
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(ColorFormatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S"
    ))
    root.addHandler(console)

    # File handler (optional)
    log_file = os.environ.get("LOG_FILE")
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        root.addHandler(file_handler)

    # Quiet noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return root


def log_startup_banner(port: int, host: str):
    """Print a startup banner with configuration summary."""
    logger = logging.getLogger("session-hub")
    logger.info("=" * 50)
    logger.info("  Claude Session Hub")
    logger.info("=" * 50)
    logger.info(f"  URL:       http://{host}:{port}")
    logger.info(f"  Log level: {os.environ.get('LOG_LEVEL', 'INFO')}")
    log_file = os.environ.get("LOG_FILE")
    if log_file:
        logger.info(f"  Log file:  {log_file}")
    logger.info(f"  Claude dir: {os.environ.get('CLAUDE_DIR', '~/.claude')}")
    logger.info(f"  Docker:    {'yes' if os.environ.get('DOCKER') else 'no'}")
    logger.info("=" * 50)
