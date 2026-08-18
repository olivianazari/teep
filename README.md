# Teep Analysis

Compares a Muay Thai teep in an uploaded video against a fixed reference clip and
produces a strict numeric score plus written feedback.

Everything runs locally. No cloud, no API keys, no network access at runtime.

---

## Run it

```bash
uv run run.py
```

That starts the server and opens a browser at <http://localhost:8000>. Stop with ctrl-c.

`uv` installs the pinned Python (3.11) and every dependency on first run; there is no
environment to manage. If you don't have `uv`:

```bash
python3 -m pip install --user uv
```

That installs to `~/Library/Python/3.x/bin` on macOS, which is **not on `PATH` by
default** — `uv: command not found` after a successful install means exactly this. Either
add it:

```bash
echo 'export PATH="$HOME/Library/Python/3.9/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

or skip `uv` entirely once the environment exists and run the interpreter directly:

```bash
.venv/bin/python3 run.py
```

Both start the same server. `--no-browser` suppresses opening a tab, `--port N` moves it
off 8000.

### Offline

The app never reaches the network at runtime:

- the pose model ships **inside the mediapipe wheel** (`backend/config.py` uses
  `backend: "legacy"`), so there is nothing to download;
- the frontend bundle in `dist/` is self-contained, fonts included.

Only the very first `uv run` needs the network, to install dependencies.

---

## What gets scored

`video_A` (in `assets/`) is the reference. It ships with the app, is always shown on the
left, and is **never scored**. Your upload is `video_B`, shown on the right.

Four metrics, and only these four:

| UI label | CSV column | Weight |
|---|---|---|
| Lead hip flexion | `kick_hip_flexion` | 33% |
| Lead knee angle | `kick_knee_angle` | 33% |
| Body tilt | `trunk_lean_sagittal` | 29% |
| Rear knee angle | `sup_knee_angle` | 5% |

"Body tilt" is the label the athlete sees; the metric **key** is still `torso_tilt`
throughout the scorer, the API and the tests.

The overall score has two halves, combined through the worst-sensitive mean:

- **Shape** — per-frame position against the reference, after time alignment.
- **Range of motion** — whether the movement travelled as far as the reference's.

Both are returned separately as `shape_score` and `rom_score`, so a low overall can always
be attributed to one or the other.

A third number, **`timing_score`**, is computed and returned but **not displayed**. Time
alignment warps tempo away before shape is measured, so tempo has to be scored separately
from the warping path — and §6.4 forbids folding it into the overall, since the coaching
response to "wrong shape" and "wrong tempo" are entirely different. The UI shows one score
by choice; restoring timing is a card in the score row and nothing else.

---

## Regenerating the reference

The tolerance bands are **derived at load time** from the reference CSV's own range, so
regenerating the reference automatically re-tunes the grading. Run this whenever
`assets/video_A.mp4` changes:

```bash
uv run python backend/teep_extract.py --video assets/video_A.mp4 --backend legacy --complexity 1 --cutoff 10.0 --det-conf 0.5 --track-conf 0.5 --leg auto --person any --out assets/
```

Then rewrite the provenance record:

```bash
uv run python -c "import json; from backend import config, reference; reference.write_provenance(config.REFERENCE_PROVENANCE, json.loads(config.REFERENCE_SUMMARY.read_text()))"
```

And rebuild the pose landmarks that drive the skeleton overlay:

```bash
uv run python -m backend.landmarks
```

Skipping that last step leaves `assets/video_A_landmarks.json` with a frame count that no
longer matches the metrics CSV. That is detected rather than ignored: the overlay is
disabled and a warning naming this command appears in the UI. Scoring is unaffected — the
overlay is a display layer and never feeds the metrics.

`assets/reference_provenance.json` records the parameters the reference was built with. On
startup the app compares it against `config.EXTRACTION` and **refuses to score** if they
disagree — a silent parameter mismatch produces plausible-looking wrong scores, which is
the worst failure mode available here.

Always pass every extraction parameter explicitly. Never rely on `teep_extract.py`'s
argparse defaults: its own default backend is `tasks`, not `legacy`, and the two produce
different numbers.

---

## Tests

```bash
uv run pytest
```

The important one is `tests/test_self_comparison.py`: scoring `video_A` against itself must
return **exactly 100.0** overall and **exactly 100.0** timing, with an identity warp path
and empty feedback. Any deviation means a bug in alignment, tolerance handling or
aggregation.

---

## Development

The shipped pack serves a prebuilt bundle from `dist/`. To work on the frontend:

```bash
uv run run.py --no-browser          # backend on :8000
npm --prefix frontend run dev       # Vite dev server, proxies /api to :8000
```

Rebuild the shipped bundle with:

```bash
npm --prefix frontend run build
```

UI primitives come from **shadcn/ui**, copied into `frontend/src/components/ui/` and
editable. Install more with `npx shadcn@latest add <name>` from `frontend/`, or through the
shadcn MCP server configured in `.mcp.json` (restart, then `/mcp` should show it
Connected). Don't write shadcn component props from memory — they change between versions.

Timelines, the warp ribbon, sparklines, apex ticks, the skeleton overlay and the video sync
controller are all custom; no Radix primitive fits them.

### Skeleton overlay

Each video pane has its own **skeleton** toggle in its header, drawing BlazePose's 33-point
pose over that video independently, on a `<canvas>` rather than SVG — it repaints every frame during playback, where reconciling ~40
SVG nodes at 30 fps would not be free.

Colours are functional, like the phase colours, and are the other sanctioned exception to
staying on default shadcn tokens: **cyan** is the kicking leg, **orange** the support leg,
**light grey** the trunk and arms. Joints that are vertices of a scored angle are drawn
larger, so you can see which points the four metrics are actually computed from. Every bone
is stroked twice, a dark halo under the colour, so the skeleton stays readable over both
bright and dark footage.

Landmarks below 0.35 visibility are not drawn at all, and fade in up to 0.75 — a guessed
joint should not look as confident as a tracked one.

These are the same smoothed image-space landmarks the metrics are computed from, so what
you see is genuinely what was measured, not a second estimate.

---

## Layout

```
run.py                  entrypoint: starts the server, opens the browser
backend/
  main.py               FastAPI app, routes, SSE
  teep_extract.py       EXISTING — not modified
  pipeline.py           runs teep_extract's functions over an upload
  reference.py          loads, validates and caches video_A
  align.py              DTW + timing score
  scoring.py            scoring engine
  feedback.py           deterministic templates
  analysis.py           ties alignment, scoring and feedback into one result
  landmarks.py          pose landmarks for the overlay (+ a regeneration CLI)
  config.py             ALL tunable constants
