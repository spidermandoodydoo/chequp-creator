# What CheqUp's Meta videos actually look like

This was measured from the ad account's own files: 383 video uploads, 112 distinct creatives, and 23 files Meta would let us
download (`videos/`, `sheets/`, `metrics.csv`). Spend comes from `performance.csv` (180 days, 67 video ads, £142k),
joined by ad name. It was pulled read-only, and nothing was written to Meta.

## The headline: two looks, and the ads don't use the design system

The CheqUp Digital Design System calls for Sand grounds, flat colour, no gradients, no blur, no shadows, Instrument
Sans at a maximum weight of 600, and no Loop graphic in advertising. **None of the live video ads follow it.** Every
conversion video in the account is built from one of two templates, A and B below. So `render/` now has two themes:

| Theme | Board key | Use it for |
|---|---|---|
| `meta-live` | `"theme": "meta-live"` | Matching what is live and converting today (Method A/B, Goals, Product, Lifestyle) |
| design system (default) | no `theme` | On-brand work that follows the Figma system, if the brand team wants ads moved onto it |

Ask the brand team which look should win. The data says the template look converts. The design system is the
brand's stated direction.

## Template A: "glass card", the conversion workhorse

**Who uses it:** Method A/B, Support/Goals, Product V1/#2 and Lifestyle A/B.

**The spend behind it:**

| Ads | Spend | Purchases | CPA |
|---|---|---|---|
| PILLAR 1 METHOD B (all weeks) | about £37k | 241 | about £155 |
| BOF METHOD A JUL_WK2 | £3.2k | 28 | £116 |
| SUPPORT B – GOALS | £6.0k | 33 | £183 |
| SUPPORT B – GOALS (2nd ad) | £1.5k | 12 | £129 |

**Shape:** 10.0 s (or 12.0 s for the newer `PL1-A WK4`). 720×1280 as delivered, 30 fps, AAC music bed mixed to
**−14.1 LUFS**, no voiceover. A 4:5 master and a 1:1 master exist for each creative.

| t (10 s cut) | t (12 s cut) | Beat | What's on screen |
|---|---|---|---|
| 0.00–0.43 | 0.00–0.70 | Fade in | White wordmark centred at about 40 % height on deep purple, or over the hero plate at about 30 % |
| 0.43–1.30 | 0.70–1.00 | Hold | Wordmark plus a one-line legal at about 62 % |
| 1.27–1.75 | 1.00–2.07 | Dissolve | Hero plate (3D product on pedestal, glass box in a landscape, or lifestyle photo) with the glass card |
| 1.75–4.15 | 2.07–5.10 | Card | Frosted card: small wordmark, 2-line heavy headline, 2–3 line body, cyan pill CTA. Optional price roundel "From £109 /month" |
| 4.15–4.60 | 5.10–5.70 | Dissolve | Card turns into a quote card |
| 4.60–7.20 | 5.70–8.70 | Quote | Frosted quote card: a large “ glyph, a 2–3 line heavy quote, and "– CheqUp Member" |
| 7.20–8.20 | 8.70–9.60 | Dissolve / Loop swoosh | Into the end card. The 12 s cut sweeps the Loop ribbon across |
| 8.20–10.0 | 9.60–12.0 | End card | Wordmark, cyan CTA, chequp.com and legal, on the deep gradient |

**Measured values** (fractions of the frame, so they hold for 1080×1920):

**End card**
- **Background:** solid `#1F0645` from 0 to 35 % of the height, then a linear ramp to `#4F29B8` at the bottom. The Method B variant runs `#1D003F` to `#41168E` with a darker vignette at the bottom.
- **Element positions (vertical):**
  - wordmark top at 33.2 %, height 6.6 %
  - CTA pill at 47.9 %, height 4.2 %, width 47 % of the frame
  - URL at 57.4 %
  - legal at 64.2 %
- **CTA pill:** `#80EBFF` (cyan, which is not in the design system), with midnight `#210847` label text.

**Glass card**
- **Size and position:** x from 9.4 % to 85 % of the width, top at 10.7 %, height about 32 %.
- **Fill:** white at about 17 %, with about a 14 px backdrop blur and a 1 px white ring at 28 %.
- **Radius:** about 1.6 % of the width.
- **Placement:** it always sits on purple. Product plates are purple-lit, and photo plates get a top purple wash (`rgba(98,52,236,.9)` fading to 0 by about 60 %).

