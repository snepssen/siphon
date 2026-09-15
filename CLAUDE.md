# siphon — working context

Fetch media from a URL, or convert what is already on the disk, through one
pipeline. Python 3 standard library only; yt-dlp and ffmpeg as external
programs; MIT.

Read `README.md` for what it does. This file is the short version: what will
bite you if you don't know it.

---

## The scope fence

**Do not build these. They are not oversights.**

- **Decrypting DRM.** siphon reads catalogue metadata from streaming services
  and fetches the audio from somewhere that serves it openly. It does not
  touch protected streams. This is not a technical limitation to be worked
  around later; it is the line the project is on the right side of.
- Accounts, uploads, publishing, sync, or anything that sends media outward.
- Editing. Trimming, joining, levelling, filtering. Conversion is a change of
  format, not a change of content — `media-preflight` is the sibling that
  measures media, and neither of them edits it.
- A library manager. Files are named and foldered on the way out and then
  forgotten about. siphon does not keep an index of what you have.
- Re-implementing yt-dlp's extractors. When a site breaks, the fix is
  upstream.

## Five promises the code keeps

**The source is never modified.** Not by conversion, not by tagging, not by
placement. Where a local file needs no conversion, `jobs._process` sets
`produced_is_original` and `library.place` copies rather than moves. There is
a test that compares the source's bytes before and after; keep it.

**Nothing is overwritten.** `paths.unique` finds a free name. A tool that
silently replaced a file somebody had already downloaded is a tool nobody
trusts twice.

**A remux is a remux.** `engines.ffmpeg.plan` compares each stream against the
target before anything runs, and re-encodes only what genuinely cannot be
copied. `-c copy` where the codecs already fit. The test proves it by
comparing packet checksums, not decoded audio — see the trap below.

**Nothing is guessed silently.** `plan` returns a sentence and a list of
reasons, and both are shown. A conversion that cannot be described in a
sentence does not belong in the planner.

The resolver keeps half of this and the half it keeps should be understood
exactly. Below `resolve.FLOOR` nothing is downloaded and the near-miss is
named. Between the floor and `CONFIDENT` the track *is* fetched, and the job
notes that the match was uncertain along with the reasons and the runners-up —
which is shown after it downloads, not before. The missing piece is a job
state that waits for somebody to choose; `item.extra["match"]["alternatives"]`
is already carried for exactly that, and until it exists this promise should
not be described as fully kept.

**The queue survives being closed.** Jobs are written to disk on every state
change, and anything found still marked `running` at startup goes back to
`queued` — because a job that was running when the process died is a job that
did not finish.

## The traps

**Comparing decoded audio does not prove a remux was lossless.** Containers
disagree about leading padding: a WebM Opus stream and the identical Ogg Opus
stream decode a few hundred samples adrift, and the file is still a perfect
copy. Worse, containers interleave streams differently, so even the packet
*sequence* changes across a faithful mp4 → mkv remux. The only honest test is
per-stream packet size and checksum, in order, which is what
`tests/test_promises.py` does. It caught two false failures already.

**`--print` puts yt-dlp into quiet mode, and quiet mode silences progress.**
`--progress` asks for it back. Without it the bar never moves and a download
that is working perfectly looks like a hang. This cost an hour once.

**The partial file must keep the real extension.** ffmpeg picks the output
container from the suffix, so writing to `out.opus.partial` fails with
"Invalid argument" — one of the least helpful sentences it knows.
`engines.ffmpeg.run` writes `.out.partial.opus` for this reason.

**Metadata is written in the conversion pass, not after it.** Tags live in the
container, so a tagging step afterwards rewrites the whole file a second time.
`plan(..., metadata=...)` folds `-metadata` into the same command. The
consequence to remember: a file that needed no conversion but does need tags
becomes a `copy` rather than a `none`.

**Do not write metadata for local files.** `sources.local` fills in a title
from the filename so the queue has something to display. Writing that back
would overwrite a real title with a filename. `jobs._process` passes
`metadata=None` for anything that came off this disk; ffmpeg then carries the
existing tags across by itself, which is what you want.

**The window is a window, not a service.** `app.py` binds the loopback
address only, and every request carries a token minted at startup and handed
to the browser in the URL. Loopback on its own is not a boundary: any page in
any tab can POST to 127.0.0.1, and this server writes files to disk. The token
plus the Origin and Host checks in `_authorised` are what stop a website you
happen to have open from queueing downloads on your machine. Do not add a
`--host` flag.

**`print` block-buffers when stdout is not a terminal.** The address the
window opens at is the one line somebody needs, and a launcher that pipes the
output would show nothing at all until the server stopped. Every startup print
in `app.py` passes `flush=True`.

**A browser will not tell a page where a dropped file lives** — except in
`text/uri-list`, which Finder does populate with a `file://` URL. The `File`
object the browser also hands over has a name and no path, which is useless to
a tool whose job is to read the file from disk. `pathFromDrop` decodes the
URI; there is no fallback that could work, so the failure case says "paste the
path instead" and names the Finder shortcut for copying one.

