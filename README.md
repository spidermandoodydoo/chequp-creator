# chequp-creator

The shorts-factory workflow, rebuilt for **CheqUp's Meta ads**. It turns CheqUp's own account, ad-library and
search data into storyboards, then into finished 9:16 / 4:5 / 1:1 videos rendered **in the CheqUp Digital
Design System**. The heavy lifting runs on **mama** (7× RTX 3090), with the PC's 5090 as an option.

```
data/insights.yaml ──► concepts/*.json ──► cqf plan (Qwen3-235B on mama) ──► concepts/variants/*.json
   (account, proof,        (base storyboards,      hook variants, auto-linted, re-asked on failure)
    search, rulings)        one per concept)
                                   │
                                   ▼
   cqf make:  lint ─► b-roll (Wan 2.2 on mama's ComfyUI farm) ─► voice (Kokoro, British) ─► render ─► QA
             (FAIL stops)  stills → Claude-vision check → i2v clips      design-system motion graphics
                                                                        (Playwright + ffmpeg)
                                   │
                                   ▼
   cqf outbox:  out/outbox/<date>/  mp4s + posters + ads_sheet.csv (copy, CTA, UTM URL, HOLD/READY) + index.html
```

Nothing is pushed to Meta automatically. CheqUp has two upheld ASA rulings and CAP/GPhC correspondence on
file, so every file passes a human with a HOLD/READY column.

## What the data says to make (from `data/insights.yaml`)

| # | Concept | Why (CheqUp's own numbers) | Storyboard |
|---|---|---|---|
| 1 | The Method in 20 seconds | METHOD B video = best ad in the account: **£62.51 CPA, 2.58 ROAS** (campaign paused) | `concepts/method-20.json` |
| 2 | WeightWatchers, included | "ww / weight watchers" ~94k UK searches/mo; a compliant trust hook | `concepts/ww-included.json` |
| 3 | Your Coach in your pocket | SUPPORT B – GOALS £128 CPA; IG Stories £176 | `concepts/coach-pocket.json` |
| 4 | 6-second bumpers ×4 | Reels overlay **£82 CPA**, Audience Network £93 | `concepts/bumper-{a..d}.json` |
| 5 | What happens when you start | How-to-start / eligibility demand; Numan's top hook | `concepts/consult-flow.json` |
| 6 | Six months, properly supported | Price in only 1 of 178 ads; Voy and Numan run time-bound offers | `concepts/six-months.json` |
| 7–10 | Real reviews · What if it lasted · Not just willpower · 65+ cut | see `insights.yaml` | via `cqf plan` |

Also from the data: **women 35–64 drive 83% of purchases**. Native FB Reels convert at £470 CPA against £82 for
Reels overlay, so every 9:16 cut puts its hook on screen from frame 0. Exclude **FB in-stream** (£3,628 CPA).
Pure lifestyle awareness video spent £44k for 2 purchases, so don't make it. Retire the "METHOD B_Oral pill" ad.

## Two looks: what's live, and the design system

`reference/meta-account/` holds CheqUp's own ad videos, pulled read-only from the Meta ad account:
- 23 small copies and their contact sheets
- metrics: LUFS, cuts, colours, timings
- spend joined to each creative
- **`LOOK.md`**, the measured spec

It turns out none of the live video ads follow the design system. They all use one "glass card" template: a deep purple
gradient, frosted cards, a cyan CTA pill and heavy type. Method B (about £37k, about £155 CPA) runs on it. So the renderer
has two themes:

- **`"theme": "meta-live"`** reproduces that template from the measured values. It adds a hook from frame 0, uses
  verbatim reviews only, and drops the medicine-jar hero. See `concepts/live-*.json`.
- **The default** is the design system, described below. See `concepts/method-20.json` and the others.

The brand team should choose. The data favours the template, and the brand guidelines favour the system.

## The design system is the renderer

`render/` builds every frame from `brand/design-system/tokens/*.css`, the files extracted from
*CheqUp Digital Design System.fig*. Nothing is restyled by hand:

