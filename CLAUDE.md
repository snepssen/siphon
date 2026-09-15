# siphon — working context

Fetch media from a URL, or convert what is already on the disk, through one
pipeline. Python 3 standard library only; yt-dlp and ffmpeg as external
programs; MIT.

Read `README.md` for what it does. This file is the short version: what will
bite you if you don't know it.

---

## The convention this belongs to

Every tool here is self-contained: a start script, and dependencies **offered
to be installed** rather than reported, with the default answer being yes to
all of them. Docker is not an acceptable dependency — a component that
normally ships as a container gets run from source instead.

That is what `bootstrap.py` and `cobalt_service.py` are for, and `start.sh`
asks before the window ever fails to appear. The rule it enforces: siphon
never runs `sudo` on anybody's behalf, so a package manager that needs root
gets its command printed rather than executed.

`media-preflight` only prints install lines. It predates this and has not been
brought up to it.

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

The resolver holds the same line in three bands. Below `resolve.FLOOR` nothing
is downloaded and the near-miss is named. At or above `CONFIDENT` the match is
acted on without comment. In between, the job stops in `model.WAITING` with
the options attached and waits for a person — *before* fetching anything, not
after.

The load-bearing detail is that `propose` mutates nothing and `accept` is the
only thing that ever sets `item.url`. A waiting job therefore has no url, and
an item with no url cannot have fetched a byte — which is what makes "shown
before it downloads" a fact about the code rather than a claim about it.
`tests/test_confirmation.py` asserts exactly that, and it is the assertion to
keep if any of the rest is refactored away.

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

**A `return` inside a `try` still runs the `finally`.** The waiting path
returns early, and the `finally` in `jobs._process` was stamping `finished_at`
on its way out — so a job stopped on a question looked like one that had
completed. The `paused` flag exists for that and nothing else. A test asserts
`finished_at is None` for a waiting job, which is how it was caught.

**Only an offered option may be chosen.** `Queue.choose` looks the url up in
the job's own `choice["options"]` and refuses anything else, so the window's
`/api/choose` cannot be talked into fetching an arbitrary address by a request
that did not come from the page. There is a test for it.

**cobalt will not run on the newest Node.** It depends on `isolated-vm`, a
native module compiled against V8's internals, which lags Node majors by
months — it does not build against Node 26 at all, and ships prebuilts for
Linux only. So `cobalt_service.node_bin()` looks for a keg-only `node@22`
beside the current one and uses it for cobalt alone, without changing what
`node` means anywhere else. The Node that builds must be the Node that runs:
a module compiled by one and loaded by another fails with "No native build was
found", which reads like a missing download rather than a version mismatch.

**cobalt is a pnpm workspace, and npm cannot install it.** Its api depends on
a sibling as `workspace:^`, a protocol npm rejects outright with
EUNSUPPORTEDPROTOCOL. Node ships corepack, which would solve this, but it is
not on the PATH of a current install — so pnpm is simply another thing siphon
offers to fetch. Do not add `--ignore-scripts` to that install: isolated-vm
compiles at install time, and skipping it leaves a node_modules that looks
complete and fails at the first import.

**cobalt's tunnels are not for yt-dlp.** cobalt hands back a plain address it
has already done the extraction for; putting that through yt-dlp asks an
extractor to extract a file, and on a one-shot tunnel it gives up with "Did
not get any data blocks" — which reads like a network fault. `sources.cobalt`
has its own `fetch`, and `sources.fetcher_for` is how the pipeline asks a
source whether it would rather fetch its own bytes.

**cobalt explains itself in the body of a 400.** `net.get_json(...,
accept_errors=True)` is why: without it a perfectly healthy instance
answering "that link is invalid" was reported as "could not reach your cobalt
instance — is it running?".

