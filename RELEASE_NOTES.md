# siphon 0.1.0

**Paste a link and get the file. Point it at a folder you already have and get
the same thing in a different shape.**

The public [cobalt](https://github.com/imputnet/cobalt) instance can no longer
fetch from YouTube — not through any fault of cobalt's, but because YouTube
blocks the addresses a popular shared service runs from. A copy running on
your own connection does not have that problem. siphon is that, plus the half
nobody ships with it: a downloader that hands you a `.webm` when you wanted an
mp3 has done half a job.

A file already on your disk enters the same pipeline a URL does, skipping only
the stage that fetches bytes. That is why converting a folder of photographs
is the same gesture as downloading an album.

## It does not re-encode what it does not have to

Asking for an mp4 is usually not a conversion — the video is already H.264 and
the audio already AAC, and the work is moving them into a different wrapper.
siphon compares every stream against what you asked for, re-encodes only what
must be, and tells you which:

```
✓ Big Buck Bunny 60fps 4K.mp4  (153.7 MB)
    Repackaged from mp4 to mp4 with the streams copied — nothing re-encoded.
    audio copied: already aac
    video copied: already h264
```

Sixteen seconds for 153 MB, packets byte-for-byte identical to what came down
the wire. It steers the fetch too: ask for an mp4 and yt-dlp is asked for the
H.264 and AAC renditions specifically, so the conversion finds nothing to do.

## Music, and knowing when it is not sure

Deezer, Spotify and Tidal albums, playlists and tracks. siphon reads the track
list, finds each track something that will serve it, tags it, embeds the cover
and files it under the album. Deezer needs no key; Spotify works without one.

The matching is the part worth reading about. Searching "Harder, Better,
Faster, Stronger" returns the real track at 223 seconds and an unrelated
recording called "Daft Hands" at 225 — and the wanted track is 226, so the
impostor is *closer*. Running time is therefore worth a quarter of the score
and never more. Above 75% siphon acts; below 50% it refuses and names the
closest miss; in between it stops and asks, before fetching anything.

## Four kinds of thing

Audio, video, still images and documents, through one pipeline and five
engines — ffmpeg, ImageMagick, pandoc, Ghostscript and LibreOffice. Office
files to PDF keep their layout; documents to markup keep their meaning. A PDF
rendered to images gives one file per page.

## It offers to install what it needs

siphon ships no binaries, deliberately: yt-dlp releases most weeks because the
sites it reads keep changing, and a frozen copy would be broken by the time
you opened it. So the first run lists what is missing, shows the commands, and
installs all of it if you press return. It will not run `sudo` on your behalf.

## What it will not do

It fetches what is served openly. It reads streaming playlists as metadata and
fetches the tracks from somewhere that serves them. **It does not decrypt
protected streams and will not be made to.** Nothing is uploaded, there are no
accounts, and the only thing that leaves your machine is the request that
fetches the media.

---

Python 3.10+, MIT. macOS, Windows and Linux.
