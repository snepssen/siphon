"""LibreOffice: office documents, spreadsheets and presentations.

The overlap with pandoc is real and the division between them is not
arbitrary, so it is worth stating.

**pandoc converts meaning.** It reads a document into its own representation
and writes it out again, which is what makes markdown to Word possible and
what makes exact layout impossible. **LibreOffice converts documents.** It
opens the file the way the application that made it would, and saves it as
something else — so a Word file becomes a PDF that looks like the Word file,
and a spreadsheet stays a spreadsheet.

So LibreOffice takes what pandoc cannot read at all (`.xlsx`, `.pptx`, and the
legacy `.doc`/`.xls`/`.ppt`), and it takes office-to-PDF, where fidelity is
the entire point of asking. pandoc keeps the markup formats, where meaning is.

**It is one process, and it does not like two.** `soffice` uses a shared user
profile and a second instance will either refuse to start or quietly attach to
the first. Every run here gets a private profile directory, which is what
makes converting a folder of documents in parallel work rather than fail
confusingly.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import formats
from engines import ConversionError
from platform_support import find, require, MissingProgram

# What LibreOffice should be asked for. Deliberately not everything it can
# open: the markup formats belong to pandoc, which does them better.
READS = {
    "doc", "docx", "odt", "rtf", "dot", "dotx", "wps",
    "xls", "xlsx", "ods", "csv", "tsv", "xlsm", "xlt",
    "ppt", "pptx", "odp", "pps", "ppsx", "pot",
    "vsd", "vsdx", "pub", "abw",
}

# target container -> the filter name LibreOffice writes it with.
WRITES = {
    "pdf": "pdf", "docx": "docx", "odt": "odt", "rtf": "rtf",
    "xlsx": "xlsx", "ods": "ods", "csv": "csv",
    "pptx": "pptx", "odp": "odp",
    "html": "html", "txt": "txt",
}

# Where each source kind sensibly goes. Asking for a spreadsheet as a
# presentation produces a file, and not one anybody wanted.
SPREADSHEETS = {"xls", "xlsx", "ods", "csv", "tsv", "xlsm", "xlt"}
PRESENTATIONS = {"ppt", "pptx", "odp", "pps", "ppsx", "pot"}

SPREADSHEET_TARGETS = {"xlsx", "ods", "csv", "pdf", "html"}
PRESENTATION_TARGETS = {"pptx", "odp", "pdf", "html"}
DOCUMENT_TARGETS = {"pdf", "docx", "odt", "rtf", "html", "txt"}


def can(source_path, target, kind=None):
    target = formats.resolve(target)
    if target.kind != formats.DOCUMENT:
        return False
    if find("soffice") is None:
        return False

    suffix = Path(str(source_path)).suffix.lstrip(".").lower()
    if suffix not in READS or target.container not in WRITES:
        return False
    if suffix == target.container:
        return False

    # Let pandoc keep what it does better: anything it can read, going to a
    # markup target, is meaning rather than layout.
    if target.container in {"md", "txt", "html"} and suffix in {"docx", "odt", "rtf"}:
        return False

    if suffix in SPREADSHEETS:
        return target.container in SPREADSHEET_TARGETS
    if suffix in PRESENTATIONS:
        return target.container in PRESENTATION_TARGETS
    return target.container in DOCUMENT_TARGETS


def plan(source_path, target, metadata=None, artwork=None):
    from engines.ffmpeg import Plan, ENCODE

    target = formats.resolve(target)
    source_path = str(source_path)
    suffix = Path(source_path).suffix.lstrip(".").lower()
    detail = [f"opened as LibreOffice opens a {suffix.upper()}"]

    if target.container == "pdf":
        detail.append("laid out as the original was, not re-typeset")
    if suffix in SPREADSHEETS and target.container == "csv":
        detail.append("only the first sheet — CSV holds one table")
    if target.container in {"docx", "pptx", "xlsx"}:
        detail.append("saved in Microsoft's format, which is a conversion of "
                      "its own; expect small differences in spacing")

    return Plan(
        action=ENCODE,
        argv=["--headless", "--norestore", "--invisible", "--nolockcheck",
              "--nodefault", "--nofirststartwizard",
              "--convert-to", WRITES[target.container]],
        summary=f"Converted to {target.container.upper()} with LibreOffice.",
        detail=detail,
        suffix=target.container,
    )


def run(plan_obj, source_path, output_path, on_progress=None,
        should_cancel=None):
    """Convert in a private profile, then move the result where it belongs."""
    binary = _need()
    source_path = Path(source_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # LibreOffice writes <source stem>.<ext> into --outdir and offers no way
    # to name the file, so it converts into a scratch directory and the result
    # is moved. The profile goes in there too: soffice shares one by default,
    # and a second instance using it will attach to the first or refuse to
    # start, which makes converting a folder in parallel fail for reasons that
    # look nothing like the cause.
    scratch = Path(tempfile.mkdtemp(prefix="siphon-soffice-"))
    profile = scratch / "profile"
    try:
        argv = [binary, f"-env:UserInstallation=file://{profile}",
                *plan_obj.argv, "--outdir", str(scratch), str(source_path)]
        if on_progress:
            on_progress({"progress": 0.1, "status": "converting"})
        try:
            done = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=900)
        except subprocess.TimeoutExpired:
            raise ConversionError(
                f"LibreOffice gave up on {source_path.name} after fifteen "
                f"minutes."
            )
        except OSError as error:
            raise ConversionError(f"Could not run LibreOffice: {error}") from error

        produced = [p for p in scratch.iterdir()
                    if p.is_file() and p.suffix.lstrip(".").lower()
                    == plan_obj.suffix.lower()]
        if not produced:
            raise ConversionError(_explain(done, source_path))

        if on_progress:
            on_progress({"progress": 1.0, "status": "converting"})
        shutil.move(str(produced[0]), str(output_path))
        return str(output_path)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _explain(done, source_path):
    text = (done.stderr or "") + (done.stdout or "")
    name = Path(source_path).name
    if "Error: source file could not be loaded" in text:
        return (f"LibreOffice could not open {name}. It may be corrupt, or "
                f"password-protected.")
    if "no export filter" in text.lower():
        return "LibreOffice has no filter for that combination of formats."
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return f"LibreOffice failed on {name}: {line.strip()[:200]}"
    return (f"LibreOffice produced nothing for {name} and said nothing about "
            f"why.")


def _need():
    try:
        return require("soffice")
    except MissingProgram as missing:
        raise ConversionError(str(missing)) from missing
