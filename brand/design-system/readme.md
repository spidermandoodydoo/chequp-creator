# CheqUp Digital Design System

CheqUp Health is a UK online clinic. A member answers a short eligibility check, a prescriber reviews it, and — if treatment is right for them — a weekly pen arrives alongside fortnightly Health Coaching and periodic clinical review. The system covers two surfaces: the public marketing site (chequp.com) and the logged-in member area.

The palette carries five treatment categories. **Weight Health** is the default and the only one with product copy in the source; Women's Health, Cardiovascular and three unnamed slots exist as theme modes.

## Source

Everything here was extracted from read-only Figma attachments. The system comes from **"CheqUp Digital Design System.fig"**, most recently the revision delivered as *"CheqUp Digital Design System-2.fig"*; the brand artwork (the wordmark) came from **"CheqUp World (Client).fig"**.

The current revision has 15 pages — Cover, Getting started, Colour, Typography & icons, Layout & shape, Motion, Actions & navigation, Forms, Content & containers, Feedback & status, Patterns & handoff, plus Landing Page and Website Design and a "-before" copy of each — 287 local components across 34 component sets plus 29 standalone symbols, and 250 Figma Variables across 9 collections. No codebase, repo or deck was supplied. If you have the live Figma URL, keep it beside this file — this project does not carry one.

**What the latest revision changed.** Four new families (`Icon small`, `SegmentedControl`, `RatingStars`, `FooterMobile`), three new icon glyphs (circle-pound-sterling, package-check, scan-heart), and a Surface axis on Text field giving it an on-purple treatment. Token values and the type ramp are unchanged. The two Landing Page boards are social campaign pages still set in **Kind Sans**, a legacy font that is not part of this system — Instrument Sans is correct everywhere.

Values in this project are transcribed from those files, not from any published library. Where a file says 13px, 27px or `#F6EFE4`, that is what is written here.

**Which file governs what.** The component inventory, tokens and type ramp come *only* from "CheqUp Digital Design System.fig" — that file's 34 component sets plus 29 standalone symbols are the whole component list. "CheqUp World (Client).fig" is used for brand artwork and as a reference for the live site; its own layer names (`Journey line`, `Media components`, `Nav buttons active left`, and so on) are page-level compositions from the website build, not design-system families, and are deliberately **not** implemented as components here. An automated inventory check run against that second file will therefore report a large mismatch in both directions — that is expected, not drift.

The Landing Page and Website Design boards in the system file are likewise **page compositions, not families**: they assemble the components above into finished screens. They inform `ui_kits/`, not `components/`.

## The four rules (verbatim from the file)

1. **Mobile is the design.** Every component is specified at 390px first. Desktop is the expansion, not the source.
2. **Decoration off the photography.** Functional UI over an image is fine: a play button, a caption, a control. Avoid decorative or illustrative graphics floating on photography. A watch-out, not a ban.
3. **AA in both directions.** Every pairing is tested light-on-dark and dark-on-light. Three category pairs need their AA-safe tone for body text.
4. **Weight is never Bold.** Instrument Sans Regular 400, Medium 500 and SemiBold 600 only. 700 is out of the system.

---

## Content fundamentals

**Second person, present tense, no hedging.** The reader is "you"; CheqUp is "we". "Two minutes, four questions, no card details." "A prescriber will review it within one working day."

**Sentence case everywhere except tags and eyebrows.** Headings are sentence case ("Not sure whether you're eligible?"). Only Tag, the field label, and the eyebrow are uppercase, and they carry 0.08em tracking to survive it. Never title-case a heading.

**Say what happens next, and to whom.** Error and confirmation copy names the consequence and the party: "Your next delivery will be cancelled and your coach will be told. You can restart at any time." Nothing is left to be inferred.

**Reassure before you explain the fault.** The 404 reads "That page has moved" then "Nothing's wrong with your account." Clinical products earn trust by ruling out the worst reading first.

**Numbers carry their source.** From a stat card in the file: "Source and sample size go here. A proof module without a source is a claim." Never ship a figure without one.

