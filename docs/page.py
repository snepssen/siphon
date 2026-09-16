"""siphon's project page, as a catalogue.

Everything particular to this project lives here; `build.py` supplies the
chrome every page in the family shares. Adding a section is a dict in
`sections` — the jump navigation, which is derived from these, picks it up on
its own.

Prose is authored HTML: a paragraph may carry a link, an `<em>` or a
`<span class="mono">`. It is written by whoever owns this repository and is
not escaped.
"""

FONTS = "fonts.css"

PAGE = {
    "meta": {
        "slug": "siphon",
        "name": "siphon",
        "title": "siphon",
        "badge": "macOS · Windows · Linux · MIT",
        "fonts": FONTS,
        "description": (
            "Fetch media from a URL, or convert what you already have. "
            "Local-first, no accounts, and it never re-encodes what it does "
            "not have to."),
        "og_description": (
            "Get media in, get it out in the shape you want — from a link or "
            "from your own disk, through one pipeline, on your own machine."),
        "subhead": (
            "Paste a link and get the file. Point it at a folder you already "
            "have and get the same thing in a different shape. It is one "
            "pipeline either way — a download is just a conversion that had to "
            "fetch its input first — and it does not re-encode anything it "
            "does not have to."),
        "stats": [
            "<b>1800</b> sites it can fetch from",
            "<b>5</b> conversion engines, none bundled",
            "<b>0</b> accounts, uploads or telemetry",
        ],
    },

    # Shown between the header and the first section.
    "header_blocks": [
        {
            "kind": "figure",
            "light": "screens/queue-light.png",
            "dark": "screens/queue-dark.png",
            "width": 1920, "height": 1440, "loading": "eager",
            "alt": ("siphon's window: a link field, a format chosen from Audio, "
                    "Video, Images or Documents, the settings that apply to it, "
                    "and a queue where a fourteen-track album is one row "
                    "reading 4 of 14."),
            "caption": ("The window siphon opens on 127.0.0.1. A playlist is one "
                        "row in the queue, not forty — how many of how many, "
                        "what is running, and one button to stop the lot."),
        },
    ],

    "sections": [
        {
            "id": "problem",
            "jump": "The problem",
            "eyebrow": "The problem",
            "heading": "The hosted ones stopped working",
            "blocks": [
                {"kind": "prose", "class": "lede", "text": [
                    'The public <a href="https://github.com/imputnet/cobalt">cobalt</a> '
                    "instance can no longer fetch from YouTube. Not because of "
                    "anything wrong with cobalt — because YouTube blocks the "
                    "addresses a popular shared service runs from. A copy running "
                    "on your own connection, making a handful of requests, does "
                    "not have that problem.",
                ]},
                {"kind": "prose", "text": [
                    "siphon is that, and the other half nobody ships with it. A "
                    "downloader that hands you a <span class=\"mono\">.webm</span> "
                    "when you wanted an mp3, and leaves you to find something else "
                    "to convert it, has done half a job. Here a file already on "
                    "your disk enters the same pipeline a URL does, simply skipping "
                    "the stage that fetches bytes.",
                ]},
                {"kind": "report", "text": (
                    'siphon get https://…                  <span class="muted">best quality, nothing re-encoded</span>\n'
                    'siphon get https://… --as mp3         <span class="muted">fetch and make an mp3</span>\n'
                    'siphon get https://…/playlist --as m4a   <span class="muted">a playlist, tagged and foldered</span>\n'
                    'siphon convert album/ --as flac       <span class="muted">a folder already on this disk</span>\n'
                    'siphon convert photos/ --as web-image <span class="muted">so is a folder of photographs</span>\n'
                    'siphon list https://…/playlist        <span class="muted">show what is in it, fetch nothing</span>'
                 ), "caption": (
                    "Or run <span class=\"mono\">siphon</span> on its own and it "
                    "opens a window on 127.0.0.1 — the same queue, seen from the "
                    "other side.")},
            ],
        },
        {
            "id": "remux",
            "jump": "Why it is fast",
            "eyebrow": "The part that matters",
            "heading": "It does not re-encode what it does not have to",
            "blocks": [
                {"kind": "prose", "text": [
                    "Asking for an mp4 is usually not a conversion. The video is "
                    "already H.264 and the audio already AAC; both are legal inside "
                    "an mp4; the only work needed is moving the streams into a "
                    "different wrapper. That takes about a second and changes not "
                    "one sample. Re-encoding anyway would cost minutes and quality "
                    "in exchange for nothing at all.",
                    "So siphon compares what is in the file against what you asked "
                    "for, stream by stream, re-encodes exactly the streams that "
                    "need it — and tells you which ones those were.",
                ]},
                {"kind": "report", "text": (
                    "$ siphon get 'https://www.youtube.com/watch?v=aqz-KE-bpKQ' --as mp4-720 -v\n"
                    '<span class="pass">✓</span> Big Buck Bunny 60fps 4K.mp4  (153.7 MB)\n'
                    "    fetched aqz-KE-bpKQ.mp4\n"
                    "    Repackaged from mp4 to mp4 with the streams copied — nothing re-encoded.\n"
                    "    audio copied: already aac\n"
                    "    video copied: already h264"
                 ), "caption": (
                    "Sixteen seconds for 153 MB, and the packets in the output are "
                    "byte-for-byte the packets that came down the wire. There is a "
                    "test that proves it — by comparing per-stream packet "
                    "checksums, because decoded audio is <em>not</em> proof: "
                    "containers disagree about padding and interleaving, so a "
                    "perfect copy can decode a few hundred samples adrift.")},
                {"kind": "prose", "text": [
                    "The same reasoning runs backwards into the fetch. Ask for an "
                    "mp4 and siphon asks yt-dlp for the H.264 and AAC renditions "
                    "specifically, so the conversion afterwards finds it has "
                    "nothing to do. Ask for an <span class=\"mono\">.opus</span> "
                    "from YouTube and the result is a pure copy of what YouTube "
                    "already serves — 0.09 seconds, against 7.65 to re-encode the "
                    "same audio to mp3.",
                    "You can see the decision before committing to it: "
                    "<span class=\"mono\">siphon plan recording.wav --as flac</span>.",
                ]},
            ],
        },
        {
            "id": "music",
            "jump": "Music",
            "eyebrow": "Music",
            "heading": "A playlist is a list of names, not a list of files",
            "blocks": [
                {"kind": "prose", "text": [
                    "Paste a Deezer, Spotify or Tidal album, playlist or track. "
                    "siphon reads the track list — titles, artists, running times, "
                    "cover art, ISRCs — then finds each track something that will "
                    "actually serve it, fetches that, tags it, embeds the cover and "
                    "files it under the album.",
                    "Deezer needs no key at all. Spotify works without one too, by "
                    "reading the same embed its web player uses — but that path "
                    "cannot see ISRCs and stops at fifty tracks of a long playlist, "
                    "so siphon says when a list looks truncated rather than quietly "
                    "handing you a fraction of it.",
                ]},
                {"kind": "heading", "text": "How a track is matched, and when it refuses"},
                {"kind": "prose", "text": [
                    "This is where a music downloader usually goes quietly wrong. "
                    "The obvious approach is to search the title and take whatever "
                    "is closest in length. That is wrong often enough to matter:",
                ]},
                {"kind": "report", "text": (
                    '<span class="muted">wanted: Daft Punk — Harder, Better, Faster, Stronger (226s)</span>\n\n'
                    '<span class="pass">100%</span>  225s  Daft Punk            Daft Punk - Harder, Better, Faster, Stronger\n'
                    '      <span class="muted">title matches, channel is the artist, length exact</span>\n'
                    ' <span class="warn">73%</span>  225s  Fr. Eckle Studios    Daft Hands - Harder, Better, Faster, Stronger\n'
                    '      <span class="muted">title matches, length exact</span>\n'
                    ' <span class="bad">49%</span>  207s  Daft Punk            Daft Punk - Around the World / Harder Better…\n'
                    '      <span class="muted">length 19s out, looks like a live version</span>'
                 ), "caption": (
                    "“Daft Hands” is a different recording that happens to be "
                    "<em>closer</em> in length than the real track. A resolver that "
                    "decides on running time picks it. So running time is worth a "
                    "quarter of the score and never more; the rest is the title, "
                    "and whether the artist is the one who uploaded it.")},
                {"kind": "prose", "text": [
                    "Above 75% siphon acts without comment. Below 50% it downloads "
                    "nothing and names the closest miss. In between it stops and "
                    "asks — <em>before</em> fetching, with the candidates and the "
                    "reasons laid out, in the terminal or as a card in the window. "
                    "A weak match is shown before it downloads, not apologised for "
                    "after.",
                ]},
                {
                    "kind": "figure",
                    "light": "screens/choosing-light.png",
                    "dark": "screens/choosing-dark.png",
                    "width": 1920, "height": 1760,
                    "alt": ("A track siphon is not sure about, stopped in the "
                            "queue. Candidates are listed with their scores, "
                            "uploaders, lengths and the reasons for each score, "
                            "each with a Use this button, and a note that nothing "
                            "has been downloaded."),
                    "caption": ("Erik Satie's Gnossienne No.1 at 69% — close enough "
                                "to offer, not close enough to assume. Nothing has "
                                "been fetched at this point, and the job waits as "
                                "long as it takes, including across a restart."),
                },
            ],
        },
        {
            "id": "formats",
            "jump": "Formats",
            "eyebrow": "Formats",
            "heading": "Four kinds of thing, one pipeline",
            "blocks": [
                {"kind": "cards", "cards": [
                    {"title": "Audio", "formats": "mp3 · m4a · opus · flac · wav",
                     "note": "Plus <span class=\"mono\">audio</span>, meaning "
                             "whatever it already was, untouched."},
                    {"title": "Video", "formats": "mp4 · mp4-1080 · mp4-720 · mkv · webm",
                     "note": "Plus <span class=\"mono\">video</span> — best "
                             "available, nothing re-encoded."},
                    {"title": "Images", "formats": "jpg · png · webp · tiff · gif",
                     "note": "<span class=\"mono\">web-image</span> caps the longest "
                             "side at 2000px. Transparency is flattened onto white, "
                             "not the black you would otherwise get."},
                    {"title": "Documents",
                     "formats": "pdf · docx · xlsx · pptx · epub · odt · rtf · html · md · txt · csv",
                     "note": "Office files to PDF keep their layout; documents to "
                             "markup keep their meaning. Different engines, on "
                             "purpose."},
                ]},
                {"kind": "prose", "text": [
                    "A preset is where a setting starts, not where it is stuck. "
                    "Pick a format and the window offers what can be adjusted on "
                    "it — a bitrate for a lossy audio format, a resolution for "
                    "video, quality and a size cap for images, a rendering "
                    "resolution for PDF pages — and the command line takes the same "
                    "choices. Only the settings that mean something are offered: "
                    "there is no bitrate on FLAC and no resolution on an audio "
                    "file, because a control that changes nothing is worse than no "
                    "control.",
                ]},
                {"kind": "prose", "class": "note", "text": [
                    "A PDF rendered to images gives you one file per page, not a "
                    "picture of page one.",
                ]},
            ],
        },
        {
            "id": "get",
            "jump": "Get it",
            "eyebrow": "Getting it",
            "heading": "It offers to install what it needs",
            "blocks": [
                {"kind": "prose", "text": [
                    "siphon ships no binaries. That is deliberate: yt-dlp releases "
                    "most weeks because the sites it reads keep changing, and a copy "
                    "frozen inside a download would be broken by the time you opened "
                    "it. This way <span class=\"mono\">brew upgrade yt-dlp</span> "
                    "fixes siphon without siphon being touched.",
                    "So the first run asks, and the default answer is yes to all of "
                    "it.",
                ]},
                {"kind": "report", "text": (
                    "siphon needs a few programs it does not ship.\n\n"
                    '  ffmpeg     needed  <span class="muted">— converting audio and video, and muxing what yt-dlp fetches</span>\n'
                    '  magick     extra   <span class="muted">— still image formats ffmpeg handles badly or not at all</span>\n\n'
                    "  brew install ffmpeg\n"
                    "  brew install imagemagick\n\n"
                    "Install all 2 of these with Homebrew? [Y/n]"
                 ), "caption": (
                    "On a system whose package manager needs root, siphon prints the "
                    "command instead of running it. It will not invoke "
                    "<span class=\"mono\">sudo</span> on your behalf.")},
                {"kind": "prose", "text": [
                    '<a href="https://github.com/snepssen/siphon">The source is on '
                    "GitHub</a>, MIT, Python 3.10 or newer. Clone it and run "
                    "<span class=\"mono\">./start.sh</span>, or build a "
                    "<span class=\"mono\">.app</span> and a single-file "
                    "<span class=\"mono\">.pyz</span> with "
                    "<span class=\"mono\">./build.sh</span>.",
                ]},
            ],
        },
        {
            "id": "limits",
            "jump": "What it will not do",
            "eyebrow": "Honesty",
            "heading": "What it will not do",
            "blocks": [
                {"kind": "prose", "text": [
                    "siphon fetches things that are served openly. It reads "
                    "playlists from streaming services as metadata — what the tracks "
                    "are — and then fetches those tracks from somewhere that serves "
                    "them. <b>It does not decrypt protected streams, and it will not "
                    "be made to.</b> If a service hands out its audio under DRM, "
                    "siphon's answer is that it cannot help, and it says so in those "
                    "words rather than failing strangely.",
                    "It is not an editor and not a library manager. It does not "
                    "upload anything anywhere, and nothing leaves your machine "
                    "except the request that fetches the media. There are no "
                    "accounts, no telemetry and no cloud anything.",
                    "Two limits worth knowing before you meet them. <b>There is no "
                    "way back out of a PDF</b> — a PDF describes marks on a page, "
                    "and the structure the document had before it became one is not "
                    "in the file; siphon makes PDFs smaller and renders their pages, "
                    "and does not pretend to un-print them. And <b>converting a "
                    "document is not transcoding</b>: tracked changes and exact "
                    "layout do not survive a change of format, so every plan says "
                    "what it will lose before it runs.",
                ]},
            ],
        },
    ],

    "footer": [
        '<a href="https://github.com/snepssen/siphon">github.com/snepssen/siphon</a> '
        '· MIT · part of the <a href="https://snepssen.github.io/tools-core/">'
        'snepssen workshop</a>',
        "You are responsible for what you download and what you do with it.",
    ],
}
