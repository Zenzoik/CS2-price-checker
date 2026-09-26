"""Backwards-compatible entry point: `python main.py` still opens the menu."""

import sys

from cs2tracker.cli import main

if __name__ == "__main__":
    sys.exit(main())
