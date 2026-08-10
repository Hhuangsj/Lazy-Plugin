#!/usr/bin/env python3
"""Validate a YAML weekly work report."""

import argparse
from pathlib import Path

from collect import load_yaml, validate_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    arguments = parser.parse_args(argv)
    errors = validate_report(load_yaml(arguments.report))
    if errors:
        for error in errors:
            print(f"invalid: {error}")
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