assets/                 video_A.mp4, its metrics CSV, summary, provenance, landmarks
frontend/               React + TS + Tailwind + shadcn source
dist/                   built bundle, served by FastAPI
tests/
```

`pipeline.py` and `analysis.py` are additions to the layout in the spec: they keep
`main.py` to routing and let the API and the self-comparison test share one code path, so
the thing the test certifies is the thing the server serves.

---

## Tuning strictness

`video_A` is the rubric. Its per-frame values are the answer key, and **how tightly it
defines an answer comes from its own stability** — not from a fraction of its range.

```python
FULL_CREDIT_STABILITY = 3.0
ZERO_CREDIT_STABILITY = 10.0
```

```
unit = max(median |frame-to-frame delta|, median |second difference|)
full = FULL_CREDIT_STABILITY * unit
zero = ZERO_CREDIT_STABILITY * unit
```

The first term is one frame of legitimate motion: a deviation smaller than that cannot be
told apart from a one-frame timing wobble. The second is the metric's own jitter — for a
clean signal it sits far below the first, but for a noisy one it does not, so **a metric
MediaPipe tracks badly earns a wider band instead of being handed one by a special case**.
That is how `rear_knee_angle` stays tolerant without one: it barely moves (median step
2.12°) but is noisy (second difference 2.61°), so the jitter term sets its band.

| metric | unit | full | zero |
|---|---|---|---|
| lead_hip_flexion | 4.71° | ±14.1° | ±47.1° |
| lead_knee_angle | 6.19° | ±18.6° | ±61.9° |
| torso_tilt | 2.23° | ±6.7° | ±22.3° |
| rear_knee_angle | 2.61° | ±7.8° | ±26.1° |

This replaced a fraction-of-range dial (`0.13 / 0.44` with 7°/22° floors) that let a metric
sit an eighth of the entire movement off at **every** frame and still score 100.

## Range of motion

Per-frame comparison is blind to amplitude. A teep that travels through half the
reference's trunk rotation, at the right times and in the right order, reads as
slightly-off-everywhere rather than as the different technique it is.

```python
ROM_FULL_CREDIT = 0.12   # within 12% of the reference's amplitude
ROM_ZERO_CREDIT = 0.70
ROM_WEIGHT      = 0.40   # share of the overall score
```

Scored on `|1 - range_B / range_A|`, penalised **both** ways — overshooting the reference
is not better form, it is a different movement.

This is what catches a genuinely bad rep. Measured on one: 19.3° of trunk rotation against
the reference's 46.5°, while every per-frame band still called the rep 79.

## Worst fault dominates

```python
AGGREGATION_POWER = -1.0    # 1.0 arithmetic, 0.0 geometric, -1.0 harmonic
```

Scores combine through a weighted **power mean** rather than an arithmetic one, both across
metrics within a frame and across phases. Below 1.0 the worst reading dominates, which is
how a coach grades — by the worst fault, not by average correctness.

Under the arithmetic mean the worst reading in a real bad rep (`torso_tilt` in `retraction`,
14.5) controlled **5.8%** of the final number, because it was averaged three separate times
on the way up: metrics into a frame, frames into a phase, phases into the overall.

Any power leaves the self-comparison gate intact — a power mean of all-100s is exactly 100.

### Calibration

Measured on two real reps, before and after:

| | competent teep | leg-only teep |
|---|---|---|
| old (mean, no ROM) | 84.5 | 79.0 |
| **now** | **84.1** | **56.3** |

The good rep barely moves; the bad one falls from green to red. That asymmetry is the point
— the changes target what was actually wrong rather than shifting the whole scale, which is
all the old dial could do.

**Calibrated on two clips.** That is thin. If a rep scores in a way you disagree with, the
levers in order of effect are `ROM_WEIGHT`, `AGGREGATION_POWER`, then the two stability
multipliers.

### Timing is a separate dial

`TIMING_DEV_FULL` / `TIMING_DEV_ZERO` (0.20 / 1.00) govern the timing score and are
untouched by the above. A phase within ~±15% of the reference duration scores 100; 2× too
slow or fast scores 0. Still computed and returned; not shown (see *What gets scored*).

### A note on `MIN_PHASE_FRAMES`

A phase with fewer than `MIN_PHASE_FRAMES` frames in your upload is dropped and its weight
redistributed, so a clip that starts mid-kick isn't scored on a `ready` stance that was
never filmed.

**This is set to 4, lowered from the spec's 5.** At 30 fps a crisp chamber genuinely lasts
4 frames — it is 4 in the reference, and §4's own reference facts list
`chamber 4 | retraction 4` — so a floor of 5 dropped both phases and moved 40% of the phase
weight onto the survivors for any rep at reference tempo.

The trade-off: a 4-frame phase is only ~130 ms, so `chamber` and `retraction` scores are
built on few samples and are noisier than the longer phases. Read them as indicative rather
than precise. Raising the floor back to 5 is a one-line change in `backend/config.py`.

Shooting the reference at 60 fps would remove the tension entirely, since every phase would
then carry twice the samples.

---

## Guards

| Condition | Behaviour |
|---|---|
| Kicking leg differs from the reference | **Refused.** No mirroring exists; comparing sign-flipped columns produces a confidently wrong score. |
| No kick detected (hip flexion range < 8°) | Refused. |
| Detection rate below 85% | Scored anyway, with a warning banner. |
| Phase shorter than 4 frames (`MIN_PHASE_FRAMES`) | Dropped, weight redistributed, noted in the UI. |
| Unreadable or unsupported video | Clear error in the upload dialog. |

`detection_rate` and `mean_pelvis_tilt_conf` come back in every result's `diagnostics`, but
the redesign removed the results header that displayed them, so they are currently **not
shown anywhere in the UI**. When a score looks inexplicable those two numbers usually
explain it immediately, so they are worth a home again — read them from `POST /api/analyze`
in the meantime. The low-detection warning banner still fires.
