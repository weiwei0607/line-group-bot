"""
LINE Group Bot — Standard logging setup.
"""

import logging
import sys


def setup_logging(level=logging.INFO):
    """Configure root logger for Render / local development."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers on re-import / reload
    if not root.handlers:
        root.addHandler(handler)
