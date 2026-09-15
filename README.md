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
siphon                                # open the window
siphon get https://…                  # best quality, nothing re-encoded
siphon get https://… --as mp3         # fetch and make an mp3
siphon get https://…/playlist --as m4a   # a playlist, tagged and foldered
siphon convert album/ --as flac       # a folder already on this disk
siphon list https://…/playlist        # show what is in it, fetch nothing
```

## The window

`siphon` on its own opens a page on `127.0.0.1`: paste a link, watch the queue,
set your keys. It is the same queue the command line uses — a job started in
the terminal appears in the window, and a job started in the window survives
the window being closed.

Paste a playlist and it shows you what is in it *before* fetching any of it,
with the running time, so "Fetch all 1120" is a decision rather than an
accident.

The server binds the loopback address only and every request carries a token
from the URL it opened at. Nothing on your network can reach it, and neither
can a website you happen to have open in another tab.

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

## Music

Paste a Deezer or Spotify album, playlist or track. siphon reads the track
list — titles, artists, running times, cover art, ISRCs — then finds each
track something that will actually serve it, fetches that, tags it, embeds the
cover and files it under the album.

```sh
siphon get 'https://www.deezer.com/album/302127' --as m4a
```

Tidal works the same way, but it is the only service here that cannot work
without a key — there is no public way to read a Tidal catalogue at all. A
free developer app at developer.tidal.com gives you a client id and secret to
paste into Settings. Tidal's covers come back at 1280px, the largest of the
three.

Deezer needs no key at all. Spotify works without one too, by reading the same
embed its web player uses — but that path cannot see ISRCs and stops at fifty
tracks of a long playlist, so siphon says when a list looks truncated rather
than quietly handing you a fraction of it. A free developer key, pasted into
Settings, switches to the proper API and the limits go away.

### How a track is matched, and when it refuses

This is the part worth understanding, because it is where a music downloader
usually goes quietly wrong.

The obvious approach is to search the title and take whatever is closest in
length. That is wrong often enough to matter. Searching for Daft Punk's
"Harder, Better, Faster, Stronger" — 226 seconds — returns the real track at
223 seconds and an unrelated recording called "Daft Hands" at 225. The
impostor is *closer*. A resolver that decides on running time picks it.

So running time is worth a quarter of the score and never more. The rest is
the title, and whether the artist is the one who uploaded it — an artist's own
channel, or one of YouTube's auto-generated "- Topic" uploads, counts for a
great deal. Karaoke, covers, live versions, 8D edits and sped-up uploads are
penalised, unless the track you asked for is itself called that.

Every match carries a confidence and its reasons:

```
100%  Harder, Better, Faster, Stronger -> Daft Punk    title matches, channel is the artist, length exact
 73%  (Daft Hands - Harder, Better...)               title matches, length exact
 49%  (Daft Punk - Around the World / Harder...)     length 19s out, looks like a live version
```

Above 75% it is acted on without comment. Below 50% nothing is downloaded and
the closest miss is named instead. In between, siphon stops and asks, before
fetching anything:

```
Not sure about: Erik Satie — Gnossienne No.1  (4m 40s)
  1.  71%   4m 10s  Erik Satie - Topic     Gnossienne No. 1 (Remastered)
          title matches, official auto-generated upload, length 30s out
  2.  69%   4m 11s  The Flaming Piano      Erik Satie - Gnossienne No. 1
          title matches, artist named in the title, length 29s out
  3.  68%   3m 25s  DistantMirrors         Erik Satie - Gnossienne No.1
          title matches, artist named in the title, length 75s out
  4.  64%  30m 35s  αλώβητος κανένας       Erik Satie - Gnossienne No.1 (Extended)
          title matches, artist named in the title, length 1555s out
  s. skip this one
