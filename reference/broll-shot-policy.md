# Which shots the 5090 makes, and which must be real

**Rule:** AI makes the world around the person; real, released photography supplies the person.

This is now enforced, not just advice:
1. Every AI shot carries a people tag (prompt rules).
2. The tag adds face terms to the negative, and the global negative bans portraits and faces looking at the camera.
3. The vision check rejects **any** recognisable face in an AI plate, whatever the tag.
4. The preset no longer says "faces optional" or lists people-led subjects.
5. Any brief that needs a face is routed to real photography, using the table below.

## Every b-roll brief in `concepts/*.json`, and where it comes from

| Brief (concept file) | AI plate (tag) | Needs real photography for |
|---|---|---|
| Hands preparing a packed lunch (made-simple-*) | yes, `hands` | — |
| Hands chopping asparagus (ww-included) | yes, `hands` | — |
| Woman in her late forties cooking a stir fry, "laughing" variant too (live-method, method-20) | as hands only, `hands` | her face or laugh |
| Woman in her fifties smiling at her phone, park (coach-pocket, live-coach) | as a back view, `back_view` | the smile |
| Two friends with tea on a sofa (live-start) | as hands only, `hands_pair` | faces or laughter |
| Cup of tea by a window | yes, `none` | — |
| Two women preparing a salad (live-ww) | as hands only, `hands_pair` | faces or laughter |
| Woman in her fifties smiling in a garden (made-simple-social, -vod) | as a back view, `back_view` | the smile |
| Two friends cooking together (made-simple-vod) | as hands only, `hands_pair` | faces or laughter |
| Family on garden sofas: woman, husband, grown-up son (route-day-23-vod) | only the laid table with no one at it, `none` | **all** the people |

## AI on the 5090 (Qwen still → SeedVR2 → a still with a push, or image-to-video → SeedVR2 → FILM)
- **Food and ingredients:** worktops, chopping boards, lunchboxes, market produce.
- **Hands doing one simple action, if they pass the vision check.** If the fingers fail, use real hands from CheqUp's own or commissioned photography, or a Storyblocks clip used as-is.
- **Empty UK interiors:** kitchens, hallways, windowsills, living rooms.
- **Objects:** trainers by the door, a mug of tea, a water bottle, a notebook, a shopping bag, a phone that's face-down or has a dark screen.
- **Exteriors:** park paths, terraced streets, gardens, weather.
- **People only as unidentifiable extras:** from behind, at a distance or out of focus. Never the subject of a quote. Always an ordinary adult in their 40s to 60s.

## Age and body (CAP 13.3, A25-1305722, Meta)

**The rules:**
- **CAP 13.3:** weight-reduction marketing "must neither be directed at nor contain anything that is likely to appeal particularly to people who are under 18 or those for whom weight reduction would produce a potentially harmful body weight (BMI of less than 18.5 kg/m2)", and "must not suggest that being underweight is desirable or acceptable" (https://www.asa.org.uk/type/non_broadcast/code_section/13.html).
- **A25-1305722 (17 Dec 2025, rule 1.3):** the ad "suggested having a larger body was undesirable". The ASA said "it was not necessarily the case that a person of the body size and shape shown would be unhealthy", and that the mirror "emphasised physical appearance rather than health" (https://www.asa.org.uk/rulings/chequp-health-ltd--a25-1305722-chequp-health-ltd-.html).
- **Meta:** weight-loss ads "must be targeted to people at least 18 years or older"
