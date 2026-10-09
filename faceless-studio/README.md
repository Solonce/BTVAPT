# Faceless Studio

Self-driving faceless video generator with a browser UI. It comes up with topics,
writes the script with Claude, narrates it, gathers footage that follows what the
narrator is saying, edits it (motion, cuts, captions, music) and drops finished
9:16 and 16:9 MP4s plus a thumbnail and upload text into a folder.

## How it works

```
topic pool ──► script (Claude) ──► narration + word timings ──► footage per beat ──► render ──► output/
   ▲  refilled by Claude + web trend research        │                    │
   └──────────── autopilot (N videos/day) ◄──────────┘     router picks a source per beat
```

* **Formats are data.** Each file in `formats/` is a recipe (structure, tone, art
  style). Add one in the UI's Formats tab when a new trend shows up. Shipped:
  timeline story, what-if, day-in-the-life, ranked movie list, narrate-my-footage.
* **The narration drives the edit.** The script is a list of beats, each with
  narration and a visual plan. Voice is generated first with word-level timing,
  each beat gets exactly the screen time its words take, long beats are split
  into several shots, and captions are timed per word.
* **Footage sources are plugins** (`studio/visuals/`), picked per beat by a
  router that respects the script, your Visual mix weights and the budget, and
  falls back automatically (paid → free → a generated card) so a render never
  dies on one bad asset:

  | Source | Cost | Needs |
  |---|---|---|
  | AI images (pollinations.ai) | free | nothing |
  | AI images (fal.ai Flux) | ~$0.004/image | `fal_key` |
  | AI video clips (fal.ai, image-to-video) | ~$0.35/clip | `fal_key`, `video_provider = fal` |
  | Stock video (Pexels) | free | `pexels_api_key` |
  | Your library (uploaded clips/images, tag-matched) | free | uploads |
  | Your media server (vidsrc-style embeds) | free | `mediaserver_*` settings |
  | Your uploaded video ("Narrate my footage") | free | upload |

* **Presets** layer overrides on top of base settings (`autopilot`, `free`,
  `premium`, `quick_short`, plus your own). Every video can also override
  length, orientation, approval and writer direction.
* **Budget guard:** monthly cap and per-video cap. Paid sources are skipped
  (free fallback used) once a cap would be exceeded. Spend is logged per job.

## Install on the Tailscale box (Ubuntu/Debian)

```bash
sudo apt install -y ffmpeg fonts-dejavu fontconfig
curl -LsSf https://astral.sh/uv/install.sh | sh        # Python package manager
cd faceless-studio
uv venv && uv pip install -e .
.venv/bin/studio --host "$(tailscale ip -4 | head -1)" --port 8765
```

Open `http://<box-name>:8765` from any device on your tailnet, go to
**Settings → API keys**, paste your Anthropic API key, save, then hit
**Surprise me** on the Create tab. To keep it running, use
`deploy/faceless-studio.service`.

> The Anthropic **API** key comes from console.anthropic.com and is billed
> separately from a Claude.ai subscription.

## Media server

Settings → Media server: set the base URL (e.g. `http://vidsrc.xyz`, which your
DNS points at your local storage API) and the embed path. Placeholders:
`{tmdb}`, `{imdb}`, `{title}`, `{year}`. `{tmdb}` needs a free TMDB key so
movie titles from the script can be turned into ids. The resolver accepts a
direct video response, `<video>/<source>` tags, `file:`/`src:` values in page
scripts, or one or two levels of nested `<iframe>`. It cuts clips with ffmpeg
from the 8–85% range of the movie and skips near-black frames. Turn it on and
give the "Ranked list (movies)" format a weight to use it.

## Rough cost per 2-minute video

| Setup | Per video |
|---|---|
| Free preset (Sonnet script, free voice + images) | ~$0.04–0.10 |
| Default (Opus script, free voice + images) | ~$0.10–0.25 |
| Premium (ElevenLabs, Flux, 4 AI video clips) | ~$1.50–2.50 + ElevenLabs plan |

Topic refills with web research add about $0.05–0.15 per batch of 12. These
are estimates; check each provider's current pricing.

## Files

```
formats/            video format recipes (TOML)
studio/pipeline.py  job steps: script → voice → footage → render → output
studio/visuals/     footage source plugins + router inputs
studio/render.py    ffmpeg: motion, cuts, captions, music ducking, thumbnails
studio/topics.py    topic pool refills (Claude + web search + optional YouTube API)
studio/web.py       API;  studio/static/ the UI
data/               database, job working files, uploads, library, music
output/             finished videos (one folder per video)
```

Tests: `uv pip install pytest && .venv/bin/python -m pytest -q`
(offline; Claude, voice and images are stubbed).
