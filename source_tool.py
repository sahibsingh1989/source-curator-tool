#!/usr/bin/env python3
"""Entry point: py source_tool.py inspect https://example.com"""

import sys

from source_curator.cli import main

if __name__ == "__main__":
    sys.exit(main())
