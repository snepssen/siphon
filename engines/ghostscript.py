"""Ghostscript: PDFs, as PDFs.

Narrow on purpose. This engine does the two things a PDF is actually asked
for — make it smaller, and turn its pages into images — and refuses the thing
people wish it did, which is get the text back out with its structure intact.
That is not a gap in Ghostscript. A PDF describes marks on a page; the
headings and lists a document had before it was printed to one are not in the
file to recover.

The compression settings are Ghostscript's own `PDFSETTINGS` presets, which
are well chosen and battle-tested. Inventing better ones is a good way to
produce a file that is smaller and unreadable.
"""

import os
import re
import subprocess
from pathlib import Path

import formats
from engines import ConversionError
from platform_support import find, require, MissingProgram

# Rasterising a PDF: the device for each image format Ghostscript can write.
DEVICES = {"png": "png16m", "jpg": "jpeg", "jpeg": "jpeg", "tiff": "tiff24nc"}


def can(source_path, target, kind=None):
    suffix = Path(str(source_path)).suffix.lstrip(".").lower()
    if suffix != "pdf":
        return False
    if find("gs") is None:
        return False
    target = formats.resolve(target)
    if target.container == "pdf":
        return True
    return target.kind == formats.IMAGE and target.container in DEVICES


def plan(source_path, target, metadata=None, artwork=None):
    from engines.ffmpeg import Plan, ENCODE

    target = formats.resolve(target)
    source_path = str(source_path)
    pages = _pages(source_path)
    detail = [f"{pages} page{'s' if pages != 1 else ''}"] if pages else []

    common = ["-dNOPAUSE", "-dBATCH", "-dQUIET", "-dSAFER"]

    if target.container == "pdf":
        # /ebook is the setting that makes a scanned or image-heavy PDF
        # markedly smaller while staying readable on screen. /screen goes
        # further and starts to show.
        argv = common + [
            "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.7",
            "-dPDFSETTINGS=/ebook", "-dDetectDuplicateImages=true",
            "-dCompressFonts=true", "-dSubsetFonts=true",
        ]
        detail.append("images downsampled to 150dpi and fonts subset")
        detail.append("text and vector graphics are untouched")
        summary = "Rewrote the PDF smaller with Ghostscript."
    else:
        device = DEVICES[target.container]
        resolution = target.dpi or 150
        argv = common + [f"-sDEVICE={device}", f"-r{resolution}"]
        if device == "jpeg" and target.quality:
            argv += [f"-dJPEGQ={target.quality}"]
        detail.append(f"rendered at {resolution}dpi")
        if pages and pages > 1:
            # One file per page, numbered. A single output path cannot hold
            # forty pages and pretending otherwise silently keeps page one.
            detail.append(f"one image per page — {pages} files")
        summary = (f"Rendered the PDF to {target.container.upper()} "
                   f"with Ghostscript.")

    return Plan(
        action=ENCODE,
        argv=argv,
        summary=summary,
        detail=detail,
        suffix=target.container,
    )


def run(plan_obj, source_path, output_path, on_progress=None,
        should_cancel=None):
    binary = _need()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    multipage = any("one image per page" in line for line in plan_obj.detail)
    if multipage:
        # %d is Ghostscript's own page counter.
        pattern = output_path.with_name(
            f"{output_path.stem}-%03d{output_path.suffix}")
        target_argv = [f"-sOutputFile={pattern}"]
    else:
        partial = output_path.with_name(
            f".{output_path.stem}.partial{output_path.suffix}")
        target_argv = [f"-sOutputFile={partial}"]

    argv = [binary, *plan_obj.argv, *target_argv, str(source_path)]
    if on_progress:
        on_progress({"progress": 0.1, "status": "converting"})
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise ConversionError("Ghostscript gave up after fifteen minutes.")
    except OSError as error:
        raise ConversionError(f"Could not run Ghostscript: {error}") from error

    if out.returncode != 0:
        raise ConversionError(_explain(out.stderr or out.stdout, source_path))

    if multipage:
        made = sorted(output_path.parent.glob(
            f"{output_path.stem}-*{output_path.suffix}"))
        if not made:
            raise ConversionError("Ghostscript wrote no pages.")
        if on_progress:
            on_progress({"progress": 1.0, "status": "converting"})
        # Every page, not just the first. Returning one path here quietly
        # threw the rest away when the job cleaned up its working directory —
        # a forty-page PDF arrived as a single image of page one.
        return [str(p) for p in made]

    partial = output_path.with_name(
        f".{output_path.stem}.partial{output_path.suffix}")
    if not partial.exists():
        raise ConversionError("Ghostscript wrote nothing.")
    if on_progress:
        on_progress({"progress": 1.0, "status": "converting"})
    partial.replace(output_path)
    return str(output_path)


def _pages(path):
    binary = find("gs")
    if binary is None:
        return None
    try:
        out = subprocess.run(
            [binary, "-q", "-dNODISPLAY", "-dNOSAFER", "-c",
             f"({path}) (r) file runpdfbegin pdfpagecount = quit"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+", out.stdout or "")
    return int(match.group()) if match else None


def _explain(text, source_path):
    text = text or ""
    name = os.path.basename(str(source_path))
    if "Password" in text or "encrypted" in text.lower():
        return f"{name} is password-protected, so Ghostscript cannot open it."
    if "not a PDF" in text or "Unrecoverable error" in text:
        return f"{name} is not a PDF Ghostscript can read, or it is damaged."
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return f"Ghostscript failed on {name}: {line.strip()[:200]}"
    return f"Ghostscript failed on {name}."


def _need():
    try:
        return require("gs")
    except MissingProgram as missing:
        raise ConversionError(str(missing)) from missing
