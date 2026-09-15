"""The promises siphon makes, as tests that fail when it stops keeping them.

These are deliberately about behaviour a user would notice, not about internal
shapes. Nothing here touches the network: the fixtures are generated with
ffmpeg, so the suite runs on a machine with no connection and gives the same
answer every time.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engines            # noqa: E402
import formats            # noqa: E402
import library            # noqa: E402
import model              # noqa: E402
import paths              # noqa: E402
from engines import ffmpeg   # noqa: E402
from model import Item, Job  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def make_media(path, seconds=2, with_video=False):
    """A small real media file, so the engine is tested against ffmpeg itself."""
    argv = ["ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if with_video:
        argv += ["-f", "lavfi", "-i",
                 f"testsrc=size=320x240:rate=15:duration={seconds}"]
    argv += ["-c:a", "aac"]
    if with_video:
        argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest"]
    argv += [str(path)]
    subprocess.run(argv, check=True, capture_output=True)
    return path


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class SourceIsNeverModified(unittest.TestCase):
    """The first promise: converting your file does not touch your file."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.source = make_media(self.dir / "source.m4a")
        self.before = self.source.read_bytes()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_conversion_leaves_the_source_byte_identical(self):
        plan = ffmpeg.plan(self.source, "mp3")
        ffmpeg.run(plan, self.source, self.dir / "out.mp3")
        self.assertEqual(self.before, self.source.read_bytes())

    def test_placing_a_no_op_copies_rather_than_moves(self):
        destination = self.dir / "elsewhere" / "source.m4a"
        library.place(self.source, destination, move=False)
        self.assertTrue(self.source.exists(), "the original was moved away")
        self.assertEqual(self.before, self.source.read_bytes())

    def test_placing_never_overwrites(self):
        first = library.place(make_media(self.dir / "a.m4a"), self.dir / "out.m4a")
        second = library.place(make_media(self.dir / "b.m4a"), self.dir / "out.m4a")
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.exists(first) and os.path.exists(second))


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class ARemuxIsARemux(unittest.TestCase):
    """The second promise: a container change never re-encodes."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _packet_crcs(self, path):
        """Hashes of the encoded packets — what 'not re-encoded' actually means.

        Decoded audio is the wrong thing to compare: containers disagree about
        leading padding, so a faithful copy can decode a few samples adrift.
        The packets themselves must be identical.
        """
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-c", "copy",
             "-f", "framecrc", "-"],
            capture_output=True, text=True, check=True,
        )
        # Grouped by stream, because two things about a packet are the
        # container's business rather than the data's, and both of them change
        # legitimately across a faithful remux:
        #
        #   the timestamps, which are in the container's own timebase, so mp4
        #   and mkv give the very same packet different numbers; and
        #
        #   the interleaving, since a container decides for itself how to
        #   braid the audio and video packets together.
        #
        # What must not change is each stream's own run of packets. So: size
        # and checksum, per stream, in order.
        streams = {}
        for line in out.stdout.splitlines():
            if not line or line.startswith("#"):
                continue
            fields = [f.strip() for f in line.split(",")]
            streams.setdefault(fields[0], []).append((fields[4], fields[5]))
        return streams

    def test_mp4_to_mkv_copies_every_packet(self):
        source = make_media(self.dir / "source.mp4", with_video=True)
        plan = ffmpeg.plan(source, "mkv")
        self.assertEqual(plan.action, ffmpeg.COPY)
        self.assertTrue(plan.lossless)
        output = ffmpeg.run(plan, source, self.dir / "out.mkv")
        before, after = self._packet_crcs(source), self._packet_crcs(output)
        self.assertEqual(sorted(before), sorted(after))
        for stream, packets in before.items():
            self.assertEqual(packets, after[stream],
                             f"stream {stream} changed across the remux")

    def test_a_file_already_in_the_target_format_does_nothing(self):
        source = make_media(self.dir / "source.m4a")
        plan = ffmpeg.plan(source, "m4a")
        self.assertEqual(plan.action, ffmpeg.NONE)
        self.assertEqual(plan.argv, [])

    def test_an_incompatible_codec_is_re_encoded_and_says_so(self):
        source = make_media(self.dir / "source.m4a")
        plan = ffmpeg.plan(source, "flac")
        self.assertEqual(plan.action, ffmpeg.ENCODE)
        self.assertTrue(plan.reencodes_audio)
        self.assertTrue(any("re-encoded" in line for line in plan.detail))

    def test_failure_leaves_no_partial_file_behind(self):
        source = self.dir / "not-media.m4a"
        source.write_bytes(b"this is not a media file")
        with self.assertRaises(engines.ConversionError):
            plan = ffmpeg.plan(source, "mp3")
            ffmpeg.run(plan, source, self.dir / "out.mp3")
        strays = [p.name for p in self.dir.iterdir() if "partial" in p.name]
        self.assertEqual(strays, [])


class FormatFacts(unittest.TestCase):
    def test_containers_refuse_what_they_cannot_hold(self):
        self.assertFalse(formats.can_hold("mp4", "vp9", "video"))
        self.assertTrue(formats.can_hold("mkv", "vp9", "video"))
        self.assertTrue(formats.can_hold("mp4", "h264", "video"))
        self.assertFalse(formats.can_hold("mp3", "aac", "audio"))

    def test_pcm_is_matched_by_family_not_by_spelling(self):
        self.assertTrue(formats.can_hold("wav", "pcm_s32le", "audio"))

    def test_aliases_resolve(self):
        self.assertEqual(formats.resolve("1080p").name, "mp4-1080")
        self.assertEqual(formats.resolve(".MP3").name, "mp3")
        with self.assertRaises(formats.UnknownFormat):
            formats.resolve("realaudio")


class Naming(unittest.TestCase):
    def test_a_collection_item_is_numbered_and_foldered(self):
        item = Item(title="Track", artist="Band", collection="Album",
                    collection_index=4, extra={"collection_size": 10})
        destination = library.destination(item, "mp3", "/tmp/base")
        self.assertEqual(destination.parent.name, "Album")
        self.assertTrue(destination.name.startswith("04 "))

    def test_names_that_would_break_elsewhere_are_repaired(self):
        self.assertNotIn("/", paths.safe_name("AC/DC"))
        self.assertTrue(paths.safe_name("con.mp3").startswith("_"))
        self.assertEqual(paths.safe_name(""), "untitled")


class StateSurvivesRestarts(unittest.TestCase):
    def test_unknown_fields_do_not_break_loading(self):
        data = Job(item=Item(title="x")).to_dict()
        data["invented_later"] = True
        data["item"]["also_invented"] = 1
        self.assertEqual(Job.from_dict(data).item.title, "x")

    def test_a_job_that_was_running_is_requeued_not_assumed_done(self):
        import jobs as jobs_module
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "queue.json"
            first = jobs_module.Queue(state_path=state)
            created = first.add([Item(url="https://example.invalid/x")], "mp3")
            created[0].state = model.RUNNING
            first._save()

            second = jobs_module.Queue(state_path=state)
            second.load()
            restored = second.get(created[0].id)
            self.assertEqual(restored.state, model.QUEUED)


class ABatchIsOneAddition(unittest.TestCase):
    """Not one album — the same playlist added twice is two batches.

    Keying a batch on the collection's name merged them, so a second copy of
    a fourteen-track album reported itself as one batch of twenty-eight and a
    single "Stop all" would have stopped both.
    """

    def setUp(self):
        import jobs as jobs_module
        self.directory = tempfile.TemporaryDirectory()
        self.queue = jobs_module.Queue(
            workers=1, state_path=Path(self.directory.name) / "queue.json")

    def tearDown(self):
        self.queue.stop(wait=False)
        self.directory.cleanup()

    def _album(self):
        return [Item(title=f"Track {n}", artist="Band", collection="An Album",
                     collection_index=n) for n in range(1, 5)]

    def test_two_additions_of_the_same_album_do_not_merge(self):
        first = self.queue.add(self._album(), "mp3")
        second = self.queue.add(self._album(), "flac")
        self.assertIsNotNone(first[0].batch)
        self.assertNotEqual(first[0].batch, second[0].batch)

    def test_one_addition_shares_a_batch(self):
        created = self.queue.add(self._album(), "mp3")
        self.assertEqual(len({job.batch for job in created}), 1)

    def test_a_single_item_gets_no_batch(self):
        """Wrapping one job in a batch card would be ceremony around nothing."""
        created = self.queue.add([Item(title="Just one")], "mp3")
        self.assertIsNone(created[0].batch)

    def test_the_name_is_still_on_the_items(self):
        """The key is an id now, so the label has to come from somewhere."""
        created = self.queue.add(self._album(), "mp3")
        self.assertEqual(created[0].item.collection, "An Album")


class CredentialsAreNotLeaked(unittest.TestCase):
    def test_status_shows_only_the_last_four_characters(self):
        import credentials
        redacted = credentials._redact("abcdefghijklmnop")
        self.assertTrue(redacted.endswith("mnop"))
        self.assertNotIn("abcdefghijkl", redacted)

    def test_every_service_explains_where_to_get_its_keys(self):
        import credentials
        for service in credentials.SERVICES.values():
            self.assertTrue(service.steps, f"{service.key} has no instructions")
            self.assertTrue(service.url, f"{service.key} has no link")
            self.assertTrue(service.why, f"{service.key} does not say why")


if __name__ == "__main__":
    unittest.main(verbosity=2)
