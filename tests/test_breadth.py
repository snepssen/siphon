"""The engines beyond audio and video, and the second fetch backend.

The image tests run ffmpeg for real, because ffmpeg is always installed and
the conversions are small. The ImageMagick tests skip when it is absent, which
is honest rather than convenient: they are not asserting anything about a
program that is not there.

cobalt's tests are all fixtures. Its request path has never been run against a
real instance — see CLAUDE.md — so what is tested is the part that can be:
turning each of its documented answers into either a url or a sentence.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import credentials                    # noqa: E402
import engines                        # noqa: E402
import formats                        # noqa: E402
from engines import ffmpeg            # noqa: E402
from sources import SourceError       # noqa: E402
from sources import cobalt            # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
HAVE_MAGICK = shutil.which("magick") is not None
HAVE_PANDOC = shutil.which("pandoc") is not None
HAVE_GS = shutil.which("gs") is not None


def make_image(path, size="640x480", alpha=False):
    source = (f"color=c=red@0.0:s={size},format=rgba" if alpha
              else f"testsrc=s={size}")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source,
                    "-frames:v", "1", str(path)], check=True)
    return path


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class ImagesWorkWithoutImageMagick(unittest.TestCase):
    """The point of the split: common images convert with what is installed."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_png_to_jpeg_uses_whatever_engine_is_present(self):
        source = make_image(self.dir / "in.png")
        engine = engines.choose(source, "jpg", kind="image")
        self.assertIsNotNone(engine, "an image conversion found no engine")
        plan = engine.plan(source, "jpg")
        out = engine.run(plan, source, self.dir / "out.jpg")
        self.assertTrue(os.path.getsize(out) > 0)

    def test_the_longest_side_is_capped_and_never_stretched(self):
        source = make_image(self.dir / "wide.png", size="1600x400")
        plan = ffmpeg.plan(source, "web-image")
        out = ffmpeg.run(plan, source, self.dir / "out.jpg")
        width, height = _size(out)
        self.assertLessEqual(max(width, height), 2000)
        self.assertEqual((width, height), (1600, 400),
                         "a 1600px image must not be enlarged to hit 2000")

    def test_a_big_image_is_actually_shrunk(self):
        source = make_image(self.dir / "huge.png", size="3000x2000")
        plan = ffmpeg.plan(source, "web-image")
        out = ffmpeg.run(plan, source, self.dir / "out.jpg")
        width, height = _size(out)
        self.assertEqual(max(width, height), 2000)
        self.assertAlmostEqual(width / height, 1.5, places=2,
                               msg="aspect ratio must survive")

    def test_transparency_is_flattened_onto_white_not_black(self):
        """ffmpeg's silent default here is black, which ruins logos."""
        source = make_image(self.dir / "clear.png", size="64x64", alpha=True)
        plan = ffmpeg.plan(source, "jpg")
        self.assertTrue(any("white" in line for line in plan.detail))
        out = ffmpeg.run(plan, source, self.dir / "out.jpg")

        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", out, "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True).stdout
        brightness = sum(raw[:300]) / 300
        self.assertGreater(brightness, 240, "flattened onto black, not white")

    def test_a_format_ffmpeg_cannot_write_is_declined_not_attempted(self):
        """This build has no libwebp. Claiming webp and failing later is worse."""
        source = make_image(self.dir / "in.png")
        if ffmpeg._has_encoder("libwebp"):
            self.skipTest("this ffmpeg can write webp")
        self.assertFalse(ffmpeg.can(source, "webp"))


