"""Tidal's JSON:API, tested against the shape a real response actually has.

Everything Tidal needs a key for is untestable here, so what is tested is what
does not: parsing its links, the refusal when no key is set, ISO 8601
durations, and the JSON:API join — `data` plus a flat `included` sidecar,
which is the part most likely to be got subtly wrong.

The fixture below is modelled on a genuine `GET /v2/albums/{id}` response:
attribute names, the `PT1H2M11S` duration format, `imageLinks` carrying `meta`
sizes, and relationships that reference `included` by (type, id) rather than
nesting. Track attributes are siphon's best reading rather than a confirmed
capture — see the note in CLAUDE.md.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sources import tidal          # noqa: E402
from sources import SourceError    # noqa: E402

ALBUM = {
    "data": {
        "id": "350860190",
        "type": "albums",
        "attributes": {
            "title": "Renaissance",
            "duration": "PT1H2M11S",
            "releaseDate": "2022-07-29",
            "numberOfItems": 2,
            "imageLinks": [
                {"href": "https://img/small.jpg", "meta": {"width": 160}},
                {"href": "https://img/big.jpg", "meta": {"width": 1280}},
            ],
            "tidalUrl": "https://tidal.com/browse/album/350860190",
        },
        "relationships": {
            "artists": {"data": [{"type": "artists", "id": "1566"}]},
            "items": {"data": [{"type": "tracks", "id": "t1"},
                               {"type": "tracks", "id": "t2"}]},
        },
    },
    "included": [
        {"type": "artists", "id": "1566", "attributes": {"name": "Beyoncé"}},
        {"type": "tracks", "id": "t1", "attributes": {
            "title": "I'M THAT GIRL", "duration": "PT3M28S",
            "isrc": "USSM12204636", "trackNumber": 1, "volumeNumber": 1},
         "relationships": {"artists": {"data": [{"type": "artists", "id": "1566"}]}}},
        {"type": "tracks", "id": "t2", "attributes": {
            "title": "COZY", "duration": "PT3M30S",
            "isrc": "USSM12204637", "trackNumber": 2, "volumeNumber": 1},
         "relationships": {"artists": {"data": [{"type": "artists", "id": "1566"}]}}},
    ],
}


class Durations(unittest.TestCase):
    def test_iso_8601_becomes_seconds(self):
        self.assertEqual(tidal.seconds("PT1H2M11S"), 3731)
        self.assertEqual(tidal.seconds("PT3M27S"), 207)
        self.assertEqual(tidal.seconds("PT45S"), 45)
        self.assertEqual(tidal.seconds("P1DT2H"), 93600)
        self.assertEqual(tidal.seconds("PT2M30.5S"), 150.5)

    def test_a_plain_number_still_works(self):
        """Losing a duration is silent, so take the forgiving path."""
        self.assertEqual(tidal.seconds(226), 226)
        self.assertEqual(tidal.seconds("226"), 226)

    def test_nonsense_is_none_rather_than_a_crash(self):
        for value in (None, "", "garbage", "PT", {}):
            self.assertIsNone(tidal.seconds(value), repr(value))


class Links(unittest.TestCase):
    def test_every_shape_of_tidal_link_is_claimed(self):
        for url in ("https://tidal.com/browse/album/350860190",
                    "https://listen.tidal.com/playlist/abc-123",
                    "https://www.tidal.com/track/77692134"):
            self.assertTrue(tidal.handles(url), url)

    def test_other_services_are_left_to_their_own_modules(self):
        for url in ("https://open.spotify.com/album/x",
                    "https://www.deezer.com/album/1",
                    "https://youtube.com/watch?v=x"):
            self.assertFalse(tidal.handles(url), url)

    def test_the_kind_and_id_are_read_from_either_host(self):
        self.assertEqual(tidal._identify("https://tidal.com/browse/album/350860190"),
                         ("album", "350860190"))
        self.assertEqual(tidal._identify("https://listen.tidal.com/playlist/ab-12"),
                         ("playlist", "ab-12"))


class TheJsonApiJoin(unittest.TestCase):
    """`included` is a flat sidecar, not a nesting. Joining it is the work."""

    def setUp(self):
        self.index = tidal._index(ALBUM)
        self.album = tidal._resource(ALBUM)

    def test_related_resources_are_found_by_type_and_id(self):
        artists = tidal._related(self.album, "artists", self.index)
        self.assertEqual([tidal._attributes(a)["name"] for a in artists], ["Beyoncé"])

    def test_relationship_order_is_preserved(self):
        tracks = tidal._related(self.album, "items", self.index)
        self.assertEqual([tidal._attributes(t)["title"] for t in tracks],
                         ["I'M THAT GIRL", "COZY"])

    def test_a_dangling_reference_is_skipped_not_fatal(self):
        album = dict(self.album)
        album["relationships"] = {"items": {"data": [
            {"type": "tracks", "id": "t1"},
            {"type": "tracks", "id": "missing"},
        ]}}
        self.assertEqual(len(tidal._related(album, "items", self.index)), 1)

    def test_the_largest_image_wins(self):
        self.assertEqual(tidal._image(tidal._attributes(self.album)),
                         "https://img/big.jpg")

    def test_a_track_becomes_a_catalogue_item_with_no_url(self):
        track = tidal._related(self.album, "items", self.index)[0]
        item = tidal._track(track, self.index, album="Renaissance")
        self.assertEqual(item.title, "I'M THAT GIRL")
        self.assertEqual(item.artist, "Beyoncé")
        self.assertEqual(item.isrc, "USSM12204636")
        self.assertEqual(item.duration, 208)
        self.assertEqual(item.track_number, 1)
        self.assertIsNone(item.url, "a catalogue item must not claim to be fetchable")
        self.assertFalse(item.fetchable)


class WorkingOutTheCountry(unittest.TestCase):
    """Tidal's catalogue differs by territory, so this is not cosmetic."""

    def test_an_rg_override_beats_the_language(self):
        # macOS writes this for English-speakers living outside the US.
        # Reading the language tag would put a London listener on the
        # American catalogue.
        self.assertEqual(tidal.region_from_tag("en_US@rg=gbzzzz"), "GB")
        self.assertEqual(tidal.region_from_tag("en_GB@rg=nozzzz"), "NO")

    def test_a_plain_tag_still_works(self):
        for tag, expected in [("en_GB", "GB"), ("nb_NO.UTF-8", "NO"),
                              ("de_DE@euro", "DE"), ("en-AU", "AU")]:
            self.assertEqual(tidal.region_from_tag(tag), expected, tag)

    def test_a_tag_with_no_region_gives_nothing_rather_than_a_guess(self):
        for tag in ("C", "C.UTF-8", "POSIX", "en", "", None):
            self.assertIsNone(tidal.region_from_tag(tag), repr(tag))


class WithoutAKey(unittest.TestCase):
    def test_the_refusal_says_where_to_get_one(self):
        import credentials
        if credentials.get("tidal", "client_id"):
            self.skipTest("this machine has Tidal keys set")
        with self.assertRaises(SourceError) as caught:
            tidal.expand("https://tidal.com/browse/album/350860190")
        message = str(caught.exception)
        self.assertIn("developer.tidal.com", message)
        self.assertIn("client id", message.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