**Contractions yes, exclamation marks no, emoji never.** "What's normal, what isn't, and when to message your coach." The source file contains no emoji anywhere, and no sentence ends in an exclamation mark.

**Clinical language gets a plain-English gloss, and an honest limit.** The BMI tooltip: "Body Mass Index estimates whether your weight is in a healthy range for your height. It is a starting point, not a diagnosis." The last clause is the pattern — say what the number is *not*.

**Buttons state the outcome, not the mechanism.** "Start your check", "Check my eligibility", "Show 24 articles", "Choose 3 months". Not "Submit", not "Apply", not "Learn more".

**Regulatory copy is present, small and unapologetic.** Pharmacy registration, superintendent pharmacist and prescription-only wording sit in the footer at 13px on `--color-on-primary-62`. They are never hidden behind a link.

**Curly apostrophes.** The file uses `’`, not `'`.

---

## Visual foundations

**Colour.** Sand `#FFF6EB` is the page, not white. White (`Surface Tertiary`) is what lifts *off* the page — cards, fields, tables. Dark Sand `#F2E6D9` is the warm band used for headers and table heads. Purple `#683CF5` is the single action colour; Lavender `#D7BEFF` is the secondary fill and the only filled button allowed on Primary Dark. Midnight `#210847` is text, and the dark section ground. Sunflower `#FFEB57` appears twice only: the announcement bar, and eyebrow text on Primary Dark. Text on light is Midnight at 100% for headings, 72% for body, 64% for muted labels, 62% for regulatory copy — the alphas are tokens, not judgement calls. At most two background colours per composition.

**Type.** Instrument Sans for everything, DM Mono for a small amount of numeric detail. Display and headings are SemiBold 600 with −0.02em tracking and 100–110% leading; body is Regular 400 at 140%. The ramp is genuinely responsive: Display 1 is 44px on mobile, 60 on tablet, 80 on desktop and wide. Design mobile-first, or the scale will surprise you.

**Spacing.** Fixed steps 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 80 · 120 · 160. Gutter, page margin, section rhythm and control height are the four values that move with the breakpoint: page margin 24 → 48 → 64, section 64 → 80 → 120, control height 48 on mobile dropping to 40 on desktop. Cards use 32px padding; article cards use 16px because the image goes to the edge.

**Colour is four tiers, and the tier you build on matters.** The file's 250 variables resolve down a chain, and the whole chain is in `tokens/fig-tokens.css`:

1. **Primitives** (34) — raw hex, named for the colour: `--brand-purple`, `--brand-sand`, `--brand-lavender`, `--brand-sunflower`, the five category hues.
2. **Color** (109) — the semantic layer: `--color-primary-base`, `--color-text-muted`, `--color-surface-tertiary`, `--color-border-control`, the ink and on-primary alpha ramps, the system intents.
3. **Theme** (4) — `--theme-accent`, `-hover`, `-active`, `-light`. These point at primary by default and are *reassigned per treatment category* under `data-mode`.
4. **Component** (40) — one token per component slot: 25 for Button (`--button-primary-background` → `--theme-accent`, `--button-secondary-background` → `--color-primary-light`, the ghost label and background states, the Highlight set, disabled fills) and 15 for the field, split across both surfaces (`--field-border`, `--field-border-focus`, `--field-on-purple-placeholder`, and so on).

Build on the component tier where one exists and the semantic tier otherwise; touch a primitive only when defining a semantic token. The payoff is the theme tier — because `--button-primary-background` resolves through `--theme-accent`, setting `data-mode="women-s-health"` on any ancestor recolours every primary button in that subtree with no component change. See the "Component tokens · Button", "Component tokens · Field" and "Token tiers" cards.

**Corner radii.** One rounded family: 4, 8, 12 (chip), 16, 20 (panel), 24 (card), 48 (inset panel), 80, and 999 (pill). Buttons, chips, tabs, pagination and progress tracks are all 999. Fields are 16. Cards are 24. Alerts, cookie notice and table cards are 20. Inset panels are 48. Bottom sheet and sticky CTA round only their top corners.