**cobalt's YouTube is broken upstream, and the token provider does not fix
it.** This was chased a long way, so the findings are here rather than in
somebody's afternoon:

  * A local cobalt returns a tunnel for a YouTube link and streams zero bytes,
    logging nothing. The same tunnel serves SoundCloud 2.8 MB, so the tunnel
    machinery is fine and the fault is YouTube-specific.
  * It needs a poToken, and `pot_provider` now supplies one — cobalt logs
    "poToken & visitor_data loaded successfully!". **The tunnel is still
    empty.** The token was necessary and is not sufficient.
  * `CUSTOM_INNERTUBE_CLIENT=ANDROID_VR`, the fix candidate in
    imputnet/cobalt#1581, does not help here either.
  * It is a known open bug for self-hosters: imputnet/cobalt#1465 ("Youtube 0
    byte file download") and #1475, open since November 2025.

So do not spend another day on it. yt-dlp fetches YouTube perfectly and is
unaffected; cobalt is for the sites where yt-dlp is the one having a bad week.
The provider is still worth having — it works, and it is the same provider
yt-dlp's own plugin uses if YouTube ever does start demanding tokens there.

**cobalt asks for tokens the way bgutil cannot answer.** cobalt's docs name
imputnet's `yt-session-generator`, which serves `/token`; cobalt's code POSTs
to `/get_pot` and its `validateSession` reads `poToken` and `contentBinding` —
bgutil's field names. Reading the code settles what the docs confuse. That
generator was tried first and is also simply stale: it drives a real Chrome,
clicks the embedded player and waits for a POST to `/youtubei/v1/player`
carrying the token, and YouTube's embed no longer makes that request — the
BotGuard call (`jnn-pa.googleapis.com/…/Waa/GenerateIT`) fires but the watched
request never does.

**The last gap between them is a Content-Type.** cobalt POSTs `/get_pot` with
no body and no headers at all; bgutil requires `Content-Type: application/json`
and returns 415 without it, which cobalt reports as "no poToken in session
response" — a sentence that points at the token rather than at the request.
`pot_provider.serve_bridge` is the fifty lines that join them, and
`tests/test_tokens.py` pins both halves, including a transcription of cobalt's
own validator so the shape is checked against what consumes it.

**Two engines claim images, and the order decides.** ImageMagick goes first
and takes them when it is installed; ffmpeg picks up what is left when it is
not. That is deliberate: ffmpeg converts PNG, JPEG, TIFF, BMP and GIF
perfectly well and is always here, so images work out of the box and work for
*more* formats once somebody installs ImageMagick — rather than refusing every
image until they do. `ffmpeg._has_encoder` asks the binary what it can write
rather than assuming: Homebrew's build reads WebP and cannot write a byte of
it, so ffmpeg declines WebP and ImageMagick's absence is what gets reported.

**`-vf` takes a simple chain; a labelled graph needs `-filter_complex`.**
Flattening transparency needs a second input to composite onto, which makes it
a labelled graph, and passing that to `-vf` is rejected as "Invalid argument"
— ffmpeg's least helpful sentence, and the second time this project has been
told it. The image path builds labelled stages and passes `-filter_complex`
with an explicit `-map`.

**Flattening transparency onto black is the silent default.** A transparent
PNG saved as JPEG goes black behind, because `format=yuv420p` alone just drops
the alpha. Logos and screenshots are exactly what people convert, so
`_image_plan` composites onto white via `scale2ref` and there is a test that
reads the corner pixels back.

**An engine may produce more than one file.** A PDF rendered to images is one
per page. `run` may return a list, and `jobs._process` places every one of
them — returning just the first quietly lost the rest when the working
directory was cleaned up, so a forty-page PDF arrived as a single image of
page one. `Job.extra_outputs` carries the others so the count shown is the
count written.

**pandoc cannot read a PDF, and that is not a gap to fill.** It writes them
given a typesetter; there is no route back, because a PDF describes marks on a
page and the structure the document had before it became one is not in the
file. `pandoc.can` returns False for a PDF source so Ghostscript gets it.
Writing a PDF needs a typesetter pandoc does not ship — none is installed
here, so `pdf` output from a document currently refuses with the install line
for tectonic.