- Instrument Sans 400/500/600 (never 700) and DM Mono, self-hosted in `brand/fonts/`.
- Sand, Dark Sand, Yellow Wash, Lavender, Midnight and Purple grounds. Purple is the action colour, and Sunflower is used only on eyebrows on Midnight.
- No gradients, textures or drop shadows. Borders are inset rings.
- Motion uses the two system durations, 160ms and 320ms, on one curve: `cubic-bezier(0.2, 0.8, 0.2, 1)`.
- Frames are laid out at the 390-wide mobile spec ("Mobile is the design") and rendered at 1080 px.
- Components are drawn from the spec: Button, ProgressStep, RadioRow and ProgressBar (the eligibility check), Card, Tag, RatingStars, the relief-rectangle crop and the PageDivider transitions (only the three defined pairs).
- Each scene type maps to the system:
  - `hook`: Display headline
  - `steps`: ProgressStep
  - `checklist`: line icons
  - `ui`: the eligibility check, animated
  - `chat`: Coach messages, always labelled illustrative
  - `stat`: refuses to render without a source
  - `quote`: refuses without a `verbatim_ref`
  - `media`: full bleed, or the relief crop
  - `endcard`: wordmark, CTA and regulatory line
- **The CheqUp Loop is never used.** The design system says it is not for digital advertising.
- Captions are a functional Midnight panel, with the current word in Lavender. They appear only on media scenes that have no other on-screen text.

## Compliance gate (`cqf/compliance.py`, tests in `tests/`)

**FAIL** (the board never renders):
- Names or indirect references to a prescription-only medicine: brand or generic names, GLP-1, jab, pen, injection, pill, tablet, oral, dose.
- Speed, superlative or kg claims ("2 minute consultation", "most effective").
- Personal attributes or body-image language.
- B-roll prompts asking for needles, pens, pills, scales, measuring tapes, body close-ups or clinical staff.
- AI people presented as members, clinicians or reviewers.
- Unsourced stats, or testimonials without a verbatim reference.
- Exclamation marks, emoji, title-case headings, or "Learn more"-style CTAs.

**HOLD** (renders as a draft, and the outbox marks it HOLD):
- Prices: needs legal sign-off.
- WeightWatchers mentions: needs partner approval.
- Stats: needs the source and sample size on file.
- Reviews: needs the verbatim review held with permission.

To clear a HOLD, record the sign-off in the board, e.g. `"approvals": {"price": "J. Smith, Legal, 2026-10-09"}`.

## Running it

```bash
pip install -r requirements.txt          # pyyaml, requests
# voice: Python ≤3.12, then
pip install "kokoro>=0.9.4" "transformers>=4.44" soundfile \
  "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
cd render && npm install && npx playwright install chromium && cd ..

python -m cqf doctor                      # tools, LM Studio on mama, ComfyUI farm, Kokoro
python -m cqf lint concepts/              # compliance only
python -m cqf plan method-20 -n 4         # 4 hook variants from Qwen on mama
python -m cqf make method-20              # full pipeline, all formats
python -m cqf batch --top 5 --variants    # top-5 concepts + their variants
python -m cqf outbox                      # build today's upload folder
```

Useful flags:
- `--no-broll`: uses each scene's `fallback_src` (CheqUp's own photography) instead of GPU clips.
- `--no-voice`: renders silently.
- `--format 9x16`: renders one ratio only.
- `--use-5090`: also queues b-roll on the PC's ComfyUI, which shorts-factory shares.
- `--strict`: refuses HOLD boards.

## mama (7× 3090) — two phases, because both jobs want every card

Qwen3-235B in LM Studio takes all 7 cards (~145 GB). Wan 2.2 needs one card per ComfyUI server. Switch between them on mama:

```bash
bash mama/cq_phase.sh plan      # load Qwen on :1234 → run `cqf plan` / `cqf batch` planning
bash mama/cq_phase.sh render    # unload Qwen, kill orphan llama-servers, check umt5, start ComfyUI on 8189-8194
bash mama/cq_phase.sh status
```

A typical batch:
1. `plan`, then write variants (no Claude limits are used, since Qwen does the writing).
2. `render`, then `cqf batch` (b-roll on 6 cards in parallel).
3. `outbox`.

If the LLM is down, `cqf plan` falls back to `claude -p`. If the farm is down, `cqf make` falls back to the brand photography.

`mama/cq_phase.sh render` also checks the size of the umt5 text encoder. That file arrived truncated on Oct 2 and failed every card at CLIPLoader. Fix it with `bash ~/fetch_models.sh /home/dan/ComfyUI-Installs/ComfyUI/ComfyUI`.

Throughput (shorts-factory figures for the same models):
- **One Wan 2.2 b-roll shot** (2 stills plus a 5 s clip) takes about 2.5 GPU-minutes on the 5090, and roughly 2× that on a 3090.
- **A typical 20 s concept** has 1–2 shots.
- **Six 3090s** should therefore produce about **40–70 b-roll shots an hour**.
- **Motion graphics** render on CPU at about 1 min per 20 s video per format.
