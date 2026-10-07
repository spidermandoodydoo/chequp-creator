"""Compliance + brand-voice lint for CheqUp ad storyboards.

Two severities:
  FAIL  — breaks UK law/ASA rulings, Meta policy or a hard design-system rule. Never renders.
  HOLD  — needs a named human sign-off (price, WeightWatchers, stats, reviews). Renders as a
          draft, but publish puts it in HOLD until the approval is recorded in the storyboard.

Rules come from presets/chequp_meta.yaml `never:` and the CAP/MHRA/GPhC enforcement notice
on prescription-only weight-loss medicines (Apr 2025, updated Sep 2025).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- prescription-only medicine: names and indirect references --------------------------
POM_TERMS = [
    r"mounjaro", r"wegovy", r"ozempic", r"saxenda", r"zepbound", r"rybelsus", r"orforglipron",
    r"tirzepatide", r"semaglutide", r"liraglutide", r"glp[\s-]?1s?", r"gip\b",
    r"jabs?", r"fat[\s-]?jab", r"skinny[\s-]?jab", r"injections?", r"injectables?", r"injecting",
    r"pens?\b", r"needles?", r"syringes?", r"vials?", r"pills?", r"tablets?", r"oral\b",
    r"weight[\s-]?loss (?:drugs?|medicines?|medications?|treatments?)", r"prescription[\s-]only",
    r"appetite suppressants?", r"dose|dosage|titrat\w*",
]
# Speed / efficacy / superlative claims (A24-1264775) and kg-lost results.
CLAIM_TERMS = [
    r"most effective", r"(?:uk’s|uk's|the) best", r"guarantee\w*", r"miracle", r"clinically proven", r"fastest",
    r"\d+\s*[- ]?min(?:ute)?s?\b[^.]{0,20}(?:consultation|consult|check|assessment)", r"lose \d", r"\d+\s*(?:kg|kilos?|lbs?|pounds|stone|st)\b",
    r"drop \d", r"melt", r"burn fat", r"quick fix", r"effortless", r"without (?:diet|exercise)",
]
# Personal attributes / negative self-perception (Meta policy, A25-1305722).
SELF_TERMS = [
    r"\bare you (?:overweight|obese|fat|big|heavy)", r"\byour (?:belly|tummy|gut|fat|rolls|thighs|muffin top|weight problem)",
    r"\bobese\b", r"\bobesity\b", r"\bfat\b", r"biggest person", r"ashamed", r"embarrass\w*", r"hate (?:your|my) body",
    r"tired of (?:being|your|looking)", r"\bugly\b", r"before (?:and|&) after", r"\bbikini body\b",
]
# B-roll prompts: imagery that implies the medicine or body focus.
IMAGE_TERMS = POM_TERMS + [
    r"\bscales?\b", r"weighing", r"measuring tape", r"tape measure", r"before and after", r"shirtless", r"bare (?:belly|stomach)",
    r"close[\s-]?up of (?:a |the )?(?:belly|stomach|waist|thighs|body)", r"hospital", r"medicine (?:box|pack)", r"pharmacy shelf",
    r"\bdoctor\b", r"\bnurse\b", r"\bclinician\b", r"\bpharmacist\b", r"lab coat", r"stethoscope",
    r"logo", r"text overlay", r"celebrity",
]
BANNED_ICONS = {"Syringe", "Pill", "IconNameSyringe", "IconNamePill", "Scale", "IconNameScale", "Weight", "IconNameWeight"}
WEAK_CTAS = {"learn more", "submit", "apply", "click here", "find out more", "read more", "sign up now"}
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")
PLACEHOLDER = re.compile(r"PLACEHOLDER|TODO|TBC|ADD SAMPLE|XX+", re.I)
SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with", "your", "you", "it", "is"}

GROUNDS = {"gradient", "sand", "dark-sand", "yellow-wash", "lavender", "midnight", "purple", "none"}
DIVIDERS = {("sand", "midnight"), ("midnight", "lavender"), ("purple", "sand")}   # the three the system defines


@dataclass
class Issue:
    level: str      # FAIL | HOLD | WARN
    where: str
    rule: str
    text: str = ""

    def __str__(self) -> str:
        return f"{self.level:4} {self.where}: {self.rule}" + (f"  «{self.text[:80]}»" if self.text else "")


def _hits(patterns: list[str], text: str) -> list[str]:
    t = text.lower()
    return [m.group(0) for p in patterns for m in [re.search(r"(?<![a-z])(?:" + p + r")", t)] if m]


def lint_copy(text: str, where: str, *, heading: bool = False, cta: bool = False) -> list[Issue]:
    """Lint one piece of viewer-facing copy (on-screen text, VO line, ad primary text)."""
    out: list[Issue] = []
    if not text:
        return out
    for h in _hits(POM_TERMS, text):
        out.append(Issue("FAIL", where, f"names/implies a prescription-only medicine ('{h}') — CAP 12.12 / enforcement notice", text))
    for h in _hits(CLAIM_TERMS, text):
        out.append(Issue("FAIL", where, f"efficacy/speed/superlative claim ('{h}')", text))
    for h in _hits(SELF_TERMS, text):
        out.append(Issue("FAIL", where, f"personal attribute / negative self-perception ('{h}') — Meta policy", text))
    if "!" in text:
        out.append(Issue("FAIL", where, "exclamation mark — brand voice: never", text))
    if EMOJI.search(text):
        out.append(Issue("FAIL", where, "emoji — brand voice: never", text))
    if PLACEHOLDER.search(text):
        out.append(Issue("HOLD", where, "placeholder text still present", text))
    if "'" in text:
        out.append(Issue("WARN", where, "straight apostrophe — use ’ (auto-fixed by fix_typography)", text))
    if heading:
        words = re.findall(r"[A-Za-z’']+", text)
        caps = [w for w in words[1:] if w[0].isupper() and w.lower() not in SMALL_WORDS
                and w not in {"CheqUp", "WeightWatchers", "Clinician", "Coach", "Health", "Method", "Trustpilot", "UK", "I", "WW"}]
        if len(words) > 2 and len(caps) >= max(2, len(words) // 2):
            out.append(Issue("FAIL", where, "title case heading — sentence case everywhere", text))
    if cta and text.strip().lower() in WEAK_CTAS:
        out.append(Issue("FAIL", where, "CTA states the mechanism — buttons state the outcome", text))
    if re.search(r"£\s?\d", text):
        out.append(Issue("HOLD", where, "price — legal sign-off (membership, not medicine)", text))
    if re.search(r"weight\s?watchers|\bWW\b", text, re.I):
        out.append(Issue("HOLD", where, "WeightWatchers brand — partner approval", text))
    return out


def lint_prompt(text: str, where: str) -> list[Issue]:
    """Lint a b-roll generation prompt (the image model must never be asked for these)."""
    return [Issue("FAIL", where, f"b-roll prompt asks for banned imagery ('{h}')", text) for h in _hits(IMAGE_TERMS, text)]


def lint_board(board: dict) -> list[Issue]:
    """Lint a whole storyboard: copy, prompts, design-system rules, approvals."""
    issues: list[Issue] = []
    approved = set(board.get("approvals", {}).keys())
    scenes = board.get("scenes", [])
    if not scenes:
        return [Issue("FAIL", "board", "no scenes")]
    if scenes[0].get("type") not in {"hook", "media", "stat", "ui", "logo", "glass"} or not (scenes[0].get("headline") or scenes[0].get("caption") or scenes[0].get("value") or scenes[0].get("title")):
        issues.append(Issue("FAIL", "scene 0", "first scene must carry the hook as on-screen text from frame 0"))
    if scenes[-1].get("type") != "endcard":
        issues.append(Issue("WARN", f"scene {len(scenes) - 1}", "last scene is not an endcard"))
    total = sum(float(s.get("dur", 0)) for s in scenes)
    if total > 60:
        issues.append(Issue("WARN", "board", f"{total:.0f}s is long for paid social — Method winner was ~20s"))

    for i, s in enumerate(scenes):
        w = f"scene {i} ({s.get('type')})"
        g = s.get("ground")
        if g and g not in GROUNDS:
            issues.append(Issue("FAIL", w, f"ground '{g}' is not a design-system surface"))
        if s.get("exit_to") and (g or "sand", s["exit_to"]) not in DIVIDERS:
            issues.append(Issue("WARN", w, f"divider {g}→{s['exit_to']} is not one of the three defined transitions"))
        for k in ("headline", "title"):
            issues += lint_copy(s.get(k, ""), f"{w}.{k}", heading=True)
        for k in ("eyebrow", "body", "caption", "label", "quote", "question", "tag", "legal", "vo", "by", "url"):
            issues += lint_copy(s.get(k, ""), f"{w}.{k}")
        for j, m in enumerate(s.get("messages", []) or []):
            issues += lint_copy(m.get("text", ""), f"{w}.messages[{j}]")
        if s.get("type") == "chat" and "illustrative" not in (s.get("note") or "Illustrative conversation").lower():
            issues.append(Issue("FAIL", w, "chat must be labelled illustrative"))
        for k in ("options", "steps", "items"):
            for j, it in enumerate(s.get(k, []) or []):
                txt = it if isinstance(it, str) else " ".join(str(v) for kk, v in it.items() if kk != "icon")
                issues += lint_copy(txt, f"{w}.{k}[{j}]")
                if isinstance(it, dict) and it.get("icon") in BANNED_ICONS:
                    issues.append(Issue("FAIL", f"{w}.{k}[{j}]", f"icon '{it['icon']}' implies medicine/body measurement"))
        if s.get("cta"):
            issues += lint_copy(s["cta"], f"{w}.cta", cta=True)
        if s.get("broll"):
            issues += lint_prompt(s["broll"], f"{w}.broll")
        if s.get("type") == "stat":
            if not s.get("source"):
                issues.append(Issue("FAIL", w, "stat without a source — 'a proof module without a source is a claim'"))
            else:
                issues += lint_copy(s["source"], f"{w}.source")
            issues.append(Issue("HOLD", w, "stat — source and sample size on file"))
        if s.get("price"):
            issues += lint_copy(" ".join(str(v) for v in s["price"].values()), f"{w}.price")
        if s.get("type") in ("quote", "glass") and s.get("quote") is not None:
            if not s.get("verbatim_ref"):
                issues.append(Issue("FAIL", w, "testimonial without verbatim_ref — reviews must be real, verbatim and on file (CAP 3.45)"))
            issues.append(Issue("HOLD", w, "review — verbatim on file with permission"))
        if s.get("loop") or "chequp-loop" in str(s.get("src", "")):
            issues.append(Issue("FAIL", w, "the CheqUp Loop is not for digital advertising"))
        if s.get("type") == "media" and s.get("person_role") in {"member", "clinician", "reviewer", "coach"}:
            issues.append(Issue("FAIL", w, "AI-generated person presented as a member/clinician/reviewer"))

    meta = board.get("meta", {})
    for k in ("primary_text", "headline", "description"):
        issues += lint_copy(meta.get(k, ""), f"meta.{k}", heading=(k == "headline"))
    if meta.get("cta_button") and meta["cta_button"].upper() not in {"LEARN_MORE", "SIGN_UP", "GET_STARTED", "APPLY_NOW", "GET_OFFER", "SEE_MORE", "CHECK_ELIGIBILITY", "BOOK_NOW", "DOWNLOAD"}:
        issues.append(Issue("WARN", "meta.cta_button", f"unknown Meta CTA type {meta['cta_button']}"))

    # Approvals recorded in the board clear the matching HOLDs.
    keys = {"price": "price", "WeightWatchers": "weightwatchers", "stat": "stat", "review": "review"}
    kept = []
    for it in issues:
        if it.level == "HOLD":
            need = next((v for k, v in keys.items() if k.lower() in it.rule.lower()), None)
            if need and need in approved:
                continue
        kept.append(it)
    return kept


def fix_typography(text: str) -> str:
    """Curly apostrophes and quotes, as the design system uses."""
    text = re.sub(r"(\w)'(\w)", "\\1\u2019\\2", text)
    text = re.sub(r"'(\w)", "\u2018\\1", text)
    text = text.replace("'", "\u2019")
    return text


def fix_board(board: dict) -> dict:
    def walk(v):
        if isinstance(v, str):
            return fix_typography(v)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: (walk(x) if k not in {"src", "broll", "id", "type", "ground", "exit_to", "icon", "verbatim_ref", "format", "mode"} else x) for k, x in v.items()}
        return v
    return walk(board)


def verdict(issues: list[Issue]) -> str:
    if any(i.level == "FAIL" for i in issues):
        return "FAIL"
    if any(i.level == "HOLD" for i in issues):
        return "HOLD"
    return "PASS"
