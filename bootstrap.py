#!/usr/bin/env python3
"""Getting the things siphon needs, rather than complaining that they are gone.

siphon ships no binaries. That is the right call — yt-dlp would be stale
within a fortnight and a bundled ffmpeg would carry worse codecs than the one
already on this machine — but it leaves a gap that a tool should close for
somebody rather than hand to them. Being told "install ffmpeg" and being asked
"shall I install ffmpeg?" are a different experience, and only one of them is
a tool doing its job.

So: everything missing is listed, the exact commands are shown, and the
default answer is yes to all of it. Pressing return installs everything.

**Two things it will not do.** It never invokes `sudo` on anybody's behalf —
on a distribution whose package manager needs root, the commands are printed
to run by hand, because a program that silently escalates is a program nobody
should run. And it never installs anything without being asked, except when
explicitly told `--yes`, which is there for scripts and for the launcher.

How it asks is workshop-core's `Bootstrap`; the words are siphon's.
"""

import sys

import platform_support as programs
from workshop.bootstrap import Bootstrap

BANNER = "siphon needs a few programs it does not ship."

_setup = Bootstrap(
    programs._registry,
    banner=BANNER,
    again="`siphon setup` will ask again.",
    # Asked through this module's name, so a test that replaces
    # platform_support.current_manager is the one consulted.
    manager=lambda: programs.current_manager(),
)

survey = _setup.survey
command_for = _setup.command_for
describe = _setup.describe
install = _setup.install
ready = _setup.ready


def offer(assume_yes=False, quiet=False):
    """Show what is missing and install it. Returns True if nothing is left."""
    return _setup.offer(assume_yes=assume_yes, quiet=quiet)


def main(argv=None):
    return _setup.main(argv)


if __name__ == "__main__":
    sys.exit(main())
