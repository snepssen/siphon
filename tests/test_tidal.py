"""Tidal's JSON:API, tested against the shape a real response actually has.

Everything Tidal needs a key for is untestable here, so what is tested is what
does not: parsing its links, the refusal when no key is set, ISO 8601
durations, and the JSON:API join — `data` plus a flat `included` sidecar,
which is the part most likely to be got subtly wrong.

The fixture below is cut down from a genuine
`GET /v2/albums/1550545?include=items.artists,artists,coverArt` response, and
every field name in it is one the real service sent. That matters more than it
sounds: an earlier version of this file invented `imageLinks` and put
`trackNumber` in a track's attributes, and both were wrong. A fixture that
agrees with the code instead of the service tests nothing at all.

The two shapes worth knowing, because neither is where you would look first:
a track's position lives in the **meta of the album's reference to it**, not
on the track; and cover art is a `coverArt` relationship to an `artworks`
resource holding the same image at seven sizes.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sources import tidal          # noqa: E402
from sources import SourceError    # noqa: E402

ALBUM = {
    "data": {
        "id": "1550545",
        "type": "albums",
        "attributes": {
            "title": "Discovery",
            "barcodeId": "0724384960650",
            "numberOfVolumes": 1,
            "numberOfItems": 2,
            "duration": "PT1H1M9S",
            "explicit": False,
            "releaseDate": "2001-03-12",
            "externalLinks": [
                {"href": "https://tidal.com/browse/album/1550545",
                 "meta": {"type": "TIDAL_SHARING"}},
            ],
        },
        "relationships": {
            "artists": {"data": [{"id": "8847", "type": "artists"}]},
            "coverArt": {"data": [{"id": "AmX9grPBhCq4a8UZmFB",
                                   "type": "artworks"}]},
            # The position of each track lives here, in the meta of the
            # reference — not on the track resource, which has no idea where
            # it sits.
            "items": {"data": [
                {"id": "1550546", "type": "tracks",
                 "meta": {"volumeNumber": 1, "trackNumber": 1,
                          "itemCursor": "4DvnLdb7Pr8"}},
                {"id": "1550547", "type": "tracks",
                 "meta": {"volumeNumber": 1, "trackNumber": 2,
                          "itemCursor": "9QrtKmc2Xz1"}},
            ]},
        },
    },
    "included": [
        {"id": "8847", "type": "artists", "attributes": {"name": "Daft Punk"}},
        {"id": "AmX9grPBhCq4a8UZmFB", "type": "artworks", "attributes": {
            "mediaType": "IMAGE",
            "files": [
                {"href": "https://resources.tidal.com/…/80x80.jpg",
                 "meta": {"width": 80, "height": 80}},
                {"href": "https://resources.tidal.com/…/1280x1280.jpg",
                 "meta": {"width": 1280, "height": 1280}},
                {"href": "https://resources.tidal.com/…/640x640.jpg",
                 "meta": {"width": 640, "height": 640}},
            ]}},
        {"id": "1550546", "type": "tracks", "attributes": {
            "title": "One More Time", "version": None, "isrc": "GBDUW0000053",
            "duration": "PT5M20S", "explicit": False,
            "externalLinks": [{"href": "https://tidal.com/browse/track/1550546",
                               "meta": {"type": "TIDAL_SHARING"}}]},
         "relationships": {"artists": {"data": [{"id": "8847", "type": "artists"}]}}},
        {"id": "1550547", "type": "tracks", "attributes": {
            "title": "Aerodynamic", "version": "Remastered",
            "isrc": "GBDUW0000057", "duration": "PT3M32S", "explicit": False},
         "relationships": {"artists": {"data": [{"id": "8847", "type": "artists"}]}}},
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
        self.assertEqual([tidal._attributes(a)["name"] for a in artists],
                         ["Daft Punk"])

    def test_relationship_order_is_preserved(self):
        tracks = tidal._related(self.album, "items", self.index)
        self.assertEqual([tidal._attributes(t)["title"] for t in tracks],
                         ["One More Time", "Aerodynamic"])

    def test_the_reference_meta_survives_the_join(self):
        """Lose this and every album comes out unnumbered."""
        linked = tidal._linked(self.album, "items", self.index)
        self.assertEqual([meta.get("trackNumber") for _, meta in linked], [1, 2])
        self.assertEqual([meta.get("volumeNumber") for _, meta in linked], [1, 1])

    def test_a_dangling_reference_is_skipped_not_fatal(self):
        album = dict(self.album)
        album["relationships"] = {"items": {"data": [
            {"type": "tracks", "id": "1550546", "meta": {"trackNumber": 1}},
            {"type": "tracks", "id": "does-not-exist", "meta": {"trackNumber": 2}},
        ]}}
        self.assertEqual(len(tidal._related(album, "items", self.index)), 1)

    def test_cover_art_is_followed_and_the_largest_size_taken(self):
        """Seven sizes of the same picture; the file may outlive the screen."""
        self.assertEqual(tidal.artwork_url(self.album, self.index),
                         "https://resources.tidal.com/…/1280x1280.jpg")

    def test_the_web_address_comes_from_external_links(self):
        attributes = tidal._attributes(self.album)
        self.assertEqual(tidal.external_url(attributes),
                         "https://tidal.com/browse/album/1550545")
        self.assertIsNone(tidal.external_url({}))

    def test_a_track_becomes_a_catalogue_item_with_no_url(self):
        track, meta = tidal._linked(self.album, "items", self.index)[0]
        item = tidal._track(track, self.index, album="Discovery", meta=meta)
        self.assertEqual(item.title, "One More Time")
        self.assertEqual(item.artist, "Daft Punk")
        self.assertEqual(item.isrc, "GBDUW0000053")
        self.assertEqual(item.duration, 320)
        self.assertEqual(item.track_number, 1)
        self.assertEqual(item.disc_number, 1)
        self.assertEqual(item.webpage_url,
                         "https://tidal.com/browse/track/1550546")
        self.assertIsNone(item.url, "a catalogue item must not claim to be fetchable")
        self.assertFalse(item.fetchable)

    def test_a_version_is_folded_into_the_title(self):
        """Tidal splits "Aerodynamic" and "Remastered"; everywhere else joins them."""
        track, meta = tidal._linked(self.album, "items", self.index)[1]
        item = tidal._track(track, self.index, meta=meta)
        self.assertEqual(item.title, "Aerodynamic (Remastered)")


class GettingTheWholeList(unittest.TestCase):
    """The embedded relationship is page one, and paging restarts from page one.

    A real 25-track playlist came back with 45 tracks in it: twenty from the
    embedded relationship, then twenty-five more from the paging endpoint,
    which does not continue from where the embedded list stopped. `_complete`
    is what decides between the two rather than adding them together.
    """

    def setUp(self):
        self.index = tidal._index(ALBUM)
        self.album = tidal._resource(ALBUM)

    def test_a_complete_embedded_list_is_used_as_is(self):
        # numberOfItems is 2 in the fixture and two items are embedded.
        called = []

        def explode(*args, **kwargs):
            called.append(args)
            raise AssertionError("must not page a list that is already whole")

        original, tidal._page = tidal._page, explode
        try:
            got = tidal._complete(self.album, "albums/x/relationships/items",
                                  "GB", self.index)
        finally:
            tidal._page = original
        self.assertEqual(len(got), 2)
        self.assertEqual(called, [])

    def test_a_short_embedded_list_is_replaced_not_appended(self):
        short = json.loads(json.dumps(ALBUM))
        short["data"]["attributes"]["numberOfItems"] = 4
        index = tidal._index(short)
        album = tidal._resource(short)

        paged = [(entry, {"trackNumber": n})
                 for n, entry in enumerate(
                     [i for i in short["included"] if i["type"] == "tracks"] * 2,
                     start=1)]
        original, tidal._page = tidal._page, lambda *a, **k: paged
        try:
            got = tidal._complete(album, "albums/x/relationships/items",
                                  "GB", index)
        finally:
            tidal._page = original
        self.assertEqual(len(got), 4, "20 embedded + 25 paged is the bug")

    def test_a_failed_page_falls_back_to_what_was_embedded(self):
        short = json.loads(json.dumps(ALBUM))
        short["data"]["attributes"]["numberOfItems"] = 40
        index = tidal._index(short)
        album = tidal._resource(short)
        original, tidal._page = tidal._page, lambda *a, **k: []
        try:
            got = tidal._complete(album, "albums/x/relationships/items",
                                  "GB", index)
        finally:
            tidal._page = original
        self.assertEqual(len(got), 2, "a partial list beats no list")


class ARejectedKeyIsNotAMissingKey(unittest.TestCase):
    """Two different problems with two different remedies.

    Written after a real rotation: the secret was changed in Tidal's
    dashboard, siphon was still holding the old one, and the message it showed
    was "it may need a key" — advice for somebody who has none, given to
    somebody whose key was simply out of date.
    """

    def setUp(self):
        import credentials
        self.credentials = credentials
        self.original = credentials.get

    def tearDown(self):
        self.credentials.get = self.original

    def test_with_a_key_set_it_says_the_key_was_rejected(self):
        self.credentials.get = lambda service, key: "something"
        message = tidal._rejected(Exception("openapi.tidal.com refused the request."))
        self.assertIn("rejected", message)
        self.assertIn("rotated", message)
        self.assertNotIn("does not have one", message)

    def test_with_no_key_set_it_says_where_to_get_one(self):
        self.credentials.get = lambda service, key: None
        message = tidal._rejected(Exception("openapi.tidal.com refused the request."))
        self.assertIn("developer.tidal.com", message)

    def test_an_unrelated_failure_is_passed_through_unchanged(self):
        self.credentials.get = lambda service, key: "something"
        message = tidal._rejected(Exception("Could not reach openapi.tidal.com."))
        self.assertIn("Could not reach", message)

    def test_a_rejection_clears_the_cached_token(self):
        """Otherwise the next call reuses a token minted with the dead key."""
        self.credentials.get = lambda service, key: "something"
        tidal._token["value"], tidal._token["expires"] = "stale", 9e9
        tidal._rejected(Exception("refused the request"))
        self.assertIsNone(tidal._token["value"])


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
    """The refusal is tested with the keys stubbed away, not skipped.

    This used to skip itself on any machine that had Tidal keys set — which is
    every machine where the feature works, so the check quietly stopped
    running exactly where it mattered. Stubbing the lookup tests the same
    thing everywhere, and never touches the real keychain.
    """

    def setUp(self):
        import credentials
        self.credentials = credentials
        self.original = credentials.get
        credentials.get = lambda service, key: None

    def tearDown(self):
        self.credentials.get = self.original

    def test_the_refusal_says_where_to_get_one(self):
        with self.assertRaises(SourceError) as caught:
            tidal.expand("https://tidal.com/browse/album/1550545")
        message = str(caught.exception)
        self.assertIn("developer.tidal.com", message)
        self.assertIn("client id", message.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
