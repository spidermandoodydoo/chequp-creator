"""Run: python -m pytest tests  (or python tests/test_compliance.py)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cqf.compliance import fix_typography, lint_board, lint_copy, lint_prompt, verdict  # noqa: E402

BAD_COPY = [
    "Get Mounjaro delivered", "The weight loss jab that works", "Weekly pen, delivered", "Try our new tablets",
    "GLP-1 support from £99", "UK’s most effective weight loss treatment", "2 minute online consultation",
    "Lose 2 stone by summer", "Are you overweight?", "Tired of your belly?", "Start today!", "Feel great 💪",
]
OK_COPY = ["Care about you, not just your weight.", "A Clinician reviews your answers", "Here’s what happens next",
           "Happens in your own time", "Open the app and message your Coach"]


def test_bad_copy_fails():
    for t in BAD_COPY:
        assert any(i.level == "FAIL" for i in lint_copy(t, "x")), t


def test_good_copy_passes():
    for t in OK_COPY:
        assert not [i for i in lint_copy(t, "x") if i.level == "FAIL"], t


def test_prompts():
    assert lint_prompt("a woman holding an injection pen in a kitchen", "x")
    assert lint_prompt("person standing on bathroom scales", "x")
    assert lint_prompt("doctor in a white lab coat", "x")
    assert not lint_prompt("two friends laughing on a sofa with cups of tea", "x")


def test_board_rules():
    b = {"scenes": [{"type": "stat", "dur": 3, "value": "4.4", "label": "x"},
                    {"type": "quote", "dur": 3, "quote": "Amazing results"},
                    {"type": "media", "dur": 3, "person_role": "member", "broll": "friends walking"}]}
    rules = " ".join(i.rule for i in lint_board(b))
    assert "without a source" in rules and "verbatim_ref" in rules and "member" in rules
    assert verdict(lint_board(b)) == "FAIL"


def test_approvals_clear_holds():
    b = {"scenes": [{"type": "hook", "dur": 2, "headline": "Membership from £109 a month.", "vo": "x"}, {"type": "endcard", "dur": 2, "headline": "x", "cta": "Start your check"}],
         "meta": {"url": "https://chequp.com/lp/method"}}
    assert verdict(lint_board(b)) == "HOLD"
    b["approvals"] = {"price": "Legal, 2026-10-08"}
    assert verdict(lint_board(b)) == "PASS"


def test_typography():
    assert fix_typography("you're not") == "you’re not"




def test_unlicensed_and_landing():
    b = {"scenes": [{"type": "media", "dur": 3, "fallback_src": "brand/design-system/assets/img/f02b703f91f681a7.jpg", "caption": "x", "vo": "x"},
                    {"type": "endcard", "dur": 2, "headline": "x", "cta": "Start your check"}], "meta": {"url": "https://chequp.com/how-it-works"}}
    rules = " ".join(i.rule for i in lint_board(b))
    assert "Stocksy" in rules and "landing page" in rules


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