**Lifestyle plates**
- The photo stays natural down to about 45 % of the height.
- Below that, a purple overlay ramps up to about 75 % opacity (bottom colour about `#7D3DA6`).

**Type**
- A heavy rounded geometric sans at about weight 700. It is most likely **Kind Sans**, the legacy campaign font the design-system README says to retire. The README says to set campaign work in Instrument Sans instead.
- Approximate sizes as a fraction of the width:
  - headline about 6.6 %
  - body about 4 %
  - quote about 3.8 %
  - legal about 1.9 %

**Motion:** 430 ms linear dissolves between every beat. Nothing moves inside a beat except a slow push on the plate.
It's effectively a sequence of animated statics, which is why only 16 % of plays reach 25 % on the Method B oral cut.

**Copy pattern:** a benefit headline ("All embracing weight loss", "Goals. Not deadlines.", "Clear, plain-speaking
weight health"), then a member quote, then "Check eligibility" or "See if it's right for you".

## Template B: the creator talking head

**Examples:** KIRSTEN, "Chequp-2", "Autumn", "Big changes…", "PriceAd".

**Spend:** KIRSTEN SEP_WK2 spent £7.4k for 25 purchases (£297 CPA). It had the account's highest CTR, 1.43 %, but converts expensively.

**Shape:** a selfie-framed real person in a home setting, 9–19 s. Each video has burned captions in one of two styles:
- **Style 1:** a white box with heavy purple text (`#5B1FA6`-ish), in 2–4 word chunks at about 30 % from the bottom.
- **Style 2:** plain white text centred low.

Overlays include a Trustpilot 4.4 badge and an end card in solid purple `#6A2EF0`-ish with the wordmark, "A GPhC-registered pharmacy" and Trustpilot.

**LTN variant (Jul WK3, 25 s):**
1. The hook "Step off the dieting roller coaster." under the wordmark on a photo with a purple wash.
2. The creator inside a phone frame on the purple gradient.
3. A Loop swoosh into the end card.

**Compliance on these:** "Chequp-2" and "PriceAd" say *"you can get weight loss medication from £109"* and *"takes
less than 3 minutes"*. Under the CAP/MHRA enforcement notice and upheld ruling A24-1264775, those are the exact claims at risk.
**Recommend pausing or re-cutting both.** cqf's lint fails both lines.

## Other formats in the library

- **Trustpilot review scroll** ("Chequp review v3"): a tangerine `#F26B4A`-ish ground, "Our weight loss programme has 13,000+ reviews", and review cards with names. **The file is silent (−70 LUFS).**
- **The 12 numbered 1×1 explainers** ("Do I need a health coach", "NHS said no", "Medication without support"…), plus the Hook 1–3 series, vox pops, "Fine print man" and timelapse sets. Meta exposes no download for these; export them from Ads Manager if they're needed.
- **TOF awareness "Lifestyle A":** about £56k across ads for 2–3 purchases. Don't make more of this.

## What the replica does (`theme: meta-live`)

- **Scene types:**
  - `logo`: wordmark fade, optionally with a hook line, in the LTN pattern
  - `glass`: plate plus frosted card, dissolving to a quote card, with an optional price roundel
  - `endcard`: the measured gradient, cyan pill, URL and legal
- **Transitions:** `"transition": {"type": "fade", "dur": 0.43}` gives the same dissolves.
- **Built-in deviations from the live template:**
  1. **A hook from frame 0.** The live template spends 0–1.3 s on a bare logo, which is probably why native Reels convert at £470 against £82 for Reels overlay. Our `logo` scene carries the headline.
  2. **Real, verbatim reviews only** (`verbatim_ref`). Several live quotes ("I use their app alongside the treatment") reference treatment and can't be checked as real.
  3. **No product jar or pack as hero.** Live legal lines cite "Orlistat 60mg". Supply clean 3D product renders from the agency if legal approves product shots. The plates are lifestyle b-roll from mama for now.
  4. **No Loop swoosh** unless the brand team overrides the design-system rule.
- **Font:** put `KindSans-*.woff2` into `brand/fonts/` to match the live type exactly. Until then it falls back to Instrument Sans 600.