**Borders and elevation.** There are no drop shadows. Every border in this system is an *inset ring*: `inset 0 0 0 1px` at ink-08 for cards, ink-10 for tables, ink-12 for quiet rows, ink-24 for the Quaternary button, ink-48 for fields. A 2px purple ring marks the recommended plan and the current progress step. The only outer glow in the file is focus: `0 0 0 4px` purple-28. Modals and drawers have no shadow at all — a Primary-dark-60 scrim does the separating.

**Shape.** The relief rectangle is the brand's one piece of geometry: a rectangle whose top edge bows outward, bottom corners at 48px. It crops hero and section photography and it recurs as the curved page divider (an ellipse of the next section's colour rising into the current one) and as the arc that opens the footer. Three divider transitions exist: Sand to Dark, Dark to Lavender, Purple to Sand. The shape is marketing furniture — the member-area guidance in the file says not to force it into a dashboard.

**Backgrounds and imagery.** Flat colour bands, no gradients and no textures. Photography is warm daylight, mid-tone, real people, unstyled — no colour grading, no duotones, no grain. Full-bleed photography appears in the testimonial section on the `#F6EFE4` Yellow Wash base; elsewhere images are cropped at 1:1, 4:3, 3:4 or 16:9 with a 16px radius. Rule 02 governs anything laid over a photo: functional UI only.

**Motion.** Two durations and one curve, and that is the whole system: `--duration-quick` 160ms for hover, focus and colour changes; `--duration-settle` 320ms for reveals, sheets and progress; `--ease-relief` `cubic-bezier(0.2, 0.8, 0.2, 1)` for both. No bounce, no spring, no parallax.

**Hover and press.** Fills darken to a named token (`primary/hover` then `primary/active`), never to an opacity change. Ghost and text-only controls gain a low-alpha wash (purple-14 hover, purple-20 active). Ringed buttons deepen their ring rather than filling. Cards deepen ink-08 to ink-16 and lift 2px. Nothing scales down on press. Links underline on hover and focus only, at a 3px offset.

**Disabled.** Filled controls drop to `surface/disabled-fill`; ringed controls drop their ring to ink-12; all disabled text is ink-32. Never opacity on the whole component.

**Transparency and blur.** Alpha is used constantly for text and rules and never for whole components. There is no backdrop blur anywhere in the file.

**Layout.** 4 / 8 / 12 / 12 columns at 390 / 768 / 1024 / 1440. The header and mobile bar are rounded blocks *inside* the page margin, not full-bleed strips — only colour bands and the testimonial section run edge to edge. Two fixed elements exist: the sticky mobile CTA and the cookie notice.

---

## Iconography

57 icons, 24×24, line style, `currentColor`, drawn on the Lucide grid at 2px nominal stroke. The file ships them twice: `Icon` at full stroke weight, and `Icon small` at half — the same 57 glyphs on the same 24 grid, for use below 24px where the full-weight outline fills in. They cover clinical (heart-pulse, stethoscope, syringe, pill, flask-conical, microscope, thermometer, brain, shield-check, briefcase-medical, clipboard-list, badge-check), measurement (weight, scale, gauge, trending-down, chart-line), activity and lifestyle (dumbbell, person-standing, footprints, bed, moon, salad, apple, carrot, droplet, leaf, sprout), service (message-circle, user-check, calendar-check, notebook-pen) and UI (check, arrows, chevrons, x, menu, search, plus, minus, circle-check, info, triangle-alert, mail, lock, eye, user, clock, upload, calendar, play).

They live in `components/icon/icon-data.js` (and `components/icon-small/icon-data.js` for the light-stroke set) as SVG path data extracted from the file — not a CDN link, and not redrawn. Render them with `<Icon name="IconNameHeartPulse" size={20} />` or `<IconSmall name="IconSmallNameHeartPulse" size={16} />` and recolour with the CSS `color` property. `components/icon/Icon.d.ts` is the complete name index; read it before using a name.

There is no icon font. No emoji appear anywhere in the source and none should be added. Unicode is used only as punctuation: `·` between metadata, `—` in prose, `…` in truncated pagination, `£` for prices, `×` in multipliers, `?` as the tooltip trigger glyph.

