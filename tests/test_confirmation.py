"""An uncertain match waits for a person, and nothing is fetched until it does.

The promise is that a weak match is shown *before* it downloads. These tests
are what make that true rather than intended, so they check the thing that
actually matters: that a waiting job has no url on its item. A url is the only
thing that lets the fetch stage do anything, so an item without one provably
cannot have downloaded a byte.

The search is stubbed throughout. What is under test is the queue's behaviour
around an uncertain answer, not YouTube's opinion of it.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jobs as jobs_module        # noqa: E402
import model                      # noqa: E402
import resolve                    # noqa: E402
from model import Item            # noqa: E402


def candidates(*rows):
    return [resolve.Candidate(url=url, title=title, uploader=uploader,
                              duration=duration)
            for url, title, uploader, duration in rows]


class StubbedSearch:
    """Stands in for `resolve.search` for the length of a test."""

    def __init__(self, results):
        self.results = results
        self.calls = 0

    def __call__(self, query, limit=None, timeout=None):
        self.calls += 1
        return list(self.results)


class ResolutionStopsBeingAnAssumption(unittest.TestCase):
    def setUp(self):
        self.original = resolve.search
        self.item = Item(origin="deezer", kind="audio",
                         title="Some Song", artist="Some Band", duration=200)

    def tearDown(self):
        resolve.search = self.original

    def test_a_confident_match_is_accepted_without_asking(self):
        resolve.search = StubbedSearch(candidates(
            ("https://example.invalid/right",
             "Some Band - Some Song (Official Audio)", "Some Band", 200),
        ))
        proposal = resolve.propose(self.item)
        self.assertTrue(proposal.confident)
        self.assertIsNone(self.item.url, "propose must not commit anything")

        resolve.accept(self.item, proposal.best, proposal)
        self.assertEqual(self.item.url, "https://example.invalid/right")

    def test_proposing_never_touches_the_item(self):
        resolve.search = StubbedSearch(candidates(
            ("https://example.invalid/maybe", "Some Song", "Someone Else", 214),
        ))
        before = self.item.to_dict()
        resolve.propose(self.item)
        self.assertEqual(self.item.to_dict(), before)


class AWaitingJobHasDownloadedNothing(unittest.TestCase):
    def setUp(self):
        self.original = resolve.search
        # Plausible: the title is right, the uploader is not the artist, and
        # the length is a little out. Enough to offer, not enough to assume.
        resolve.search = StubbedSearch(candidates(
            ("https://example.invalid/a", "Some Song", "Not The Band", 214),
            ("https://example.invalid/b", "Some Song (Cover)", "Someone", 199),
        ))
        self.directory = tempfile.TemporaryDirectory()
        self.queue = jobs_module.Queue(
            workers=1, state_path=Path(self.directory.name) / "queue.json")

    def tearDown(self):
        self.queue.stop(wait=False)
        resolve.search = self.original
        self.directory.cleanup()

    def _run_one(self, **item_kwargs):
        item = Item(origin="deezer", kind="audio", title="Some Song",
                    artist="Some Band", duration=200, **item_kwargs)
        job = self.queue.add([item], "mp3")[0]
        self.queue.start()
        self.queue.drain(timeout=30)
        return self.queue.get(job.id)

    def test_it_waits_rather_than_guessing(self):
        job = self._run_one()
        self.assertEqual(job.state, model.WAITING)
        self.assertIsNone(job.item.url,
                          "a waiting job must have nothing to fetch from")
        self.assertIsNone(job.output_path)
        self.assertIsNone(job.finished_at, "waiting is not finished")
        self.assertFalse(job.finished)

    def test_the_question_carries_the_options_and_the_reasons(self):
        job = self._run_one()
        options = job.choice["options"]
        self.assertGreaterEqual(len(options), 2)
        self.assertTrue(all(o["url"] and o["title"] for o in options))
        self.assertTrue(options[0]["reasons"])
        self.assertGreaterEqual(options[0]["score"], options[1]["score"],
                                "options must be offered best first")

    def test_choosing_commits_to_exactly_what_was_chosen(self):
        job = self._run_one()
        second = job.choice["options"][1]["url"]
        self.assertTrue(self.queue.choose(job.id, second))

        job = self.queue.get(job.id)
        self.assertEqual(job.item.url, second)
        self.assertEqual(job.item.extra["match"]["chosen_by"], "you")
        self.assertIn(job.state, {model.QUEUED, model.RUNNING, model.FAILED,
                                  model.DONE})
        self.assertIsNone(job.choice, "the question is answered and gone")

    def test_an_invented_url_is_refused(self):
        """Only the options offered may be chosen — not anything posted at it."""
        job = self._run_one()
        self.assertFalse(self.queue.choose(job.id, "https://evil.invalid/x"))
        self.assertEqual(self.queue.get(job.id).item.url, None)

    def test_skipping_downloads_nothing_and_says_why(self):
        job = self._run_one()
        self.assertTrue(self.queue.skip(job.id))
        job = self.queue.get(job.id)
        self.assertEqual(job.state, model.CANCELLED)
        self.assertIsNone(job.item.url)
        self.assertIn("none of the matches", job.message.lower())

    def test_the_question_survives_a_restart(self):
        job = self._run_one()
        self.queue.stop(wait=True, timeout=5)

        reopened = jobs_module.Queue(
            workers=1, state_path=Path(self.directory.name) / "queue.json")
        reopened.load()
        restored = reopened.get(job.id)
        self.assertEqual(restored.state, model.WAITING,
                         "a question is still worth asking tomorrow")
        self.assertTrue(restored.choice["options"])
        self.assertIsNone(restored.item.url)

    def test_unattended_mode_takes_the_best_and_never_waits(self):
        unattended = jobs_module.Queue(
            workers=1, confirm_uncertain=False,
            state_path=Path(self.directory.name) / "unattended.json")
        item = Item(origin="deezer", kind="audio", title="Some Song",
                    artist="Some Band", duration=200)
        job = unattended.add([item], "mp3")[0]
        unattended.start()
        unattended.drain(timeout=30)
        unattended.stop(wait=False)

        job = unattended.get(job.id)
        self.assertNotEqual(job.state, model.WAITING)
        self.assertEqual(job.item.url, "https://example.invalid/a")


class NothingPlausibleIsStillARefusal(unittest.TestCase):
    def setUp(self):
        self.original = resolve.search
        resolve.search = StubbedSearch(candidates(
            ("https://example.invalid/no", "Totally Different", "Nobody", 40),
        ))
        self.directory = tempfile.TemporaryDirectory()
        self.queue = jobs_module.Queue(
            workers=1, state_path=Path(self.directory.name) / "queue.json")

    def tearDown(self):
        self.queue.stop(wait=False)
        resolve.search = self.original
        self.directory.cleanup()

    def test_below_the_floor_it_fails_rather_than_asking(self):
        """A question with no plausible answer is not worth anybody's time."""
        item = Item(origin="deezer", kind="audio", title="Some Song",
                    artist="Some Band", duration=200)
        job = self.queue.add([item], "mp3")[0]
        self.queue.start()
        self.queue.drain(timeout=30)

        job = self.queue.get(job.id)
        self.assertEqual(job.state, model.FAILED)
        self.assertIn("No convincing match", job.error)
        self.assertIsNone(job.item.url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
