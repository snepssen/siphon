# siphon

Fetch media from a URL, or convert what you already have. One tool, one
pipeline, on your own machine.

siphon exists because the hosted downloaders stopped working. The public
[cobalt](https://github.com/imputnet/cobalt) instance can no longer fetch from
YouTube — not because of anything wrong with cobalt, but because YouTube
blocks the addresses a popular shared service runs from. A copy running on
your own connection, making a handful of requests, does not have that problem.

It also does the other half. A downloader that hands you a webm when you
wanted an mp3, and leaves you to find something else to convert it, has done
half a job. Here a file already on your disk enters the same pipeline a URL
does, simply skipping the stage that fetches bytes — so converting an album
you already own is the same gesture as downloading one.

```sh
siphon get https://…                  # best quality, nothing re-encoded
siphon get https://… --as mp3         # fetch and make an mp3
siphon get https://…/playlist --as m4a   # a playlist, tagged and foldered
siphon convert album/ --as flac       # a folder already on this disk
siphon list https://…/playlist        # show what is in it, fetch nothing
```

## What it will not do

siphon fetches things that are served openly. It reads playlists from
streaming services as metadata — what the tracks are — and then fetches those
tracks from somewhere that serves them. **It does not decrypt protected
streams, and it will not be made to.** If a service hands out its audio under
DRM, siphon's answer is that it cannot help, and it says so in those words
rather than failing strangely.

It is not an editor, it is not a library manager, and it does not upload
anything anywhere. Nothing leaves your machine except the request that fetches
the media.

## The part that matters: it does not re-encode what it does not have to

Asking for an mp4 is usually not a conversion. The video is already H.264 and
the audio already AAC; both are legal inside an mp4; the only work needed is
moving the streams into a different wrapper. That takes about a second and
changes not one sample. Re-encoding anyway would cost minutes and quality in
exchange for nothing at all.

So siphon compares what is in the file against what you asked for, stream by
stream, and re-encodes exactly the streams that need it — then tells you which
ones those were:

```
$ siphon get 'https://www.youtube.com/watch?v=aqz-KE-bpKQ' --as mp4-720 -v
✓ Big Buck Bunny 60fps 4K.mp4  (153.7 MB)
    fetched aqz-KE-bpKQ.mp4
    Repackaged from mp4 to mp4 with the streams copied — nothing re-encoded.
    audio copied: already aac
    video copied: already h264
```

Sixteen seconds, and the packets in the output are byte-for-byte the packets
that came down the wire. There is a test that proves it.

The same reasoning runs backwards into the fetch. If you ask for an mp4,
siphon asks yt-dlp for the H.264 and AAC renditions specifically, so that the
conversion afterwards finds it has nothing to do. Ask for an `.opus` from
YouTube and the result is a pure copy of what YouTube already serves.

You can see the decision before committing to it:

```sh
siphon plan recording.wav --as flac
```

## What it runs on

Python 3.10 or newer, and two external programs it deliberately does not
bundle:

| Program | For | If it is missing |
| --- | --- | --- |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | fetching from a URL | `brew install yt-dlp` |
| [ffmpeg](https://ffmpeg.org) | converting, and muxing what yt-dlp fetches | `brew install ffmpeg` |

Both are external on purpose. yt-dlp ships a release most weeks because the
sites it reads keep changing underneath it; a copy frozen inside siphon would
be broken by the time you installed it. `brew upgrade yt-dlp` fixes siphon
without siphon being touched.

`siphon check` says what is present, what is missing, and what to type.

## Formats

`siphon formats` lists them. The short version: `mp3`, `m4a`, `opus`, `flac`,
`wav` for audio; `mp4`, `mp4-1080`, `mp4-720`, `mkv`, `webm` for video; and
`audio` or `video` for "whatever it already was, untouched".

## Keys

Most of what siphon reaches for needs nothing. Deezer answers anyone, and
YouTube, SoundCloud and Bandcamp go through yt-dlp without being told who you
are. Spotify and Tidal need a free developer key, which you register once.

`siphon keys` shows what is set and, for anything that is not, the steps to go
and find it. Keys are kept in your login keychain on macOS, and in a file
readable only by you elsewhere. They are never printed, never logged, and
never sent anywhere but to the service they belong to.

## Where files go

`~/Downloads/siphon`, unless you pass `--to`. A playlist becomes a folder,
tracks are numbered, and nothing is ever written over the top of something
already there.

## Licence

MIT. You are responsible for what you download and what you do with it.
