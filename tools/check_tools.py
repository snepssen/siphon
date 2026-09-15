#!/usr/bin/env python3
"""Exit 0 when siphon can work, 1 when something it needs is missing.

Split out so the .app launcher — a shell script that must not import the
application — can ask the same question the application will ask, and answer
it before the window fails to appear.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir))

import platform_support  # noqa: E402

if __name__ == "__main__":
    missing = platform_support.missing(required_only=True)
    for program in missing:
        line = program.install_line()
        sys.stderr.write(
            f"siphon needs {program.key} for {program.purpose}."
            + (f" Install it with: {line}\n" if line else "\n")
        )
    sys.exit(1 if missing else 0)
