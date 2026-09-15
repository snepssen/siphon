"""Installing what siphon does not ship, and running cobalt without Docker.

Nothing here installs anything. What is tested is the reasoning around it: how
a missing program becomes a command, which programs get offered, and the two
refusals that matter — no package manager, and a manager that needs root.

That second one is not a detail. siphon will not run `sudo` for anybody, so
on a distribution whose package manager needs it the commands are printed
instead. A test that lets that regress would let siphon start escalating
privileges quietly.
"""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap                      # noqa: E402
import cobalt_service                 # noqa: E402
import platform_support as programs   # noqa: E402


class KnowingWhatIsMissing(unittest.TestCase):
    def test_every_program_can_be_installed_on_every_manager(self):
        """A program with no package name anywhere can only ever be reported."""
        for key, program in programs.PROGRAMS.items():
            self.assertTrue(program.packages,
                            f"{key} names no package for any manager")
            for manager in programs.MANAGERS:
                self.assertIn(manager, program.packages,
                              f"{key} has no {manager} package")

    def test_one_package_serving_two_programs_is_offered_once(self):
        """ffmpeg and ffprobe are one formula; asking twice is asking twice."""
        self.assertEqual(programs.PROGRAMS["ffprobe"].provided_by, "ffmpeg")
        keys = [p.key for p in programs.missing()]
        if "ffmpeg" in keys:
            self.assertNotIn("ffprobe", keys)

    def test_the_command_is_built_from_the_package_name(self):
        magick = programs.PROGRAMS["magick"]
        self.assertEqual(bootstrap.command_for(magick, "brew"),
                         ["brew", "install", "imagemagick"])
        self.assertEqual(bootstrap.command_for(magick, "apt"),
                         ["sudo", "apt-get", "install", "-y", "imagemagick"])

    def test_a_cask_keeps_its_flag(self):
        """`brew install --cask libreoffice` is three arguments, not two."""
        self.assertEqual(bootstrap.command_for(programs.PROGRAMS["soffice"], "brew"),
                         ["brew", "install", "--cask", "libreoffice"])

    def test_the_line_shown_matches_the_command_run(self):
        """One table, so the sentence and the argv cannot drift apart."""
        manager = programs.current_manager()
        if manager is None:
            self.skipTest("no package manager on this machine")
        for program in programs.PROGRAMS.values():
            argv = bootstrap.command_for(program, manager)
            if argv:
                self.assertEqual(program.install_line(), " ".join(argv))


class RefusingToEscalate(unittest.TestCase):
    def test_managers_needing_root_are_marked_as_such(self):
        for name in ("apt", "dnf", "pacman"):
            self.assertTrue(programs.MANAGERS[name]["needs_root"], name)
        for name in ("brew", "winget"):
            self.assertFalse(programs.MANAGERS[name]["needs_root"], name)

    def test_a_root_manager_prints_rather_than_runs(self):
        original_manager = programs.current_manager
        original_install = bootstrap.install
        ran = []
        programs.current_manager = lambda: "apt"
        bootstrap.install = lambda *a, **k: ran.append(a) or []
        try:
            bootstrap.offer(assume_yes=True, quiet=True)
        finally:
            programs.current_manager = original_manager
            bootstrap.install = original_install
        self.assertEqual(ran, [], "siphon must never run sudo on somebody's behalf")


class FindingProgramsFinderCannotSee(unittest.TestCase):
    def test_locate_works_without_a_registry_entry(self):
        """npm ships with node and has no Program of its own."""
        self.assertIsNotNone(programs.locate("python3", "python"))
        self.assertIsNone(programs.locate("definitely-not-a-real-binary-xyz"))


class CobaltWithoutDocker(unittest.TestCase):
    def test_it_needs_git_node_and_pnpm_and_says_which_are_absent(self):
        absent = cobalt_service.requirements()
        self.assertIsInstance(absent, list)
        for key in absent:
            self.assertIn(key, {"git", "node", "pnpm"})

    def test_it_binds_the_loopback_address_only(self):
        """cobalt defaults to 0.0.0.0; here it is one person's helper."""
        self.assertEqual(cobalt_service.HOST, "127.0.0.1")
        self.assertTrue(cobalt_service.url(9000).startswith("http://127.0.0.1"))

    def test_the_checkout_lives_in_siphons_own_state(self):
        """So `remove` can delete it without wondering what else is in there."""
        import paths
        self.assertTrue(str(cobalt_service.home()).startswith(str(paths.state_dir())))

    def test_it_prefers_a_node_its_native_dependency_supports(self):
        """isolated-vm does not build against the newest Node."""
        directory = cobalt_service.node_bin()
        if directory is None:
            self.skipTest("no node on this machine")
        self.assertTrue(Path(directory, "node").is_file())

    def test_the_node_used_to_build_is_the_node_used_to_run(self):
        """A module built by one Node and loaded by another is 'no native build'."""
        environment = cobalt_service._environment()
        directory = cobalt_service.node_bin()
        if directory:
            self.assertTrue(environment["PATH"].startswith(directory))


if __name__ == "__main__":
    unittest.main(verbosity=2)
