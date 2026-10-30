#!/usr/bin/env python3
"""Run the website in the foreground using the active Python environment."""

import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'check'), nargs='?', default='run')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if not (root / 'config.json').is_file():
        parser.error('Create config.json from config.example.json, or use the core deployment configure.py.')
    command = [sys.executable, str(root / 'server.py')]
    if args.action == 'check':
        command.append('--check')
    try:
        return subprocess.call(command, cwd=root)
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
