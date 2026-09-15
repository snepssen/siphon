"""pandoc: documents, markup and ebooks.

The odd one out among these engines. ffmpeg and ImageMagick convert a thing
into the same thing in a different wrapper; pandoc reads a document into its
own representation and writes that out again, which means a conversion here
can genuinely lose things — a Word file's tracked changes, a PDF's exact
layout — in a way that transcoding an audio file does not. So every plan says
what will not survive, rather than leaving somebody to discover it.

One limit worth knowing before it bites: **pandoc cannot read a PDF.** It
writes them happily, given a PDF engine, but there is no route back. A PDF is
a description of marks on a page and the structure a document had before it
became one is simply not in there. `ghostscript` handles PDFs as PDFs, and
anything else wanting the text out of one needs a different kind of tool
entirely.
"""

import os
import subprocess
from pathlib import Path

import formats
from engines import ConversionError
from platform_support import find, require, MissingProgram

# suffix -> the name pandoc knows the format by. Only formats it can read.
READS = {
    "md": "markdown", "markdown": "markdown", "mdown": "markdown",
    "txt": "markdown",            # plain text is valid markdown, near enough
    "html": "html", "htm": "html", "xhtml": "html",
    "docx": "docx", "odt": "odt", "rtf": "rtf", "epub": "epub",
    "tex": "latex", "latex": "latex", "typ": "typst",
    "rst": "rst", "org": "org", "textile": "textile", "wiki": "mediawiki",
    "ipynb": "ipynb", "json": "json", "csv": "csv", "bib": "biblatex",
    "man": "man", "djot": "djot", "adoc": "asciidoc",
}

# target container -> the name pandoc writes it as.
WRITES = {
    "md": "markdown", "html": "html", "docx": "docx", "odt": "odt",
    "rtf": "rtf", "epub": "epub3", "txt": "plain", "tex": "latex",
    "rst": "rst", "org": "org", "pdf": "pdf",
}

# Losing the original's exact look is the normal outcome, not a fault, so it
# is said in advance for the conversions where people notice.
CAVEATS = {
    ("docx", "md"): "Word's comments, tracked changes and exact layout do not "
                    "survive; the text, headings, lists and tables do.",
    ("docx", "txt"): "All formatting is discarded — this is the words only.",
    ("html", "md"): "Scripts, styling and anything the page drew with them are "
                    "dropped.",
    ("epub", "md"): "Chapter files are joined into one document.",
    ("md", "docx"): "Word gets pandoc's default styling, not a house template.",
}

# PDF needs a typesetter, and pandoc ships none. Tried in order.
PDF_ENGINES = ("tectonic", "xelatex", "lualatex", "pdflatex", "typst",
               "weasyprint", "wkhtmltopdf", "prince")


def can(source_path, target, kind=None):
    target = formats.resolve(target)
    if target.kind != formats.DOCUMENT:
        return False
    if find("pandoc") is None:
        return False
    suffix = Path(str(source_path)).suffix.lstrip(".").lower()
    if suffix == "pdf":
        return False              # no route back out of a PDF
    if suffix not in READS or target.container not in WRITES:
        return False
    if target.container == "pdf" and _pdf_engine() is None:
        return False              # let the refusal say what to install
    return True


def _pdf_engine():
    for name in PDF_ENGINES:
        from shutil import which
        found = which(name)
        if found:
            return found
    return None


def plan(source_path, target, metadata=None, artwork=None):
    from engines.ffmpeg import Plan, NONE, ENCODE

    target = formats.resolve(target)
    source_path = str(source_path)
    suffix = Path(source_path).suffix.lstrip(".").lower()
    reader = READS.get(suffix)
    writer = WRITES.get(target.container)

    if reader is None:
        raise ConversionError(
            f"pandoc does not read {suffix or 'that'} files."
        )
    if writer is None:
        raise ConversionError(
            f"pandoc does not write {target.container} files."
        )
    if suffix == target.container:
        return Plan(action=NONE,
                    summary="Already in the requested format; left alone.",
                    detail=["nothing to do: the file is already what was asked for"],
                    suffix=target.container)

    detail = [f"read as {reader}, written as {writer}"]
    argv = ["--from", reader, "--to", writer, "--quiet"]

    if target.container in {"html", "epub"}:
        # One file rather than a file plus a folder of images somebody will
        # lose the moment they email it.
        argv += ["--standalone", "--embed-resources"]
        detail.append("images and styling embedded, so it is one file")
    elif target.container in {"docx", "odt", "rtf", "pdf", "tex"}:
        argv += ["--standalone"]

    if target.container == "pdf":
        engine = _pdf_engine()
        if engine is None:
            raise ConversionError(
                "pandoc can write a PDF but needs a typesetter to do it, and "
                "there is none installed. `brew install tectonic` is the "
                "smallest one that works."
            )
        argv += [f"--pdf-engine={os.path.basename(engine)}"]
        detail.append(f"typeset with {os.path.basename(engine)}")

    if metadata is not None:
        title = getattr(metadata, "title", None)
        if title:
            argv += ["--metadata", f"title={title}"]

    caveat = CAVEATS.get((suffix, target.container))
    if caveat:
        detail.append(caveat)

    return Plan(
        action=ENCODE,
        argv=argv,
        summary=f"Converted from {reader} to {writer} with pandoc.",
        detail=detail,
        suffix=target.container,
    )


def run(plan_obj, source_path, output_path, on_progress=None,
        should_cancel=None):
    from engines.ffmpeg import NONE

    if plan_obj.action == NONE:
        return str(source_path)

    binary = _need()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(
        f".{output_path.stem}.partial{output_path.suffix}"
    )

    argv = [binary, str(source_path), *plan_obj.argv, "-o", str(partial)]
    if on_progress:
        on_progress({"progress": 0.1, "status": "converting"})
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        partial.unlink(missing_ok=True)
        raise ConversionError("pandoc gave up after ten minutes.")
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise ConversionError(f"Could not run pandoc: {error}") from error

    if out.returncode != 0 or not partial.exists():
        partial.unlink(missing_ok=True)
        raise ConversionError(_explain(out.stderr, source_path))

    if on_progress:
        on_progress({"progress": 1.0, "status": "converting"})
    partial.replace(output_path)
    return str(output_path)


def _explain(stderr, source_path):
    text = stderr or ""
    name = os.path.basename(str(source_path))
    if "pdflatex not found" in text or "pdf-engine" in text:
        return ("That PDF needs a typesetter pandoc could not find. "
                "`brew install tectonic` is the smallest one that works.")
    if "Could not parse" in text or "ParseError" in text:
        return f"pandoc could not make sense of {name}."
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return f"pandoc failed on {name}: {line.strip()[:200]}"
    return f"pandoc failed on {name}."


def _need():
    try:
        return require("pandoc")
    except MissingProgram as missing:
        raise ConversionError(str(missing)) from missing