Which one? [1]
```

The window shows the same choice as a card with a button on each option. A
waiting job holds its place in the queue and survives siphon being closed,
because the question is still worth asking tomorrow.

For unattended runs, `--pick best` takes the top match without asking and
`--pick skip` passes over anything uncertain. Piped into a script with no
terminal to ask, siphon takes the best rather than hanging on a prompt nobody
can see.

## Getting what it needs

`./start.sh` checks before it opens anything, and offers to install whatever
is missing. Pressing return installs all of it.

```
siphon needs a few programs it does not ship.

  ffmpeg     needed  — converting audio and video, and muxing what yt-dlp fetches
  magick     extra   — still image formats ffmpeg handles badly or not at all

  brew install ffmpeg
  brew install imagemagick

Install all 2 of these with Homebrew? [Y/n]
```

`siphon setup` asks again at any time, and the same offer appears in the
window's settings with a button. On a system whose package manager needs root,
siphon prints the command rather than running it — it will not invoke `sudo`
on your behalf.

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

`siphon formats` lists them, grouped, and says which need a program you have
not installed yet.

| | |
| --- | --- |
| **Audio** | `mp3` `m4a` `opus` `flac` `wav` `audio` |
| **Video** | `mp4` `mp4-1080` `mp4-720` `mkv` `webm` `video` |
| **Images** | `jpg` `png` `webp` `tiff` `gif` `web-image` |
| **Documents** | `pdf` `docx` `epub` `html` `md` `txt` `rtf` |

`audio` and `video` mean "whatever it already was, untouched". `web-image` is
a JPEG with its longest side capped at 2000px.

Images and documents go through the same pipeline everything else does, so
`siphon convert photos/ --as web-image` works on a folder, and a PDF rendered
to images gives you one file per page rather than a picture of page one.

Two notes on what is honest here. Converting a document is not like
transcoding audio: pandoc reads it into its own representation and writes it
out again, so tracked changes and exact layout do not survive — every plan
says what it will lose before it runs. And **there is no way back out of a
PDF**: a PDF describes marks on a page, and the structure the document had
before it became one is not in the file. siphon makes PDFs smaller and renders
their pages; it does not pretend to un-print them.

## A second way to fetch

siphon can install and run its own [cobalt](https://github.com/imputnet/cobalt)
— from source, no Docker:

```sh
siphon cobalt start        # fetches it, installs it, runs it on 127.0.0.1:9000
siphon get 'cobalt:https://…'
```

It binds the loopback address only, and `siphon cobalt remove` deletes it and
everything it pulled in. The buttons in Settings do the same thing.

`siphon cobalt start` also brings up a YouTube token provider
([bgutil](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)) and a small
bridge, because cobalt cannot mint YouTube's `poToken` itself. `siphon cobalt
tokens` shows their state.

**yt-dlp uses that provider too**, automatically, whenever it is running —
siphon loads bgutil's yt-dlp plugin and points it at the same server. YouTube
asks for a token on some videos and not others, and refuses quietly when it
does not get one; yt-dlp copes without today, and this is already in place for
when it stops coping. With the server down, nothing changes and nothing waits
on it.

```sh
siphon cobalt tokens start     # provider + bridge; yt-dlp picks it up too
siphon cobalt tokens           # what is running
```

Two honest notes. cobalt's own native dependency does not build against the
very newest Node, so siphon looks for an LTS one beside it (`brew install
node@22`) and uses that for cobalt alone.

And **YouTube through cobalt does not work**, tokens or no tokens. cobalt
loads them successfully and the tunnel still comes back empty; it is an open
bug for self-hosters ([#1465](https://github.com/imputnet/cobalt/issues/1465),
[#1475](https://github.com/imputnet/cobalt/issues/1475)) that the documented
workaround does not fix either. siphon fetches YouTube with yt-dlp, which is
unaffected — use cobalt for the sites where yt-dlp is the one having a bad
week.

They fail differently — two implementations of the same awkward job, written
by different people against the same moving targets — so a site that has
broken for one is often fine on the other. yt-dlp keeps everything by default;
this is one setting away when you need it.

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