**Brand marks.** `assets/logo-wordmark.svg` is the full CheqUp lockup at its native 100×29, and `assets/logo-mark.svg` is the "C" on its own — both extracted verbatim from the Figma sources. `Logo` inlines the same paths so `currentColor` drives the ink/sand tones. `assets/star.svg` is the rating star. Photography: nine JPEGs in `assets/img/`, copied bit-for-bit from the file.

**The CheqUp Loop.** `assets/chequp-loop.svg` — the single illustrative asset in the system, and the one exception to "no illustrations". It is taken directly from the loop on the logo mark: one continuous line with an upward lift, carrying the same idea as the shape system, relief. Native 1461.6×806.4. It appears in the website hero behind the panel.

The path carries `fill="currentColor"`, but that only pays off if the SVG is **inlined or masked**. Loaded through an image element it is an isolated document and `currentColor` resolves against the SVG's own black, so setting `color` on the element does nothing and the loop paints black. Either inline the markup, or mask it:

```css
.loop{
  aspect-ratio: 1461.6 / 806.4;
  background: var(--color-on-primary-base);   /* sand on midnight; ink on light */
  mask: url(assets/chequp-loop.svg) center / contain no-repeat;
}
```

It is **called the CheqUp Loop**, and the source is emphatic that this is its only name — not a journey line, not a swoosh, not a squiggle. A journey line implies a path from one point to another, and the asset deliberately does not do that: it loops and lifts.

Background only, brand level only:

| May be used | Must not |
| --- | --- |
| The website, as a background graphic behind a panel | Digital advertising |
| Branded collateral | Social posts |
| Event signage | Campaign work of any kind |
| End frames on brand video, where it stays connected to the logo | Anywhere it would sit in front of content, or carry meaning on its own |

Beyond the Loop there are no illustrations in the source file — the visual language is photography plus flat colour, and nothing else should be drawn to fill that gap.

---

## Caveats

