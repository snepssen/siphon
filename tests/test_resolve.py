"""The resolver's judgement, tested against the cases that actually go wrong.

None of these touch the network. `rank` is a pure function of an item and a
list of candidates, which is the whole reason the searching and the scoring
are separate: the part that can be wrong can be tested without asking YouTube
anything.

The first test is the one that matters. It is built from a real search: asking
for Daft Punk's "Harder, Better, Faster, Stronger" returns the genuine track
at 223 seconds and an unrelated recording called "Daft Hands" at 225. The
track wanted is 226 seconds long, so the impostor is *closer*. Any resolver
that decides on running time picks the wrong one.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import resolve                      # noqa: E402
from model import Item              # noqa: E402


def candidate(title, uploader, duration, verified=False):
    return resolve.Candidate(url="https://example.invalid/x", title=title,
                             uploader=uploader, duration=duration,
                             verified=verified)


class DurationIsNotEnough(unittest.TestCase):
    def setUp(self):
        self.item = Item(title="Harder, Better, Faster, Stronger",
                         artist="Daft Punk", duration=226, kind="audio")

    def test_the_closest_by_length_is_not_the_winner(self):
        real = candidate("Daft Punk - Harder, Better, Faster, Stronger "
                         "(Official Video)", "Daft Punk", 223)
        impostor = candidate("Daft Hands - Harder, Better, Faster, Stronger",
                             "Fr. Eckle Studios", 225)
        self.assertLess(abs(226 - impostor.duration), abs(226 - real.duration),
                        "the fixture no longer reproduces the trap")

        ranked = resolve.rank(self.item, [impostor, real])
        self.assertIs(ranked[0], real)
        self.assertGreaterEqual(real.score, resolve.CONFIDENT)
        self.assertLess(impostor.score, resolve.CONFIDENT,
                        "an unrelated recording must not reach confidence")

    def test_a_live_version_is_pushed_below_the_floor(self):
        live = candidate("Daft Punk - Harder Better Faster Stronger (Live at "
                         "Alive 2007)", "Daft Punk", 300)
        resolve.rank(self.item, [live])
        self.assertLess(live.score, resolve.FLOOR)

    def test_a_topic_channel_counts_as_the_artist(self):
        topic = candidate("Harder, Better, Faster, Stronger", "Daft Punk - Topic", 226)
        resolve.rank(self.item, [topic])
        self.assertGreaterEqual(topic.score, resolve.CONFIDENT)
        self.assertIn("official auto-generated upload", topic.reasons)


class UnwantedVersions(unittest.TestCase):
    def test_karaoke_and_covers_are_rejected(self):
        item = Item(title="Yesterday", artist="The Beatles", duration=125,
                    kind="audio")
        for title, uploader in [
            ("Yesterday - Karaoke Version", "Sing King"),
            ("Yesterday (Beatles Cover)", "Some Guy"),
            ("Yesterday - 8D AUDIO", "8D Tunes"),
            ("Yesterday sped up", "speedy"),
        ]:
            with self.subTest(title=title):
                bad = candidate(title, uploader, 125)
                resolve.rank(item, [bad])
                self.assertLess(bad.score, resolve.CONFIDENT, title)

    def test_a_penalty_does_not_apply_when_it_was_asked_for(self):
        """Somebody wanting a remix must be able to get the remix."""
        item = Item(title="Around the World (Remix)", artist="Daft Punk",
                    duration=200, kind="audio")
        wanted = candidate("Daft Punk - Around the World (Remix)",
                           "Daft Punk", 200)
        resolve.rank(item, [wanted])
        self.assertGreaterEqual(wanted.score, resolve.CONFIDENT)


class Normalising(unittest.TestCase):
    def test_decoration_and_accents_are_stripped(self):
        self.assertEqual(resolve.normalise("Café (Official Video) [HD]"), "cafe")
        self.assertEqual(resolve.normalise("Song feat. Someone"), "song")

    def test_the_query_names_the_artist_first(self):
        item = Item(title="Hello", artist="Adele")
        self.assertEqual(resolve.query_for(item), "Adele Hello")


class RefusingRatherThanGuessing(unittest.TestCase):
    def test_nothing_plausible_means_nothing_is_returned(self):
        item = Item(title="Some Obscure Track", artist="Nobody At All",
                    duration=200, kind="audio")
        rubbish = [candidate("Completely Different Song", "Random", 45)]
        ranked = resolve.rank(item, rubbish)
        self.assertLess(ranked[0].score, resolve.FLOOR)

    def test_an_already_fetchable_item_is_left_alone(self):
        item = Item(title="x", url="https://example.invalid/already")
        self.assertIsNone(resolve.resolve(item))
        self.assertEqual(item.url, "https://example.invalid/already")


if __name__ == "__main__":
    unittest.main(verbosity=2)
