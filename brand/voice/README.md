# Voice reference clips (synthetic; no real person)

Chatterbox (the VO engine, `cqf/voice.py`) copies the delivery of a short reference clip. Every clip here
is **machine-made**, so no real person's voice is cloned and nobody's consent or likeness is involved.

| File | What it is | Made with | Used by |
|---|---|---|---|
| `announcer_ref.wav` | ~8.6 s, Kokoro voice `bf_emma` reading a neutral news paragraph | Kokoro-82M (Apache-2.0) | source for the bouncy clip; the "fluid" and "steady" profiles in `presets/chequp_meta.yaml` |
| `announcer_bouncy_ref.wav` | Chatterbox reading an upbeat promo paragraph, cloned from `announcer_ref.wav` at exaggeration 1.0, cfg_weight 0.35, temperature 0.9; best of seeds 11/22/33 by pitch SD | Chatterbox 0.5B (MIT) | **default** announcer / CheqUp voice (Dan's pick of 8 Oct, "bouncy") |
| `customer_ref.wav` | ~8 s, Kokoro voice `bf_isabella`, conversational | Kokoro-82M (Apache-2.0) | the `customer` role in dialogue ads |

Texts (in `scripts/make_voice_refs.py`) are neutral and say nothing about health, weight or medicine.

## Making them

The clips are generated **on Dan's 5090 PC** with local open models, never on the cloud VM:

```powershell
.venv\Scripts\python.exe scripts\make_voice_refs.py --config config.pc.yaml          # makes only what's missing
.venv\Scripts\python.exe scripts\make_voice_refs.py --config config.pc.yaml --force  # remake all three
```

It refuses to make anything (exit 4) unless the Python running it, and the Chatterbox env for the bouncy clip, see a
CUDA GPU; `--cpu` overrides that. `scripts/setup_pc.ps1` runs it at the end (it skips clips that exist). It prints the pitch SD (semitones) of each clip and of each
bouncy candidate; the candidates stay in `out/voice_refs/` so a different take can be swapped in by ear.
The 8 Oct originals measured 3.81 / 3.42 / 2.57 st for seeds 11 / 22 / 33 (seed 11 kept). Model sampling differs
between machines, so a regenerated set will sound similar but not identical: listen before a production run.

If a clip is missing, `cqf doctor` says MISS and every line that needs it is read by Kokoro instead, with a
warning naming the line (recorded per line in `board.audio.voice_engines` and REPORT.md).

## Licences and provenance

- **Kokoro-82M** (hexgrad, Apache-2.0): weights and output usable commercially.
- **Chatterbox** (Resemble AI, MIT): weights and output usable commercially. Every Chatterbox output carries
  Resemble's imperceptible PerTh watermark, so the ad VO is identifiable as synthetic.
- The VO engine and the references are local and open; nothing is sent to a TTS service.