- **The accreditation marks are still type.** The website file carries CQC (96×32) and LegitScript (106×32) at 32% opacity, but only fragments of each mark's paths survived extraction, so `LogoStrip` sets the names rather than shipping a partly-drawn regulator logo. Supply the two official SVGs and swap them in.
- **Fonts come from Google Fonts, and that is correct.** Instrument Sans is a Google font — [fonts.google.com/specimen/Instrument+Sans](https://fonts.google.com/specimen/Instrument+Sans) — confirmed by the design owner as the real source. DM Mono likewise. `tokens/fonts.css` imports both; no substitution is involved and no licensed binaries are needed. Self-host by downloading the families into `assets/fonts/` and swapping the `@import` for local `@font-face` rules.
- **Kind Sans is legacy — ignore it.** The two Landing Page boards (social campaign pages, 1200 and mobile) are still set in Kind Sans. That is leftover from an earlier brand and is **not** part of this system: Instrument Sans is the correct font everywhere, with DM Mono for code. Confirmed by the design owner. If those campaign pages are rebuilt, set them in Instrument Sans.
- **The "-before" pages are not implemented.** `Website-Design-before` and `Landing-Page-before` are prior revisions kept for comparison; `ui_kits/` follows the current boards.
- **The member area is guidance, not screens.** See `ui_kits/member-area/README.md`.
- **Text styles.** The file defines its type ramp entirely as Figma Variables (25 Typography variables across four modes) and carries no named TEXT styles, so `fig-typography.css` generated empty and the ramp is authored as the 14 `.cq-*` role classes in `tokens/typography.css` instead.
- **Theme modes are attribute-scoped.** Figma's mode names collide across collections (both Theme and Spacing have modes), so `data-mode` on `<html>` or a section selects a *category theme*; breakpoints resolve automatically through media queries. Setting `data-mode="desktop"` pins the desktop scale and disables the category themes on that element.

---

## Index

| Path | What's in it |
| --- | --- |
| `styles.css` | The single entry point. `@import`s only. |
| `tokens/fig-tokens.css` | All 269 Figma Variables, all modes. Generated from the .fig. |
| `tokens/fonts.css` | Font families and the `@font-face` source. |
| `tokens/scale.css` | Breakpoint resolution, px-derived lengths, rings, motion. |
| `tokens/typography.css` | The 14 type roles as `.cq-*` classes. |
| `tokens/base.css` | Page defaults, link colours, focus ring, surface helpers. |
| `guidelines/*.card.html` | 25 foundation specimen cards (Colors, Type, Spacing, Brand). |
| `components/<group>/` | The component library — see below. |
| `templates/marketing-page/` | Copyable public-page template (Design Component). |
| `templates/member-dashboard/` | Copyable logged-in dashboard template (Design Component). |
| `ui_kits/marketing/` | chequp.com: home, pricing, eligibility check. |
| `ui_kits/member-area/` | Logged-in dashboard, per the file's guidance. |
| `assets/` | `logo-wordmark.svg`, `logo-mark.svg`, `chequp-loop.svg`, `star.svg`, `footer-shield-outline.svg`, `footer-shield-tick.svg`, social glyphs, `img/` photography. |
| `vue/` | Vue 3 port of the component library — 64 SFCs, same names. See `vue/README.md`. |
| `tokens/tailwind.preset.cjs` | Tailwind v3 preset generated from the Figma variables. |
| `SKILL.md` | Agent Skills front matter for use outside this project. |

### Components

All 63 families the source design-system file defines, and nothing more.

**Every component name below is intentional and confirmed.** They are transcribed from
"CheqUp Digital Design System.fig", which is the file that defines this system's component
inventory. They are *not* named after layers in "CheqUp World (Client).fig" — that second
file is a website build, and its page-level layer names (`Journey line`, `Media components`,
`Image cluster`, `Nav buttons active left`, `Measure line`, `Cursors`, `Frame 46`, and the
rest) are compositions assembled *from* this system, not families of it. Renaming these
components to match those layers would break every consumer. If an inventory check is run
against the website file it will flag all 63 names; that result should be disregarded.

**Actions & navigation** — `Button`, `TextLink`, `NavItem`, `Tab`, `FilterChip`, `Breadcrumbs`, `Pagination`, `CarouselControls`, `ProgressStep`, `ProgressBar`, `Spinner`

**Forms** — `TextField`, `SegmentedControl`, `SearchField`, `CheckboxRow`, `RadioRow`, `Toggle`, `NumberStepper`, `UploadFileRow`, `MultiStepPanel`

**Content & containers** — `Card`, `PlanCard`, `CategoryCard`, `Panel`, `AccordionRow`, `ImageContainer`, `ReliefRectangle`, `VideoPanel`, `Table`, `TableCard`, `Testimonials`, `PageDivider`, `GridDiagram`, `BreakpointTable`

**Feedback & status** — `Alert`, `Toast`, `Tag`, `Avatar`, `AvatarInitials`, `AvatarOverflow`, `Rating`, `RatingStars`, `Skeleton`, `EmptyState`, `ErrorPage`, `TooltipTrigger`, `TooltipBubble`, `Modal`, `BottomSheet`, `Drawer`, `AnnouncementBar`, `CookieNotice`, `StickyMobileCTA`

**Navigation & brand** — `Logo`, `HeaderBar`, `MobileBar`, `MobileMenu`, `NavDropdown`, `Footer`, `FooterMobile`, `LogoStrip`

**Icons** — `Icon` and `IconSmall` (57 names each)

### Intentional additions

One export is an intentional addition: **`TabBar`**, the tinted Primary-dark-04 track the source
draws around Tab instances, so the 4px gap and padding stay consistent.

Two things the source names but does not ship as their own family are folded into the family they belong to
rather than invented as new components:

- The select on the "Text fields & select" board is `<TextField as="select" options={…} />` — same 48px height, 16px radius and ink-48 ring, plus a chevron.
- The row container around Progress step instances is a plain `display:flex; gap:8px` wrapper, not a component.

The shared alert/toast icon switch is exported as lowercase `alertGlyph` so it stays off the public namespace.
