"""Getting the things a tool needs, rather than complaining that they are gone.

Being told "install ffmpeg" and being asked "shall I install ffmpeg?" are
different experiences, and only the second is a tool doing its job. So
everything missing is listed, the exact commands are shown, and the default
answer is yes to all of it: pressing return installs everything.

Two things it will not do. It never invokes `sudo` on anybody's behalf: on a
package manager that needs root the commands are printed for the person to
run. And it never installs anything without being asked, except when told
`--yes`, which is there for scripts and for the launcher.

A tool makes one `Bootstrap` from its `Registry` and its own words, and its
start script runs `main()`: `--check` is silent and answers by exit code.
"""

import subprocess
import sys

from . import programs as _programs


class Bootstrap:
    def __init__(self, registry, *, banner, again, note=None, manager=None):
        """
        registry: the tool's programs.Registry.
        banner: the first line shown, such as "siphon needs a few programs it does not ship."
        again: what to say after a "no", such as "`siphon setup` will ask again."
        note: optional; returns a paragraph to show before the offer, or None.
        manager: optional; returns the package manager to use. A tool whose
            tests replace its own `current_manager` passes a lookup of that
            name, so the replacement is the one asked.
        """
        self.registry = registry
        self.banner = banner
        self.again = again
        self.note = note
        self.manager = manager or (lambda: _programs.current_manager())

    @property
    def tool(self):
        return self.registry.tool

    def survey(self):
        """Everything missing, split into what the tool cannot work without."""
        absent = self.registry.missing()
        return {"manager": self.manager(),
                "required": [p for p in absent if p.required],
                "optional": [p for p in absent if not p.required]}

    @staticmethod
    def command_for(program, manager):
        """The argv that would install one program, or None if it cannot."""
        return _programs.command_for(program, manager)

    def describe(self, program, manager):
        argv = self.command_for(program, manager)
        return " ".join(argv) if argv else f"(no {manager} package known)"

    def install(self, chosen, manager, on_line=None):
        """Run the installs. Returns [(program, ok, detail)] in the order tried."""
        results = []
        for program in chosen:
            argv = self.command_for(program, manager)
            if argv is None:
                results.append((program, False, "no package known for this system"))
                continue
            if on_line:
                on_line(f"$ {' '.join(argv)}")
            try:
                done = subprocess.run(argv, capture_output=True, text=True, timeout=1800)
            except (OSError, subprocess.SubprocessError) as error:
                results.append((program, False, str(error)))
                continue
            # Ask the disk, not the exit code: Homebrew returns non-zero for
            # things that are not failures, such as already installed.
            self.registry.forget()
            if self.registry.find(program.key) is not None:
                results.append((program, True, ""))
            else:
                detail = (done.stderr or done.stdout or "").strip().splitlines()
                results.append((program, False, detail[-1] if detail else f"exit {done.returncode}"))
        return results

    def offer(self, assume_yes=False, quiet=False):
        """Show what is missing and install it. Returns True if nothing required is left."""
        state = self.survey()
        manager = state["manager"]
        outstanding = state["required"] + state["optional"]
        width = max([len(p.key) for p in outstanding] + [9]) + 1

        if not outstanding:
            if not quiet:
                print(f"Everything {self.tool} needs is already installed.")
            return True

        if not quiet:
            print(self.banner)
            print()
            for program in state["required"]:
                print(f"  {program.key:<{width}} needed  — {program.purpose}")
            for program in state["optional"]:
                print(f"  {program.key:<{width}} extra   — {program.purpose}")
            print()
            paragraph = self.note() if self.note else None
            if paragraph:
                print(paragraph)
                print()

        if manager is None:
            print(f"{self.tool} could not find a package manager to install them with.", file=sys.stderr)
            print("On macOS, Homebrew is the usual one: https://brew.sh", file=sys.stderr)
            return not state["required"]

        label = _programs.MANAGERS[manager]["label"]
        if _programs.MANAGERS[manager]["needs_root"]:
            # Never sudo on somebody's behalf. Show the line; let them run it.
            if not quiet:
                print(f"Installing these needs root, so {self.tool} will not run it for you. Copy this:\n")
                names = [p.package_for(manager) for p in outstanding if p.package_for(manager)]
                print("  " + " ".join(_programs.MANAGERS[manager]["command"] + names))
                print()
            return not state["required"]

        if not assume_yes:
            for program in outstanding:
                print(f"  {self.describe(program, manager)}")
            print()
            what = "it" if len(outstanding) == 1 else f"all {len(outstanding)} of these"
            try:
                answer = input(f"Install {what} with {label}? [Y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return not state["required"]
            if answer in {"n", "no"}:
                print(f"\nNothing installed. {self.again}")
                return not state["required"]

        print()
        results = self.install(outstanding, manager, on_line=print)
        print()
        for program, ok, detail in results:
            if ok:
                version = self.registry.version(program.key) or "installed"
                print(f"  ✓ {program.key:<{width}} {version}")
            else:
                print(f"  ✗ {program.key:<{width}} {detail}", file=sys.stderr)
                print(f"    try by hand: {self.describe(program, manager)}", file=sys.stderr)
        self.registry.forget()
        return not self.registry.missing(required_only=True)

    def ready(self):
        """True when nothing the tool cannot work without is missing."""
        return not self.registry.missing(required_only=True)

    def main(self, argv=None):
        argv = list(sys.argv[1:] if argv is None else argv)
        if "--check" in argv:
            return 0 if self.ready() else 1
        return 0 if self.offer(assume_yes="--yes" in argv or "-y" in argv) else 1
