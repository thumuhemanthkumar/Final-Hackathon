#!/usr/bin/env python3
"""Entry point:  python3 jobsched.py <command> [options]   (see --help)."""
import sys

from jobscheduler.cli import main

if __name__ == "__main__":
    sys.exit(main())