@unittest.skipUnless(HAVE_MAGICK, "needs ImageMagick")
class ImageMagickTakesTheImagesFirst(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_it_is_preferred_over_ffmpeg_for_images(self):
        source = make_image(self.dir / "in.png")
        engine = engines.choose(source, "jpg", kind="image")
        self.assertEqual(engine.__name__, "engines.imagemagick")

    def test_it_reaches_webp_which_this_ffmpeg_cannot(self):
        source = make_image(self.dir / "in.png")
        engine = engines.choose(source, "webp", kind="image")
        self.assertIsNotNone(engine)
        plan = engine.plan(source, "webp")
        out = engine.run(plan, source, self.dir / "out.webp")
        self.assertTrue(os.path.getsize(out) > 0)


@unittest.skipUnless(HAVE_PANDOC, "needs pandoc")
class Documents(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.source = self.dir / "note.md"
        self.source.write_text("# Title\n\nSome *text*.\n\n- one\n- two\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_markdown_becomes_a_real_document(self):
        from engines import pandoc
        for fmt in ("docx", "html", "epub", "rtf", "txt"):
            with self.subTest(fmt=fmt):
                plan = pandoc.plan(self.source, fmt)
                out = pandoc.run(plan, self.source, self.dir / f"out.{fmt}")
                self.assertGreater(os.path.getsize(out), 0)

    def test_html_and_epub_come_out_as_one_file(self):
        from engines import pandoc
        plan = pandoc.plan(self.source, "html")
        self.assertTrue(any("one file" in line for line in plan.detail))

    def test_pandoc_refuses_a_pdf_source_rather_than_mangling_it(self):
        """There is no route back out of a PDF, and pretending there is is worse."""
        from engines import pandoc
        fake = self.dir / "doc.pdf"
        fake.write_bytes(b"%PDF-1.4\n")
        self.assertFalse(pandoc.can(fake, "md"))

    def test_a_lossy_conversion_says_what_it_will_lose(self):
        from engines import pandoc
        fake = self.dir / "doc.docx"
        plan = pandoc.plan(self.source, "docx")
        self.assertTrue(plan.detail)


@unittest.skipUnless(HAVE_GS, "needs Ghostscript")
class Pdfs(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.pdf = self.dir / "pages.pdf"
        script = self.dir / "pages.ps"
        script.write_text("%!PS-Adobe-3.0\n" + "\n".join(
            f"%%Page: {n} {n}\n/Helvetica findfont 24 scalefont setfont "
            f"72 700 moveto (Page {n}) show showpage" for n in (1, 2, 3)))
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                        f"-sOutputFile={self.pdf}", str(script)], check=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_ghostscript_claims_pdfs(self):
        engine = engines.choose(self.pdf, "pdf", kind="document")
        self.assertEqual(engine.__name__, "engines.ghostscript")

    def test_every_page_comes_back_not_just_the_first(self):
        """Returning one path lost the rest to the workdir cleanup, silently."""
        from engines import ghostscript
        plan = ghostscript.plan(self.pdf, "png")
        produced = ghostscript.run(plan, self.pdf, self.dir / "out.png")
        self.assertIsInstance(produced, list)
        self.assertEqual(len(produced), 3)
        for path in produced:
            self.assertGreater(os.path.getsize(path), 0)

    def test_the_page_count_is_read_before_anything_runs(self):
        from engines import ghostscript
        self.assertEqual(ghostscript._pages(self.pdf), 3)


HAVE_SOFFICE = shutil.which("soffice") is not None


class LayoutGoesToLibreOfficeMeaningGoesToPandoc(unittest.TestCase):
    """Two engines can open a .docx, and which one should is not arbitrary.

    pandoc reads a document into its own representation, which is what makes
    markdown-to-Word possible and exact layout impossible. LibreOffice opens
    the file the way its own application would. So a Word file to PDF — where
    looking the same is the entire point of asking — is LibreOffice's, and a
    Word file to markdown is pandoc's.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.md = self.dir / "note.md"
        self.md.write_text("# Title\n\nSome *text*.\n")
        self.csv = self.dir / "data.csv"
        self.csv.write_text("name,count\nalpha,10\nbeta,20\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    @unittest.skipUnless(HAVE_SOFFICE and HAVE_PANDOC, "needs both engines")
    def test_office_to_pdf_goes_to_libreoffice(self):
        docx = self.dir / "report.docx"
        subprocess.run(["pandoc", str(self.md), "-o", str(docx)], check=True)
        engine = engines.choose(docx, "pdf", kind="document")
        self.assertEqual(engine.__name__, "engines.libreoffice")

    @unittest.skipUnless(HAVE_SOFFICE and HAVE_PANDOC, "needs both engines")
    def test_office_to_markup_goes_to_pandoc(self):
        docx = self.dir / "report.docx"
        subprocess.run(["pandoc", str(self.md), "-o", str(docx)], check=True)
        for fmt in ("md", "html", "txt"):
            with self.subTest(fmt=fmt):
                engine = engines.choose(docx, fmt, kind="document")
                self.assertEqual(engine.__name__, "engines.pandoc", fmt)

    @unittest.skipUnless(HAVE_PANDOC, "needs pandoc")
    def test_markup_sources_stay_with_pandoc(self):
        """LibreOffice cannot read markdown at all."""
        from engines import libreoffice
        self.assertFalse(libreoffice.can(self.md, "pdf"))
        self.assertFalse(libreoffice.can(self.md, "docx"))

    @unittest.skipUnless(HAVE_SOFFICE, "needs LibreOffice")
    def test_spreadsheets_are_only_offered_spreadsheet_shaped_targets(self):
        from engines import libreoffice
        self.assertTrue(libreoffice.can(self.csv, "xlsx"))
        self.assertTrue(libreoffice.can(self.csv, "pdf"))
        self.assertFalse(libreoffice.can(self.csv, "pptx"),
                         "a spreadsheet as slides is a file nobody wanted")

    @unittest.skipUnless(HAVE_SOFFICE, "needs LibreOffice")
    def test_a_spreadsheet_really_converts(self):
        from engines import libreoffice
        plan = libreoffice.plan(self.csv, "xlsx")
        out = libreoffice.run(plan, self.csv, self.dir / "out.xlsx")
        self.assertGreater(os.path.getsize(out), 0)
        with open(out, "rb") as handle:
            self.assertEqual(handle.read(2), b"PK", "not a real xlsx")

    @unittest.skipUnless(HAVE_SOFFICE, "needs LibreOffice")
    def test_each_run_gets_its_own_profile(self):
        """soffice shares one profile by default, and a second instance using
        it attaches to the first or refuses to start — which makes converting
        a folder in parallel fail for reasons that look nothing like this."""
        from engines import libreoffice
        import inspect
        source = inspect.getsource(libreoffice.run)
        self.assertIn("UserInstallation", source)

    @unittest.skipUnless(HAVE_SOFFICE, "needs LibreOffice")
    def test_a_pdf_says_it_keeps_the_original_layout(self):
        from engines import libreoffice
        plan = libreoffice.plan(self.csv, "pdf")
        self.assertTrue(any("laid out" in line for line in plan.detail))


class PdfsFromDocuments(unittest.TestCase):
    @unittest.skipUnless(HAVE_PANDOC, "needs pandoc")
    def test_markdown_to_pdf_needs_a_typesetter_and_names_one(self):
        from engines import pandoc
        directory = Path(tempfile.mkdtemp())
        try:
            source = directory / "note.md"
            source.write_text("# Title\n\ntext\n")
            if pandoc._pdf_engine() is None:
                with self.assertRaises(ConversionErrorType) as caught:
                    pandoc.plan(source, "pdf")
                self.assertIn("tectonic", str(caught.exception))
            else:
                plan = pandoc.plan(source, "pdf")
                out = pandoc.run(plan, source, directory / "out.pdf")
                with open(out, "rb") as handle:
                    self.assertEqual(handle.read(5), b"%PDF-")
        finally:
            shutil.rmtree(directory, ignore_errors=True)


from engines import ConversionError as ConversionErrorType  # noqa: E402


class CobaltAnswers(unittest.TestCase):
    """Fixtures only — the request path has never met a real instance."""

    def test_a_direct_link_is_taken(self):
        for status in ("stream", "redirect", "tunnel"):
            with self.subTest(status=status):
                url, name = cobalt._interpret(
                    {"status": status, "url": "https://x.invalid/f.mp4",
                     "filename": "f.mp4"}, "https://site/v")
                self.assertEqual(url, "https://x.invalid/f.mp4")
                self.assertEqual(name, "f.mp4")

    def test_a_picker_takes_the_first_thing_in_it(self):
        url, _ = cobalt._interpret(
            {"status": "picker", "picker": [
                {"url": "https://x.invalid/1.jpg"},
                {"url": "https://x.invalid/2.jpg"}]}, "https://site/v")
        self.assertEqual(url, "https://x.invalid/1.jpg")

    def test_an_error_becomes_a_sentence(self):
        with self.assertRaises(SourceError) as caught:
            cobalt._interpret(
                {"status": "error", "error": {"code": "error.api.content.video.age"}},
                "https://site/v")
        self.assertIn("age", str(caught.exception))

    def test_yes_without_an_address_is_still_a_failure(self):
        with self.assertRaises(SourceError):
            cobalt._interpret({"status": "stream"}, "https://site/v")

    def test_an_unknown_status_is_reported_rather_than_assumed(self):
        with self.assertRaises(SourceError) as caught:
            cobalt._interpret({"status": "invented-later"}, "https://site/v")
        self.assertIn("invented-later", str(caught.exception))

    def test_it_claims_the_prefix_even_unconfigured_so_it_can_explain(self):
        self.assertTrue(cobalt.handles("cobalt:https://youtube.com/watch?v=x"))
        self.assertFalse(cobalt.handles("https://youtube.com/watch?v=x"))

    def test_an_unconfigured_instance_says_how_to_configure_it(self):
        original = credentials.get
        credentials.get = lambda service, key: None
        try:
            with self.assertRaises(SourceError) as caught:
                cobalt.expand("cobalt:https://youtube.com/watch?v=x")
            self.assertIn("Settings", str(caught.exception))
        finally:
            credentials.get = original


class FormatCatalogue(unittest.TestCase):
    def test_every_kind_has_presets(self):
        grouped = formats.kinds()
        for kind in (formats.AUDIO, formats.VIDEO, formats.IMAGE,
                     formats.DOCUMENT):
            self.assertTrue(grouped.get(kind), f"no {kind} presets")

    def test_every_preset_explains_itself(self):
        for name, target in formats.PRESETS.items():
            self.assertTrue(target.summary, f"{name} has no summary")


def _size(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    width, height = out.split(",")[:2]
    return int(width), int(height)


if __name__ == "__main__":
    unittest.main(verbosity=2)