**cobalt is off unless configured and only ever claims `cobalt:` links.** It
sits behind yt-dlp, which keeps everything by default. It claims the prefix
even with no instance set, so the refusal can say how to configure one instead
of the registry saying "nothing here knows what to do with cobalt:…".

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

**Tidal is verified end to end** — albums, playlists, single tracks, auth,
pagination and artwork, all run against the real service.

**The embedded relationship is page one, and paging restarts from page one.**
An album or playlist response carries at most twenty items in
`relationships.items`, whatever `numberOfItems` says, and
`/relationships/items` begins again from the start rather than continuing.
Adding one to the other gave a 25-track playlist 45 tracks. `_complete` picks
between them instead: the embedded list when it is already whole, the paged
list otherwise, and the embedded list again if paging comes back empty,
because a partial list beats no list. It cost one repeated request and removed
a whole class of duplicate. The album path hid this for a while because a
14-track album fits in one response.

**What guessing the schema got wrong**, kept here because it is what the
inference-versus-capture distinction actually costs. Four of the field names
this module used before a key existed were invented: there is no `imageLinks`
and no `tidalUrl`, and `trackNumber`/`volumeNumber` are not track attributes
at all. What is real:

  * **A track's position is in the meta of the reference to it.** An album's
    `relationships.items.data` entries look like `{"id": …, "type": "tracks",
    "meta": {"volumeNumber": 1, "trackNumber": 1, "itemCursor": …}}`, and that
    meta is the only place the ordering exists. `_linked` returns
    `(resource, meta)` pairs for exactly this reason; `_related` drops the
    meta and is only for relationships that have none worth keeping. Use the
    wrong one on `items` and every album comes out unnumbered.
  * **Cover art is a relationship, not a field.** `coverArt` → an `artworks`
    resource → `files`, the same image at seven sizes from 80px to 1280px,
    each with `meta.width`. `artwork_url` follows that and takes the largest.
  * **The web address is `externalLinks[].href`**, not `tidalUrl`.
  * **A nested include must name the relationship it wants.** `include=albums`
    on a track gets the album without its artwork; `include=albums.coverArt`
    is what actually produces a cover. This is silent — the track simply comes
    out with no picture. The same mistake left every playlist track coverless
    until the include asked for `items.albums.coverArt,coverArt`.
  * **A playlist's title is `name`, and its items carry no track numbers** —
    the reference meta holds `itemId` and `addedAt` instead, so position comes
    from the order, which is what `_track(..., position=…)` is for.
  * **`title` and `version` are separate.** Tidal stores "Aerodynamic" and
    "Remastered" apart; everywhere else calls that one name, so `_track`
    joins them.

The test fixture in `tests/test_tidal.py` is cut from a real response and
every field name in it is one the service actually sent. Keep it that way: the
earlier fixture agreed with the code rather than the API, which is a test that
proves nothing.

**`net.get_json` must forward `data`.** It did not, and because the Spotify
embed path needs no token, nothing noticed: both credentialled paths were
broken with a `TypeError` that only appeared the first time anybody actually
had keys. Any helper with a happy path that is never exercised is a helper
that is quietly wrong.

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
- **Phase 3, done.** Deezer, Spotify and Tidal, `resolve.py`, artwork
  embedding, and the confirmation step for uncertain matches in both the
  terminal and the window. The one gap is Tidal's playlist path, which needs a
  real playlist link to verify.
- **Tokens.** `pot_provider.py` runs bgutil and a bridge in front of it, so
  cobalt gets real poTokens. It does not make cobalt's YouTube work — see
  below — but it is correct, tested, and the piece anything else would need.
- **Phase 4, done and verified.** ImageMagick, pandoc and Ghostscript engines,
  images and documents in the catalogue, cobalt as a second fetch backend, and
  `bootstrap.py` — which is the convention the whole family follows and the
  thing to copy into the next tool.
- **Phase 4.** Breadth. ImageMagick, pandoc and Ghostscript engines; a
  self-hosted cobalt instance as a second fetch backend.
