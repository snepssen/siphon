"""ImageMagick: still images, and the formats ffmpeg cannot reach.

There is an overlap here worth being explicit about, because two engines that
both claim images is how a registry becomes a coin toss.

ffmpeg is already installed and already converts PNG, JPEG, TIFF, BMP and GIF
perfectly well, so those keep working whether or not ImageMagick is here. What
ffmpeg on this machine cannot do is **write** WebP — it reads it and has no
encoder — and it has no idea what HEIC or AVIF are, which between them covers
every photograph taken on a phone in the last five years.

So ImageMagick goes ahead of ffmpeg in the registry and claims images when it
is installed; ffmpeg picks up what is left when it is not. The practical
result is that siphon converts images out of the box and converts *more* of
them once somebody runs one brew command — rather than refusing all images
until they do.
"""

import os
import re
import subprocess
from pathlib import Path

import formats
from engines import ConversionError
from platform_support import find, require, MissingProgram

# What ImageMagick will take in. Deliberately broad: its whole value here is
# the long tail, and a format it cannot read fails with a clear message
# anyway.
READS = {
    "jpg", "jpeg", "png", "gif", "webp", "tif", "tiff", "bmp", "heic", "heif",
    "avif", "svg", "ico", "psd", "xcf", "dds", "tga", "pcx", "ppm", "pgm",
    "pbm", "jp2", "j2k", "exr", "hdr", "cr2", "nef", "arw", "dng", "raf",
    "orf", "rw2", "pdf",
}

WRITES = {
    "jpg", "jpeg", "png", "gif", "webp", "tif", "tiff", "bmp", "ico", "avif",
    "jp2", "ppm", "pdf",
}


def can(source_path, target, kind=None):
    target = formats.resolve(target)
    if target.kind != formats.IMAGE:
        return False
    if find("magick") is None:
        return False
    suffix = Path(str(source_path)).suffix.lstrip(".").lower()
    return suffix in READS and target.container in WRITES


def plan(source_path, target, metadata=None, artwork=None):
    """What converting this image would do. Nothing is written."""
    from engines.ffmpeg import Plan, NONE, COPY, ENCODE

    target = formats.resolve(target)
    source_path = str(source_path)
    suffix = Path(source_path).suffix.lstrip(".").lower()
    detail = []

    width, height = _dimensions(source_path)
    resizing = bool(
        target.max_edge and width and height
        and max(width, height) > target.max_edge
    )

    same = _same_format(suffix, target.container)
    if same and not resizing:
        return Plan(
            action=NONE,
            summary="Already in the requested format; left alone.",
            detail=["nothing to do: the file is already what was asked for"],
            suffix=target.container,
        )

    argv = [source_path, "-auto-orient"]
    if width and height:
        detail.append(f"{width}×{height} {suffix.upper()}")
    if resizing:
        # `>` means only ever shrink. Enlarging a photograph to hit a number
        # invents detail that was never there.
        argv += ["-resize", f"{target.max_edge}x{target.max_edge}>"]
        detail.append(f"scaled down so the longest side is {target.max_edge}px")

    if target.container in {"jpg", "jpeg"}:
        # JPEG has no alpha, and the default for a transparent PNG is black.
        argv += ["-background", "white", "-alpha", "remove", "-alpha", "off"]
        detail.append("transparency flattened onto white (JPEG has no alpha)")

    if target.quality and not target.lossless:
        argv += ["-quality", str(target.quality)]
        detail.append(f"quality {target.quality}")

    if target.container == "gif":
        argv += ["-layers", "Optimize"]

    # Strip camera and location metadata by default. A holiday photo carries
    # the coordinates of the house it was taken in, and a converted copy is
    # usually one about to be sent somewhere.
    argv += ["-strip"]
    detail.append("camera and location metadata stripped")

    return Plan(
        action=ENCODE if not same else COPY,
        argv=argv,
        summary=(f"Converted to {target.container.upper()}"
                 + (" and scaled down" if resizing else "")
                 + " with ImageMagick."),
        detail=detail,
        suffix=target.container,
    )


def run(plan_obj, source_path, output_path, on_progress=None,
        should_cancel=None):
    """Carry out a plan. Returns the path written."""
    from engines.ffmpeg import NONE

    if plan_obj.action == NONE:
        return str(source_path)

    binary = _need()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(
        f".{output_path.stem}.partial{output_path.suffix}"
    )

    # `magick input … output` — the same trap as ffmpeg: the output format is
    # taken from the extension, so the real one has to stay on the end.
    argv = [binary, *plan_obj.argv, str(partial)]

    if on_progress:
        on_progress({"progress": 0.1, "status": "converting"})
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        partial.unlink(missing_ok=True)
        raise ConversionError(
            f"ImageMagick gave up on {os.path.basename(str(source_path))} "
            f"after ten minutes."
        )
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise ConversionError(f"Could not run ImageMagick: {error}") from error

    if out.returncode != 0 or not partial.exists():
        partial.unlink(missing_ok=True)
        raise ConversionError(_explain(out.stderr, source_path))

    if on_progress:
        on_progress({"progress": 1.0, "status": "converting"})
    partial.replace(output_path)
    return str(output_path)


def _dimensions(path):
    binary = find("magick")
    if binary is None:
        return None, None
    try:
        out = subprocess.run(
            [binary, "identify", "-format", "%w %h", f"{path}[0]"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    match = re.match(r"^(\d+)\s+(\d+)", (out.stdout or "").strip())
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _same_format(suffix, container):
    families = {"jpg": {"jpg", "jpeg"}, "tiff": {"tif", "tiff"}}
    return suffix in families.get(container, {container})


def _explain(stderr, source_path):
    text = stderr or ""
    name = os.path.basename(str(source_path))
    if "no decode delegate" in text:
        return (f"ImageMagick does not have a decoder for {name}. For HEIC or "
                f"AVIF that usually means libheif is missing — "
                f"`brew reinstall imagemagick` normally brings it.")
    if "no encode delegate" in text:
        return "ImageMagick was built without support for that output format."
    if "unable to open image" in text.lower():
        return f"ImageMagick could not open {name}."
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return f"ImageMagick failed on {name}: {line.strip()}"
    return f"ImageMagick failed on {name}."


def _need():
    try:
        return require("magick")
    except MissingProgram as missing:
        raise ConversionError(str(missing)) from missing