**Deezer describes a playlist and an album differently.** A playlist's nested
tracks carry `isrc` and `track_position`; an album's carry neither. So an
album is listed from the order it arrives in and `deezer.enrich` fills in the
rest one track at a time, from the resolver, only for tracks somebody actually
fetches. Doing it at expand time would be a request per track at the moment
somebody pastes a link, for data most of those tracks will never need.

**Spotify's embed has two shapes.** An album or playlist puts its tracks in
`entity.trackList`; a single track has no `trackList` at all and carries its
fields at the top level, with the artist in `entity.artists` rather than
`subtitle`. Handle both or track links fail with "nothing in it siphon can
fetch", which is a confusing thing to be told about one track.

**The embed also truncates at fifty and will not say so.** That is the most
dangerous behaviour in this whole file: someone asks for a 200-track playlist,
gets 50, and has no idea. `_from_embed` flags a page-sized result as possibly
truncated and the CLI, the API and the page all surface it. Never remove that
without replacing it with something better.

**Opus cannot carry cover art here.** Ogg stores a picture as a base64 blob in
a comment field and ffmpeg will not write it, so `ARTWORK_CONTAINERS` is mp3,
m4a and flac only. Adding opus to that set would silently drop the artwork
rather than embed it. Relatedly, mp4 has no standard ISRC atom, so an m4a
loses the ISRC that a flac or an mp3 keeps.

**Tidal's request path is unverified, and that must stay written down until
it is not.** Tidal is the only service here with no keyless way in: the v2 API
answers `UNAUTHORIZED` to everything, there is no public embed JSON, and the
share pages carry a title and nothing else. So nothing in `_album`,
`_playlist` or `_bearer` has ever been run against the real service.

What *is* confirmed, from a captured `GET /v2/albums/{id}` response: the base
URL, the `Accept: application/vnd.api+json` header, the `countryCode`
parameter, and the album attribute names — `title`, `releaseDate`,
`imageLinks` with `meta.width`, `tidalUrl`, and durations as ISO 8601 strings.
What is *inferred* is the track attribute spelling (`isrc`, `trackNumber`,
`volumeNumber`) and the cursor pagination parameter. The first thing to do
with a real key is check those against an actual response, then delete this
paragraph.

**Do not call `/v2/trackManifests/{id}`.** It returns a real playback manifest
— the audio itself, DRM-protected. It is the one endpoint that would turn this
module into something siphon has promised not to be. The catalogue is what
this reads; `resolve` finds the audio elsewhere, as it does for every other
catalogue source.

**Tidal durations are ISO 8601 strings.** `PT1H2M11S`, not a number of
seconds, and nothing else in siphon works that way. Passing one to `float()`
raises; passing it through untouched is worse, because every track silently
ends up with no duration and the resolver loses a quarter of its evidence
without anything looking broken. `tidal.seconds` handles it.

**`locale.getlocale()` cannot tell you the country on macOS.** It reports
`('C', 'UTF-8')` in a process started from Finder. The system knows, and keeps
it in `AppleLocale` — where the language and the region are allowed to
disagree: `en_US@rg=gbzzzz` is English as spoken by somebody in Britain.
Reading the language tag alone would put a London user on the American
catalogue, and Tidal's catalogue genuinely differs by territory.
`tidal.region_from_tag` prefers the `rg` override, and it is a pure function
so that the rule is testable.

**Drain both pipes.** `_stream` in both `sources/ytdlp.py` and
`engines/ffmpeg.py` reads stdout in the main thread and stderr in another. A
pipe nobody is reading fills at 64 KB and the child blocks writing to it,
which looks exactly like a stalled download. Close both when done, or a long
session runs out of descriptors.

## The shape

```
siphon.py       the command line
jobs.py         the queue and the pipeline: fetch → convert → place
model.py        Item (a thing that exists) and Job (one trip through)
sources/        URL or path → a list of Items. Nothing is downloaded here.
engines/        programs that turn one format into another
formats.py      presets, and what each container is allowed to hold
library.py      what a finished file is called and where it goes
credentials.py  keys, and the instructions the settings page is generated from
paths.py        the four directories
platform_support.py   finding external programs, and explaining their absence
```

Both registries — `sources.ORDER` and `engines.ORDER` — are ordered, specific
first. yt-dlp goes last and claims everything left. Adding a format siphon
cannot yet handle should be a new module in `engines/` and one line in
`ORDER`, never a change to the queue or the pipeline.

## Where it is up to

- **Phase 1, done.** Queue, pipeline, yt-dlp source, local source, ffmpeg
  engine, credentials store, CLI, tests.
- **Phase 2, done.** The `127.0.0.1` window: live queue over server-sent
  events, playlist preview before committing, settings page generated from
  `credentials.status()`, drag-and-drop.
- **Phase 3, in progress.** Deezer and Spotify done and verified end to end,
  with `resolve.py` and artwork embedding. Tidal is written but **its
  catalogue path has never run** — see below. The confirmation step for an
  uncertain match is still missing; the promise above says so.
- **Phase 4.** Breadth. ImageMagick, pandoc and Ghostscript engines; a
  self-hosted cobalt instance as a second fetch backend.
