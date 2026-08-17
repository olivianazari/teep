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
| Torso tilt | `trunk_lean_sagittal` | 29% |
| Rear knee angle | `sup_knee_angle` | 5% |

Two numbers come back, and they are deliberately **never merged**:

- **Overall score** — shape, measured after time alignment.
- **Timing score** — tempo, measured from the warping path itself.

Alignment warps time away, so a slow, sloppy teep would otherwise warp neatly onto a fast,
crisp one and score well. The coaching response to "wrong shape" and "wrong tempo" are
entirely different, so they stay apart.

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

The **Skeleton** toggle in the header draws BlazePose's 33-point pose over both videos, on a
`<canvas>` rather than SVG — it repaints every frame during playback, where reconciling ~40
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

Four numbers in `backend/config.py`, and nothing else:

```python
FULL_CREDIT_FRAC  = 0.15   # of the reference's active-window range
ZERO_CREDIT_FRAC  = 0.50
FULL_CREDIT_FLOOR = 8.0    # degrees
ZERO_CREDIT_FLOOR = 25.0
```

Loosening these compresses the top of the scale. Watch the **spread** between the
best- and worst-scoring phase on a rep with a known fault: once that falls below
about 10 points the tool has stopped discriminating, and past roughly
`0.30 / 0.90 / 15 / 45` the feedback panel empties out entirely and every rep
comes back clean.

**These are loosened from the spec's `0.08 / 0.25 / 4.0 / 12.0`**, which graded too harshly
on real footage — a competent teep with one clear postural fault scored 65.

The floors were doing most of the damage. Tolerances are `max(frac × range, floor)`, and
two of the four metrics have ranges small enough that the floor always won:

| metric | range | at spec values | bound by |
|---|---|---|---|
| lead_hip_flexion | 87.0° | ±7.0 / ±21.8 | proportional |
| lead_knee_angle | 97.2° | ±7.8 / ±24.3 | proportional |
| torso_tilt | 46.5° | ±4.0 / ±12.0 | **floor** |
| rear_knee_angle | 33.5° | ±4.0 / ±12.0 | **floor** |

So torso tolerated ±12° while the knee tolerated ±24.3°, despite torso carrying 29% of the
weight — putting 44% of its frames on a hard zero. Raising the floors alongside the
fractions keeps the four metrics comparable to one another rather than just shifting every
score upward.

Raise all four to grade more softly, lower them to grade harder. The self-comparison gate
returns exactly 100.0 at any setting, since a zero deviation always scores full credit — so
it does not constrain your choice here.

Expect the feedback panel to be populated on most uploads. That is what strict grading
means. If you ever find it empty on reps that clearly have faults, the dial has gone too
soft.

### Timing is a separate dial

`TIMING_DEV_FULL` / `TIMING_DEV_ZERO` (0.20 / 1.00) govern the timing score and are
untouched by the above. A phase within ~±15% of the reference duration scores 100; 2× too
slow or fast scores 0.

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
| Phase shorter than 5 frames | Dropped, weight redistributed, noted in the UI. |
| Unreadable or unsupported video | Clear error in the upload dialog. |

`detection_rate` and `mean_pelvis_tilt_conf` are shown in the header. When a score looks
inexplicable, those two numbers usually explain it.
