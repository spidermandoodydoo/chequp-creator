
## How prompts are put together (`farm.py`)
- **Still, positive:** `rewritten_prompts.still_prompt` + a space + `look_positive`.
- **Still, negative:** `negative` + ", " + `people_add_on[shot.people]`. Every shot must carry a people tag; a shot without one fails at plan time.
- **Still size:** `models.qwen_image.sizes[aspect]`, either 928x1664 or 1664x928. It's never the Wan size, and never 1080x1920.
- **Clip, positive:** the motion prompt only. Don't add a scene description or the look string; the still already supplies those.
- **Clip, negative:** `negative_wan` + "，" + `negative_i2v_extra`.
- **Clip size:** `models.wan22.clip.sizes[aspect]`, either 720x1280 or 1280x720. SeedVR2 inside the graph takes it to 1080x1920 or 1920x1080.
- **Why the negatives now work:** Qwen runs at cfg 4 and Wan at cfg 3.5. At cfg 1.0, ComfyUI drops the negative entirely.

## People tags (enforced)
The shot policy decides which tag a brief gets.

| Tag | What's allowed in frame | What gets added to the negative |
|---|---|---|
| `none` | no people | person, people, face, hands, arm, portrait |
| `hands` | one person's hands and forearms | face, head, hair, portrait, looking at the camera, second person, extra hands |
| `hands_pair` | two people's hands and forearms | face, head, hair, portrait, looking at the camera, third person, extra hands |
| `back_view` | one adult seen from behind, small in the frame | face, front view, profile, portrait, looking at the camera, close-up |
| `distant` | small out-of-focus figures far away | face, portrait, looking at the camera, close-up, crowd |

The vision check rejects any recognisable face whatever the tag.

## Rules
1. **Never write a negation in the positive.** Write the opposite instead:

   | Instead of | Write |
   |---|---|
   | no grading or duotone | true-to-life colour, clean whites |
   | no blur | sharp focus |
   | no faces | only her forearms and hands are in frame |

   Never name the unwanted thing in the positive; it belongs in the negative. NegT2IBench (arXiv 2610.03084) found that 41.5% of failures render exactly what was forbidden.
2. **Describe what's in the frame, not the mood.** Use 80–150 words, in this order:
   1. subject and action;
   2. each item named, with how many and how it's cut;
   3. who or what is in frame (matching the tag);
   4. the light source and its direction;
   5. the composition sentence;
   6. lens and aperture.
3. **Name the light.**
   - Good: "low sun from a window on the left rakes across…", "crisp window-shaped patches of light", "rim-lights her hair".
   - Never use: soft, hazy, dreamy, moody, calm, dawn, mid-tone, documentary, bokeh, shallow depth of field, out of focus, cinematic, film, film-stock names, 8k, masterpiece, hyperrealistic.
   - `look_positive` says "balanced exposure" for the brand's "mid-tone".
4. **Lens:** "50mm lens at f/5.6". Use f/8 for food and shots where everything should be sharp.
5. **Always include a composition sentence.**
   - **9:16:** "Vertical frame: the upper half is plain {filler}, {anchor} sits just below the centre, {foreground} fills the bottom."
     - The card covers 10.7% to about 43% of the height (LOOK.md).
     - The 4:5 and 1:1 crops keep the band from about 15% to 85%, so the anchor survives.
   - **16:9 (its own still, 1664x928):** "Horizontal frame: {anchor} on the right third, the left half is plain {filler}."
   - Name the filler concretely, for example "sunlit white tiled wall".
6. **Food and hands:**
   - Name every food item, with how many and how it's cut.
   - Say which hand does what, with one simple grip and one action.
   - Hands get short unpainted nails and age cues.
7. **People (only as the tag allows):**
   - State an age from 40s to 60s, plus hair and clothes.
   - When a hand or figure's ethnicity is shown, name it respectfully, and rotate it across shots.
   - Never write: beautiful, attractive, model, perfect, young, slim, skinny, patient, member, consultation, uniform, scrubs, lanyard.
   - Never frame body shape, the waist, the belly or the thighs. Use neutral, everyday clothes.
8. **Make it British with objects:** Victorian or Georgian terraces, sash windows, radiators, an electric kettle, white tiled splashbacks, London plane trees, black iron railings.
   - No landmarks, brands or readable text.
   - Phones are face-down, or have a dark screen.
9. **Never in the positive** (`compliance.lint_prompt` enforces most of these): scale(s), weighing, measuring tape, pens, needles, pills, doctor, nurse, clinician, hospital, logo, before and after, mirror.
10. **Motion prompts (Wan image-to-video):**
    - 15–40 words.
    - One small action that finishes within 5 s, using only things already in the still.
    - Use speed words ("slowly", "very slowly").
    - Hand-action shots end with "Fixed camera."
    - Environment shots end with "The camera pushes in very slowly."
    - Never write "handheld" or "no camera shake".
11. **Seeds:** run 4 per shot and let the vision check pick the best. If a still fails, change the prompt, not the sampler.

All 10 rewritten prompts pass `compliance.lint_prompt`, and each still prompt is 109–150 words.

