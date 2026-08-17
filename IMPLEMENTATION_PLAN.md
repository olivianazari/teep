# Teep Analysis Tool — Implementation Plan

**Audience:** AI coding agent.
**Read this document fully before writing code.** Every number in it is a decision, not a
suggestion. Where a value is marked `TUNABLE`, it lives in a config file and must not be
hardcoded at a call site.

> **Status: built and running.** All nine steps of §13 are complete and the §14 gate
> passes. This document has been reconciled with what actually ships — several
> numbers changed during the build, and blockquotes like this one record what moved
> and why. If a value here disagrees with `backend/config.py`, the config is
> authoritative and this document has drifted; fix it.
>
> The invariants in `CLAUDE.md` all still hold: four scored metrics, no LLM,
> `teep_extract.py` unmodified, video_A never scored, self-comparison exactly 100.0.
>
> Changed from the original plan: the strictness dial (§5), `MIN_PHASE_FRAMES` (§7.6),
> `FEEDBACK_SUPPRESS_ABOVE` (§8), a new warp-path run penalty (§6.3a), the reference
> clip itself (§4), and the entire frontend now follows the Figma design (§10).
>
> The design has since been revised further. Three changes there are structural
> rather than cosmetic, and each is written up where it belongs: the **improvements
> panel is gone** and the scores moved below the videos (§10 Structure), so the
> feedback engine's output now reaches the athlete only through the timeline's
> adjustment markers; **SSE progress is no longer displayed** (§10 Upload dialog);
> and there is a new **export with the skeleton burned in** (§10 Export video).
> "Torso" reads as "Body" in the UI — the metric key is unchanged.

---

## 1. What this is

A locally-hosted web app that compares a Muay Thai teep (front kick) in an uploaded video
against a fixed reference video, and produces a strict numeric score plus written feedback.

The user downloads a file pack, runs one command, and a browser opens. No cloud, no API
keys, no network access required at runtime.

- **video_A** — the reference. Ships with the app. Always displayed on the left. **Never scored.**
- **video_B** — the user's upload. Displayed on the right. **This is what gets scored.**

### Non-goals (explicitly out of scope)

Do not build these. They were considered and rejected.

| Excluded | Reason |
|---|---|
| LLM / AI feedback generation | Feedback is deterministic templates. No key, no model, no network. |
| Cloud hosting, job queue, Redis, Celery | Single user, one video at a time. Synchronous + SSE is sufficient. |
| Capture-protocol validation UI | Assume all uploads are full-body, fixed-camera, side profile. |
| Kick-side mirroring | All uploads are the same kicking side. Mismatch is refused, not corrected. |
| Multi-rep reference sets | One reference video only. |
| `hip_drive` metric | Removed. Correlated r = −0.955 with torso tilt. |
| `rear_hip_flexion` metric | Removed. Correlated r = −0.98 with torso tilt. |
| Phase-normalized timeline mode | Real-time proportional timelines only. |
| User accounts, persistence, history | Stateless per session. |

---

## 2. Architecture

```
┌─────────────────────────────────────────────────┐
│  Browser (React + TS)  →  http://localhost:8000 │
└─────────────────────────────────────────────────┘
                      ↕ HTTP + SSE
┌─────────────────────────────────────────────────┐
│  FastAPI (single process)                       │
│    • serves built React bundle as static files  │
│    • POST /api/analyze  → SSE progress → result │
│    • runs teep_extract.py pipeline in-process   │
│    • DTW + scoring + feedback (Python)          │
└─────────────────────────────────────────────────┘
```

**One process. One port. One command.** The frontend is built to static assets at package
time and served by FastAPI. There is no separate Node dev server in the shipped pack.

### Why the backend is Python

`video_A_metrics.csv` was produced by `teep_extract.py`. Video_B must be measured by the
**same instrument** or the comparison is meaningless — a different MediaPipe version or
backend produces systematically different numbers, and that bias would silently appear in
scores as the athlete's error. Do not reimplement the biomechanics in TypeScript.

---

## 3. Repository layout

```
teep-analyzer/
├── run.py                     # entrypoint: starts server, opens browser
├── pyproject.toml             # pinned deps (see §11)
├── backend/
│   ├── main.py                # FastAPI app, routes, SSE
│   ├── teep_extract.py        # EXISTING — do not modify (see §4)
│   ├── pipeline.py            # runs teep_extract's functions over an upload
│   ├── reference.py           # loads + caches video_A metrics
│   ├── align.py               # DTW (§6)
│   ├── scoring.py             # scoring engine (§7)
│   ├── feedback.py            # template engine (§8)
│   ├── analysis.py            # align + score + feedback -> result object (§9)
│   ├── landmarks.py           # per-frame pose rows for the overlay (§10.2)
│   └── config.py              # ALL tunable constants
├── assets/
│   ├── video_A.mp4
│   ├── video_A_metrics.csv
│   ├── video_A_summary.json
│   ├── video_A_landmarks.json        # overlay skeleton for the reference
│   ├── reference_provenance.json     # extraction params used for video_A
│   └── pose_landmarker_heavy.task    # ONLY if backend == "tasks"
├── .mcp.json                  # shadcn MCP server (see §10.0)
├── frontend/
│   ├── components.json        # shadcn config
│   └── src/
│       ├── App.tsx
│       ├── assets/figma/      # icons exported from the design (§10)
│       ├── components/        # AnalyticsStrip, Timelines, PhaseLegend,
│       │                      # SkeletonOverlay, Sparkline, IconButton,
│       │                      # UploadDialog, ui/ (shadcn, editable)
│       └── lib/               # api, warp, grade, skeleton, apex, exportVideo
└── dist/                      # built static bundle (served by FastAPI)
```

`pipeline.py` and `analysis.py` are additions to the original plan. They keep
`main.py` to routing, and let the API and the §14 self-comparison test share one
code path — so the thing the test certifies is the thing the server serves.

---

## 4. The extraction script

`teep_extract.py` already exists and **requires no changes**. All four scored metrics are
already present in its output. Call it as a module or subprocess; do not rewrite it.

### Reference CSV loader — two gotchas

1. **`video_A_metrics.csv` has a stray title line above the header row.** The loader must
   detect and skip a leading non-header line. Do not assume `pd.read_csv` works unaided.
2. **Extraction parameters must be identical for video_A and video_B.** Not just the
   backend — every parameter that changes the numbers. Define them once in `config.py` and
   pass them explicitly on every run. Never rely on argparse defaults.

   ```python
   EXTRACTION = {
       "backend":    "legacy",   # model ships in the mediapipe wheel; nothing to download
       "complexity": 1,          # legacy only: 0 lite, 1 full, 2 heavy
       "cutoff":     10.0,       # Butterworth low-pass Hz
       "det_conf":   0.5,
       "track_conf": 0.5,
       "leg":        "auto",
       "person":     "any",      # legacy only
   }
   ```

   `legacy` (`mp.solutions.pose`) has its model bundled in the mediapipe wheel — nothing to
   download, offline by default. `tasks` requires `pose_landmarker_heavy.task` (~30 MB) to
   be bundled in `assets/`. **The two produce different numbers.**

   **Regenerate video_A's reference CSV using these exact parameters** rather than trusting
   the shipped one, whose provenance is unknown:

   ```bash
   python teep_extract.py --video assets/video_A.mp4 --backend legacy \
       --complexity 1 --cutoff 10.0 --out assets/
   ```

   This guarantees A and B come from the same instrument by construction. Tolerances are
   derived from this CSV at load time (§5), so they update automatically.

3. **Record provenance.** `summary.json` does not currently log which backend or parameters
   were used, so a reference CSV carries no record of how it was made. Write the resolved
   `EXTRACTION` dict into every result and into a `assets/reference_provenance.json`
   alongside video_A. On startup, if the two disagree, refuse to score and say so — a
   silent parameter mismatch produces plausible-looking wrong scores, which is the worst
   possible failure mode here.

### Reference facts (video_A, for test assertions)

The clip originally described here (118 frames @ 30.003 fps) was never the one
shipped in `assets/`. What shipped first was a rendered debug video with a pose
skeleton, a HUD and subtitles burned into the pixels, at 100 frames / 25 fps —
and its second athlete broke tracking, giving 65% detection. It was replaced
with clean source footage during the build. These are the current facts:

```
frames: 99           fps: 30.0          duration: 3.30 s
kick_side: left      detection rate: 100%       apex frame: 48
phase frame counts:
  ready 35 | chamber 4 | extension 8 | impact 2 | retraction 4 | recovery 19 | reset 27
active window (non-ready, non-reset): 37 frames, t = 1.17–2.37 s
```

The source is variable-frame-rate: 102 decoded frames are resampled to 99 on an
even timebase. That is the case §10 warns about — after resampling, row *n* is
no longer decoded frame *n*, so the frontend keys on `time_s` throughout.

Do not hardcode any of these numbers. Every one of them changes if the reference
is re-shot, and everything downstream derives from the CSV at load time.

---

## 5. Metric definitions

Four scored metrics. `lead` = the kicking leg, `rear` = the support leg.

| UI label | CSV column | Weight | Full credit | Zero credit |
|---|---|---|---|---|
| Lead hip flexion | `kick_hip_flexion` | 33% | ±11.3° | ±38.3° |
| Lead knee angle | `kick_knee_angle` | 33% | ±12.6° | ±42.8° |
| Body tilt | `trunk_lean_sagittal` | 29% | ±7.0° | ±22.0° |
| Rear knee angle | `sup_knee_angle` | 5% | ±7.0° | ±22.0° |

The metric **key** stays `torso_tilt` — it is the identity the scorer, the API
and the tests use. Only the label the athlete sees reads "Body".

Weights sum to 100. `TUNABLE`.

The degree columns are **derived, not configured** — they are what the current
reference and the current dial happen to produce. See below.

### How the tolerances are derived

**Compute these at load time from the reference CSV. Do not hardcode the degree values.**

```python
range_A      = metric.max() - metric.min()      # over video_A's active window
full_credit  = max(0.13 * range_A,  7.0)
zero_credit  = max(0.44 * range_A, 22.0)
```

Store the two percentages (`0.13`, `0.44`) and the two floors (`7.0`, `22.0`) in config —
**these four numbers are the strictness dial** and are the only values that should be
adjusted to make grading harsher or softer.

> **Loosened from the original `0.08 / 0.25 / 4.0 / 12.0`.** Those graded far too
> harshly on real footage: a competent teep with one clear postural fault scored
> 65. The floors did most of the damage. Tolerances are `max(frac × range, floor)`,
> and two of the four metrics have ranges small enough that the floor always won —
> torso tolerated ±12° while the knee tolerated ±24°, despite torso carrying 29% of
> the weight, putting 44% of its frames on a hard zero. Raising the floors
> alongside the fractions keeps the four metrics comparable to one another rather
> than just shifting every score up.
>
> When retuning, watch the **spread** between the best- and worst-scoring phase on
> a rep with a known fault. Below about 10 points the tool has stopped
> discriminating, and past roughly `0.30 / 0.90 / 15 / 45` the feedback panel
> empties entirely and every rep comes back clean.

Deriving rather than hardcoding means regenerating video_A's reference CSV (different
backend, different smoothing cutoff, a re-shoot at 60 fps) automatically updates the
tolerances to match. Hardcoded values would silently go stale.

The degree values in the table above are what this formula yields for the current reference
(ranges: `kick_hip_flexion` 87.0° · `kick_knee_angle` 97.2° · `trunk_lean_sagittal` 46.5° ·
`sup_knee_angle` 33.5°). Assert the **formula** in tests, not the degree values — those
move whenever the reference or the dial changes, and `tests/test_acceptance.py`
checks the derivation rather than the numbers for exactly that reason.

### Semantics — critical for feedback wording

- `*_hip_flexion`: **higher = more flexed.** 0° = thigh in line with trunk.
- `*_knee_angle`: **higher = straighter.** 180° = fully extended. A positive delta means
  *straighter*, NOT "more flexed." Getting this backwards inverts every knee cue.
- `trunk_lean_sagittal`: **positive = leaning forward.** Teep values are negative
  (leaning back). Higher = more upright.

### Rear knee angle — known low signal

`sup_knee_angle` spans only 33.5° across the entire kick; the support leg is
near-straight throughout. That range is close to MediaPipe's own error on the joint. It
carries 5% weight and a deliberately wide tolerance floor. Do not "fix" this by tightening
its band.

---

## 6. Temporal alignment (DTW)

### 6.1 Active window only

Run DTW on the **active window only** — phases `chamber` through `recovery`, excluding
`ready` and `reset`.

In video_A, 62 of 99 frames are someone standing still. Including them lets the cost
matrix be dominated by idle frames, and the warping path will happily align A's `ready` to
B's `reset` because both are cheap. Segment first, align second.

### 6.2 Feature vector

Per frame, the four scored metrics, each z-normalized using **video_A's** active-window
mean and SD (so both series share one normalization).

### 6.3 Apex anchoring

Constrain the warping path to pass through `(apex_A, apex_B)`. Both apex frame indices come
from `summary.json` → `phases.apex`, computed independently per video.

Peak leg extension is a real physical extremum. Forcing the path through it prevents drift
in low-contrast stretches (chamber frames look much alike) and improves alignment across
the whole window, not just at impact.

Implement as two DTW runs — `[start..apex]` and `[apex..end]` — concatenated. Simpler and
more robust than a constrained single pass.

### 6.3a Run penalty on the warping path

`WARP_RUN_PENALTY = 0.8` — cost added when the path repeats a non-diagonal step.
`TUNABLE`; `0.0` restores plain DTW.

Where the clips differ in length the path must take a fixed number of
non-diagonal steps — that count is pure geometry and no penalty changes it — but
it is free to take them all in one run or to spread them out. Plain DTW has no
preference and routinely picks a single long run, which on screen is **one video
frozen on a single frame while the other plays on**. Charging for a repeat of the
same direction makes the spread-out path strictly cheaper.

Two approaches were rejected. A flat per-step penalty is cost-neutral, since the
step count is fixed. A hard slope constraint (Itakura) caps compression at 2:1,
and a real athlete can replant three or four times faster than the reference — at
which point the path is forced across phase boundaries to stay feasible.

Do not raise it much above 0.8. Measured on a real upload, 1.2 began pulling the
path across the retraction/recovery boundary: phase correspondence fell from 92%
to 87% and retraction's timing ratio went from a correct 1.00 to a fabricated
0.75. Smoother playback is not worth a wrong measurement.

This only redistributes compression, it cannot remove it. A phase the athlete
covers in 5 frames where the reference takes 19 will hold for ~4 frames however
the path is drawn; the UI labels that rather than hiding it (§10).

### 6.4 Timing score

DTW warps time away, which means post-warp deviation **cannot see tempo errors**. A slow,
sloppy teep will warp onto a fast crisp one and score well on shape. Timing must therefore
be scored separately, from the warping path itself.

For each Tier-1 phase (chamber, extension, retraction, recovery):

```
r         = duration_B / duration_A
deviation = |log2(r)|
score     = 100 if deviation ≤ 0.20        (within ~±15%)
            0   if deviation ≥ 1.00        (2× too slow or fast)
            linear in between
```

`timing_score` = phase-weight-weighted mean of those, renormalized over Tier-1 phases only.

**Report timing separately from the shape score. Never blend them into one number.** The
coaching response to "wrong shape" and "wrong tempo" are entirely different.

---

## 7. Scoring engine

### 7.1 Per-frame, per-metric

```
delta = |value_B[frame] − value_A[corresponding_frame_via_warp_path]|

score = 100                                          if delta ≤ full_credit
        0                                            if delta ≥ zero_credit
        100 × (zero − delta) / (zero − full)         otherwise
```

Linear. Strictness lives in the band widths, not the curve shape.

### 7.2 Per-frame composite

Metric-weighted sum of the four per-frame metric scores. Range 0–100.

### 7.3 Phase score

Three different methods depending on the phase. This matters — do not apply one uniformly.

| Phase | Method |
|---|---|
| `chamber`, `extension`, `retraction`, `recovery` | Mean of per-frame composites over the phase's frames in B (warp-aligned) |
| `impact` | **Event-based** — see 7.4 |
| `ready`, `reset` | **Static** — see 7.5 |

### 7.4 Impact is an event, not a phase

`segment_phases` builds the impact window as ±40 ms around apex. At 30 fps that is ±1
frame, so impact comes out **2–3 frames long** in any video it processes, including B. A
phase score built on 2 samples swings wildly between near-identical uploads.

Instead: score impact over **apex ± 2 frames** (5 frames at 30 fps) using each video's own
apex, regardless of what the phase labeler emitted. Same sample count every time.

This is done **in the scorer**. Do not widen the window inside `segment_phases` — that
would shift phase boundaries in video_A and require regenerating the reference CSV.

The 5-frame minimum in §7.6 does not apply to impact.

### 7.5 Ready and reset are scored statically

These sit outside the DTW active window, so there is no frame correspondence and nothing to
compare frame-to-frame. Score them by comparing single numbers:

```
delta = |median(metric over phase in B) − median(metric over phase in A)|
```

Then through the same tolerance bands. No alignment needed — there is no motion to align.

### 7.6 Phase weights

| Phase | Weight |
|---|---|
| chamber | 20% |
| extension | 20% |
| retraction | 20% |
| recovery | 20% |
| ready | 6.67% |
| impact | 6.67% |
| reset | 6.67% |

Tier 1 : Tier 2 ratio is 3:1. `TUNABLE`.

**Weight by phase, not by frame.** Chamber is 4 frames and reset is 44 in the reference. A
flat frame average would let standing still drown out the kick. Score each phase
independently, then combine by phase weight.

**Minimum frame floor:** if a phase has fewer than `MIN_PHASE_FRAMES` frames in video_B,
drop it and redistribute its weight proportionally across surviving phases. (Exempt:
`impact`.) Without this, a clip that starts mid-kick gets scored on a `ready` stance that
was never filmed.

> **`MIN_PHASE_FRAMES = 4`, lowered from 5.** At 30 fps a crisp chamber genuinely
> lasts 4 frames — it is 4 in the reference, and `retraction` is 4 as well — so a
> floor of 5 dropped both and moved 40% of the phase weight onto the survivors for
> any rep at reference tempo. The self-comparison in §14 was silently scoring only
> five of seven phases. The trade-off: a 4-frame phase is ~130 ms, so `chamber` and
> `retraction` scores rest on few samples and are noisier than the longer phases.
> Shooting the reference at 60 fps would remove the tension entirely.

### 7.7 Overall score

Phase-weighted sum of phase scores. Range 0–100.

Reported alongside — never merged with — `timing_score`.

---

## 8. Feedback engine (deterministic templates)

No LLM. A lookup table of four sentences, filled from data the scorer already produced.

```yaml
lead_hip_flexion:
  "Lead hip is {delta}° {more|less} flexed than reference during {phase}."
lead_knee_angle:
  "Lead knee is {delta}° {straighter|more bent} than reference during {phase}."
rear_knee_angle:
  "Rear knee is {delta}° {straighter|more bent} than reference during {phase}."
torso_tilt:
  "Body is {delta}° {more upright|further back} than reference during {phase}."
```

**Fill rules**

- `delta = |mean signed deviation across the phase|`, always absolute, rounded to whole
  degrees. Decimals imply precision MediaPipe does not have.
- The direction word carries the sign: **left word when B > A, right word when B < A.**
- `{phase}` is the phase display name.

**Selection**

1. Compute a `(metric, phase)` deviation for every combination.
2. **Suppress any pair whose metric score in that phase is above 80**
   (`FEEDBACK_SUPPRESS_ABOVE`). Above 80 is good enough not to flag.
3. Rank survivors by `severity_in_tolerance_units × metric_weight × phase_weight`.
4. Take them in that order, skipping any metric that already has
   `FEEDBACK_MAX_PER_METRIC` (2) items, until `FEEDBACK_TOP_N` (5) are chosen.

> **The per-metric cap is new, and `TOP_N` moved 3 → 5.** Severity order alone
> let one metric take every slot: on a real upload all three went to
> `torso_tilt` (chamber 55.9, extension 54.0, recovery 67.8), cutting a
> `lead_knee_angle` at 76.6 and a `lead_hip_flexion` at 71.7 that the scorer
> had already found. The athlete was told the same thing three times and never
> heard about the other two — and with the improvements panel gone (§10), a cut
> item leaves no marker and is therefore completely invisible.
>
> Raising `TOP_N` alone would not have fixed it; slots four and five would have
> gone to `torso_tilt` as well. The cap is what makes the list representative.
> It cannot manufacture items — everything still has to clear the ≤80 filter
> first, so a clean rep still produces nothing.

> **80, lowered from 90, to match the UI's green band** (§10.1: green 100–80,
> yellow 79–60, red 59–0). A card that reads green while the panel still writes
> that metric up would tell the athlete two different things about the same
> number. These two constants must move together — `FEEDBACK_SUPPRESS_ABOVE` in
> `backend/config.py` and `GRADE_GOOD` in `frontend/src/lib/grade.ts`.

Each rendered item also carries the frames of video_B it refers to
(`start_frame`, `end_frame`) and the single frame where that metric is furthest
off (`worst_frame`), so the UI can send the viewer straight to it. The mean
deviation says how bad a phase is; `worst_frame` says where to look.

`worst_frame` is now the item's **only** surface. The improvements panel it was
originally built for has been removed (§10), so feedback reaches the athlete
solely through the adjustment markers on the timeline: a marker at each
`worst_frame`, its sentence on hover, a seek on click. The scorer records the
frame per metric per phase and the result object carries it — the marker never
recomputes it.

**Empty state:** a rep where everything clears 80 produces zero feedback items, and
therefore no markers. See §10 on the ambiguity that creates.

Expect markers on most uploads. That is what strict grading means.

---

## 9. API contract

### `GET /api/reference`

Returns video_A metadata: frame count, fps, phase boundaries, apex frame, and the full
per-frame metric series for the four scored metrics (for sparklines and the reference
timeline).

Also returned, all consumed by the frontend:

- `time_s` — per-row timestamps. The frontend keys on these, never on row index (§10).
- `tolerances`, `metrics` — derived bands and metric labels/weights, so the UI never
  duplicates a scoring constant.
- `landmarks` — per-frame pose rows for the overlay (§10.2), or `null`.
- `phase_colors`, `phase_border_colors`, `phase_legend` — the phase palette and its
  legend labels. `backend/config.py` is the single source; the UI never hardcodes these.
- `extraction`, `warnings`, `detection_rate`, `mean_pelvis_tilt_conf`.

### `POST /api/analyze`

Multipart upload of video_B. Returns an SSE stream:

```
event: progress   data: {"stage": "extracting", "pct": 34}
event: progress   data: {"stage": "smoothing",  "pct": 60}
event: progress   data: {"stage": "segmenting", "pct": 75}
event: progress   data: {"stage": "aligning",   "pct": 85}
event: progress   data: {"stage": "scoring",    "pct": 95}
event: result     data: { ...full result object... }
event: error      data: {"code": "...", "message": "..."}
```

Stages mirror the script's own `[1/4]`–`[4/4]` output. Expect 10–30 s total.

### Result object

```jsonc
{
  "overall_score": 72.4,
  "timing_score": 88.1,
  "kick_side": "left",
  "fps": 30.0,
  "frame_count": 131,
  "apex_frame": 61,
  "phases": [ { "name": "chamber", "start": 44, "end": 48, "score": 65.2, "weight": 0.20 } ],
  "frames": [ { "frame": 0, "time_s": 0.0, "phase": "ready", "composite": 91.2,
                "metrics": { "lead_hip_flexion": { "value": 12.4, "ref_value": 11.9,
                                                   "delta": 0.5, "score": 100 } } } ],
  "warp_path": [[0, 0], [1, 1], [1, 2], [2, 3]],
  "feedback": [ { "metric": "lead_knee_angle", "metric_label": "Lead knee angle",
                  "phase": "extension",
                  "text": "Lead knee is 14° more bent than reference during extension.",
                  "delta": 14, "score": 63.8, "severity": 2.3,
                  "start_frame": 50, "end_frame": 57, "worst_frame": 51 } ],
  "diagnostics": { "detection_rate": 0.97, "mean_pelvis_tilt_conf": 0.68, "warnings": [] },

  // added during the build
  "dropped_phases": [ { "name": "chamber", "frames": 3 } ],
  "timing_phases":  [ { "name": "recovery", "duration_a_s": 0.633, "duration_b_s": 0.267,
                        "ratio": 0.42, "deviation": 1.24, "score": 0.0, "weight": 0.2 } ],
  "landmarks":      [ [x0, y0, v0, x1, y1, v1, ...] ],   // per frame, or null
  "extraction":     { "backend": "legacy", "complexity": 1, ... },
  "reference":      { "frame_count": 99, "fps": 30.0, "apex_frame": 48, ... },
  "upload_token": "…", "video_url": "/api/upload/…/video", "filename": "teep.mov"
}
```

`phases[]` entries also carry `frames`, `method` (`warped` / `event` / `static`)
and `metric_scores`, so the UI can show which of the three §7.3 methods produced
a phase score without recomputing anything.

`warp_path` is `[[frame_A, frame_B], ...]` and is what the frontend uses for synced
playback and the timeline ribbon.

---

## 10. Frontend

> **The Figma redesign has landed.** This section originally described a functional
> default to be replaced later; that replacement has happened. The live design is
> Figma file `F0U2Kxe24jI5YBI1HIWhrr`, frame `92:483` ("Desktop - 9"), pulled through
> the Figma MCP server. Where the text below and the design disagree, **the design
> wins** — but read the deviations at the end of this section first, because a few
> things the design does not cover are still required by §6, §12 and §14.

### 10.0 Component library — shadcn/ui

Use **shadcn/ui** for all standard UI primitives. React + TypeScript + Tailwind.

**Install components via the shadcn MCP server, not from memory.** shadcn/ui changes
between versions, and component props recalled from training data are frequently wrong or
invented. The MCP server gives live access to real component source.

Setup — add to `.mcp.json` in the project root, then restart and run `/mcp` to confirm the
server shows as Connected:

```json
{
  "mcpServers": {
    "shadcn": {
      "command": "npx",
      "args": ["-y", "shadcn@latest", "mcp"]
    }
  }
}
```

Reference: https://ui.shadcn.com/docs/mcp

Note that shadcn components are **copied into the repo** (`components/ui/`), not installed
as a dependency. They are yours to edit.

#### Component mapping

| UI element | shadcn component |
|---|---|
| Upload modal | `Dialog` (drop zone and loading state inside are custom) |
| Metric cards (×4) | `Card` |
| Overall score | `Card` |
| Errors, low-detection warnings | `Alert` (`destructive` variant for refusals) |
| Dropped-phase notice | `Alert` |

Three rows have gone since the design landed. The **feedback panel** no longer
exists (see Structure). **SSE progress stages** are no longer shown, so there is
no `Progress` — the loading state is the design's own animation. **Frame-step
controls** and the **pane buttons** are the design's own component sets rather
than `Button` variants; see `IconButton` below.

#### Build these custom — no shadcn equivalent exists

Do not force these into Radix primitives.

- Both `<video>` elements and the warp-sync controller
- Timeline tracks with phase-colored segments
- The warp ribbon between timelines (SVG)
- Apex tick markers
- Adjustment markers on the athlete's track (§10 Timelines)
- Sparklines inside metric cards (SVG)
- Playhead spanning both timelines

#### Styling constraint

**Superseded.** The original instruction was to stay on default shadcn tokens and avoid
inventing a visual identity, because a Figma redesign was coming. It came. The design's
values now live as tokens in `frontend/src/index.css` — canvas, surface, ink, radii,
grade tints — and components reference those tokens rather than raw hexes.

Rules that still hold:

- **Never hardcode a design value at a call site.** It goes in `index.css` as a token,
  or in `backend/config.py` if the backend also needs it.
- **Phase colors live in `backend/config.py`** (`PHASE_COLORS`, `PHASE_BORDER_COLORS`,
  `PHASE_LEGEND`) and reach the UI through `/api/reference`. They are functional, not
  decorative — they encode which phase a segment is.
- The design's legend names the stance phase "Idle" and shows six bands, so `ready`
  and `reset` — the same standing stance either side of the kick — share one colour.
- **PP Mori is the design's typeface and is not bundled** (licensed). The font stack
  names it first and falls back to Geist, which ships with the app. This is the
  largest visual gap against the Figma frames.
- Icons come from the design's own exported SVGs in `frontend/src/assets/figma/`,
  committed rather than hotlinked — Figma's asset URLs expire in ~7 days.
- **Radii are tokens, one per role**: `--radius-card` 30px (every block on the
  page and the metric cards), `--radius-tip` 15px (hover tip and the upload
  dialog), `--radius-control` 4px, `--radius-band` 2px. The pane buttons carry
  the design's own 10px and are the one exception, set in `IconButton`.

### Structure (top to bottom)

> **Reordered, and the improvements panel is gone** (design node `100:969`).
> The scores moved from the top of the page to below the videos, and the
> written-feedback block was removed outright — `FeedbackPanel.tsx` is deleted.

```
┌────────────────────────────┬─────────────────────────────┐
│ YOU  [upload][export][skel] │ IDEAL            [skel]    │
│   video_B (yours)           │   video_A (reference)      │
├────────────────────────────┴─────────────────────────────┤
│ [Overall 89][lead hip][lead knee][rear knee][body]       │
├──────────────────────────────────────────────────────────┤
│  legend ·········· [◀ ▶ ▶|] ··········                   │
│  Timeline YOU        ═══ warp ribbon ═══                 │
│  Timeline IDEAL                                          │
└──────────────────────────────────────────────────────────┘
```

There is **no page header**. Its controls live in the video pane headers: a
skeleton toggle in each (they drive their own overlay independently), plus upload
and export in the YOU pane. The frame/phase readout sits beside the pane label,
because the right edge now belongs to those controls.

Vertical space is the scarce dimension on a 1440×900 laptop. Every block except the
videos is fixed height; the video row is the flex element that absorbs the slack, so
it is the first thing to suffer when anything below it grows.

**Every block must hold its height across states.** The metric cards render before
an upload with empty figures rather than appearing on result — otherwise uploading
would inject their height into the column and shrink both videos at the moment the
athlete wants to look at them.

**What removing the improvements panel costs.** The feedback engine still runs and
its sentences still reach the athlete, but only through the timeline's adjustment
markers — hover for the sentence, click to seek (§8, and Timelines below). Nothing
now lists all three items at once, so a fault is only discoverable by finding its
marker. That is a real reduction in what the tool says out loud; it is the design's
call, recorded here so it is not mistaken for an oversight.

### Pane header buttons — `IconButton`

One component for all four (design component sets `100:935`, `104:1214`,
`100:947`, `100:953`, each drawn with `state=idle` and `state=hover`).

- 35px square, 10px radius, 5px inset around the glyph.
- Two tones. `solid` — `bg-ink`, hover `#545454` — is the upload button, the
  only primary action. `subtle` — `#f7f6f5`, hover `#e5e2e0` — is export and the
  skeleton toggle.
- **Only the surface changes on hover.** The design's exported hover glyphs are
  byte-identical to the idle ones, so one asset serves both states and hover is
  pure CSS. Do not export and ship a second copy of each icon.
- Disabled keeps the idle surface (`enabled:hover:` guards both tones), so a
  button that cannot act does not light up under the cursor.
- The header carries a bottom border and a 20px gap beneath it; without the gap
  the buttons sit on top of the video.

### Export video (with skeleton)

`frontend/src/lib/exportVideo.ts`. Writes the athlete's clip to a file with the
pose overlay burned in — the overlay is a canvas painted over a `<video>`, so
the composite exists only on screen and there is nothing on disk to hand over.

**Step the frames; do not play the clip.** The first version called `play()` and
captured the stream in real time, which produced a 0.03 s / 42 KB file whenever
the tab was not frontmost, because playback *and* `requestVideoFrameCallback` are
throttled in a hidden document. Seeking is not throttled, so stepping works
whatever the tab is doing — and a user is very likely to switch away during an
export.

- `canvas.captureStream(0)` puts the stream in manual mode; exactly one frame
  reaches the recorder per `track.requestFrame()`.
- Each step seeks to `time_s + 1/(2·fps)` — mid-frame, because seeking to an
  exact boundary can decode either side of it.
- Steps are paced to `1000/fps` against a wall clock. MediaRecorder timestamps by
  wall clock, so without the pacing the file is correct but plays at seek speed.
- `video/mp4;codecs=avc1` where the browser supports it, else webm.

The overlay and the export share **one** drawing function — `drawPose` in
`lib/skeleton.ts`. They must not each own a copy, or the exported skeleton will
drift from the one the athlete checked on screen.

Scope: the athlete's clip only, not the side-by-side, and it runs at real time
(~4 s for a 3.3 s clip) because of the pacing above.

### Metric cards (×4)

Metric name · B's value at the current frame · the ideal it was measured against ·
sparkline of both traces with a playhead. The overall score is rep-level, not
frame-level, and sits at the **leading** edge of the row so it reads first.

The "ideal" figure must be the value the score was actually measured against —
the warp-aligned frame inside the active window, the phase median outside it.
Showing A's value at the current A frame instead puts a number on screen that
the score does not reconcile with.

### 10.1 Grade colours

Scores band as **green 100–80 · yellow 79–60 · red 59–0** (`GRADE_GOOD` /
`GRADE_POOR` in `frontend/src/lib/grade.ts`).

- The **overall score card** is filled with the grade tint.
- The **four metric cards** keep a plain white surface and colour the figure and
  its sparkline trace instead: `#C27000` yellow, `#BE0000` red. A good score stays
  at default ink — only a problem gets coloured, so colour always means "look here".
- The reference trace stays neutral grey so the two are distinguishable when the
  card goes red.

`GRADE_GOOD` is matched by the backend's `FEEDBACK_SUPPRESS_ABOVE` (§8), so a card
is green exactly when the feedback engine stays silent about that metric — and
therefore exactly when that metric puts no marker on the timeline.

Metric cards grade **per frame**, like the values beside them, so the colours change
as you scrub. A fixed per-rep grade would need the backend to expose a single
rolled-up score per metric, which it currently does not.

### 10.2 Pose skeleton overlay

Not in the original plan; added during the build. Each pane can draw the detected
skeleton over its video, toggled per pane.

Landmarks come from `image_s` — the very array `compute_metrics` consumed — so the
overlay shows the pose the scores were taken from, not a second detection run.
`backend/landmarks.py` packs them per frame as `[x, y, visibility, …]` in
normalised image coordinates; the reference's are cached in
`assets/video_A_landmarks.json`, an upload's ride along in the result object.

Drawn on a canvas rather than SVG: it repaints every frame of playback, and
reconciling ~40 SVG nodes 30 times a second is a cost a single clear-and-stroke
is not.

**Colours** come from design node `111:1251` and live in `SKELETON_COLORS`:
lead (kicking) leg `#c8e9ef`, rear (support) leg `#ffdfb3`, body and arms
`#e6dfe0`, over a dark halo for contrast against light footage. They reuse the
phase palette deliberately, so a limb on the video and a band on the timeline
speak the same visual language.

`drawPose` lives in `lib/skeleton.ts` and is shared with the exporter (see Export
video above) — the overlay and the exported file are the same drawing code.

**Pick the frame from what the video has actually presented, not from React
state.** Setting `currentTime` returns immediately but the picture changes tens of
milliseconds later (measured ~13 ms), so drawing on the state change paints the new
pose over the old frame. The torso barely moves between frames and looks fine; the
kicking leg moves a long way and looks badly out of sync. Track
`requestVideoFrameCallback`'s `mediaTime`, with a `seeked` listener as fallback.

Landmarks are purely diagnostic — nothing in scoring reads them.

### Playback — warp-synced

Both videos play with **B time-remapped through the warping path** so they always show the
same phase. Frame-stepping A advances B to its warp-corresponding frame and vice versa.

Tempo error is not lost — it is reported as `timing_score`.

**Label the holds.** Where the athlete is faster than the reference, several
reference frames map onto one of theirs and their video legitimately holds still
while the reference plays on. Left unlabelled that reads as a stalled player, and
it is the most common "is this broken?" report. Surface the count
(`holding N ref frames`) once it exceeds ~2. The stall is information, not a
defect: it is the same tempo error `timing_score` measures.

**Do not use `video.currentTime` seeking alone for frame stepping.** HTML5 seeks are not
reliably frame-accurate. Use `requestVideoFrameCallback`, and key the timeline on `time_s`
rather than array index — the pipeline resamples variable-frame-rate input, after which row
*n* no longer corresponds to decoded frame *n*.

### Timelines

Two tracks, phase segments color-coded, playhead spanning both. The **warp
ribbon** drawn between them connects corresponding frames; its compression and stretching
*is* the timing error made visible. This is the clearest proof the alignment works — do not
cut it.

**Track order: the athlete on top, the reference beneath** — matching the video
panes above, where the athlete is on the left. The athlete's is the row being
read, so it leads in both blocks. Write the vertical geometry in terms of
`Y_TOP` / `Y_BOTTOM` rather than of A and B, so the order is one assignment
rather than a dozen edits. Three things
are ordering-sensitive and will silently draw wrong if they are keyed to the
series instead of the position: the ribbon's endpoints (its control points assume
`y1 < y2`), the two scrub hit areas that meet in the middle of the ribbon, and
which caption goes in the gap.

**Apex tick:** draw a distinct marker at the apex frame on each track. It is the most
meaningful single frame in the clip. Note the scored impact window (apex ±2) is wider than
the 2–3 frame `impact` band the labeler emits; the tick resolves the discrepancy visually.

**Geometry.** Tracks are `TRACK_H = 13`px with `RIBBON_H = 31`px between them;
bands carry a `PHASE_GAP = 5`px gap and a `MIN_BAND_W = 3`px floor so a 2-frame
phase still renders. All in `Timelines.tsx` as named constants — no magic numbers
inside the path maths.

**Adjustment markers.** Draw the design's alert glyph (node `94:637`, red
`#BE0000`) on the athlete's track at each feedback item's `worst_frame` (§8).
Hovering gives the sentence; clicking seeks both videos there. Since the
improvements panel was removed this is the **only** route to the written
feedback, so it is load-bearing rather than a convenience. Four things it must
get right:

- **The athlete's track only.** The items describe what *they* did and
  `worst_frame` indexes their video; a marker on the reference would be asserting
  a fault in the reference.
- **Render after the scrub hit areas.** Those cover the whole strip, so anything
  drawn earlier is unclickable. Stop pointer-down propagation too, or clicking a
  marker also starts a scrub drag.
- **Group by frame.** Two items can share a worst frame. One marker carrying both
  sentences, not two glyphs on the same pixel.
- **Centre it on the band**, scaled to `ALERT_W = 12` from the 20×18 export —
  matching the apex tick. (The original instruction was to place it in the gap
  *above* the band; there is no such gap at 13px, and the same placement clipped
  the apex star off the top of the SVG.)

**Hover tip.** Design node `104:1186`, `--radius-tip` 15px, in two variants:
amber (`#ffdfb3` on `#e48300`) and red (`#fbd5d6` on `#ff4e51`). The variant
tracks the item's severity, so the tip agrees with the metric card's colour
rather than contradicting it.

**Legend** carries the six phase bands plus a **"Bad form"** entry showing the
alert glyph, so the marker is explained rather than left to be guessed at.

A clean rep produces no markers, exactly as it produces no feedback items. That
does mean an analysed-and-clean timeline looks identical to one that was never
analysed — with the improvements panel gone there is now nothing else on the page
that distinguishes the two, so this ambiguity is worse than it was.

### Upload dialog and loading state

Triggered from the **YOU pane header**, not a page header. File picker **and**
drag-and-drop. Accept common video containers. On success, replaces video_B and
re-runs analysis.

Both states are drawn by the design and follow it rather than shadcn defaults:

- **Upload** — node `97:686`. 556px, `--radius-tip`, dashed drop zone that
  darkens while a file is over it, capture protocol as the subtitle.
- **Scoring** — node `104:1094`. Four exported figures on a staggered pulse
  (`animate-teep-kick`, 160 ms apart), with the design's empty 35px spacer
  beneath.

**The SSE stages are no longer displayed.** The design's loading block has no
caption or bar, so `POST /api/analyze` still streams `progress` events and the
client still consumes them — passing a no-op callback, which keeps the stream
drained rather than buffering — but nothing is drawn from them. The cost: the
animation loops indefinitely, so a run that has stalled looks exactly like a run
that is merely slow. The stage data is there the moment a slot for it exists.

### 10.3 Deviations from the design, and why

The design does not cover everything the rest of this document requires. Where it
is silent, these decisions stand:

| Thing | Decision |
|---|---|
| `timing_score` | **Computed but not displayed.** The design has no slot for it. §6.4 still forbids merging it into the overall score, and it is in the API. A rep can currently score well while badly mistimed with nothing on screen saying so — the least-bad place to restore it is beside the overall score. |
| Diagnostics (§12) | `detection_rate` / `mean_pelvis_tilt_conf` are in the API but no longer shown. The low-detection **warning** still appears. |
| Warnings banner | Conditional, so it shifts the layout when it fires. Not in the design, and the improvements panel that could have absorbed it is gone, so it now has nowhere stable to live. |
| SSE progress | Streamed but not displayed — see the loading state above. |
| Error states | Still on shadcn defaults; the design draws the upload and loading states but not the failures. |
| Export scope | The athlete's clip only, not the side-by-side, and no reference skeleton. Runs at real time because the recorder is paced by wall clock. |
| Playhead | The design's asset is a static line; the playhead must move and span a variable height, so its geometry is reproduced parametrically. |
| Apex tick (§10) | Centred **on** the band. Above-the-band placement clipped the reference star off the top of the SVG, and a 13px band has no room above it. |
| Skeleton toggle off-state | The design draws one state. Chrome stays constant and the glyph fades — the white-stroked icon would be invisible on a light variant. |
| Sparklines | The design's metric cards have none; kept because nothing else shows the shape of the movement. They cost ~19px per card over the design's height. |

---

## 11. Packaging

Target: **download, one command, browser opens.** The user is not assumed to be a developer.

- **`uv`** for bootstrap — installs the pinned Python and dependencies without the user
  managing environments. Avoid Docker (video-decode friction, heavy dependency).
- **Pin Python and mediapipe versions hard.** MediaPipe is unusually particular about both
  and is the most likely thing to break on an unfamiliar laptop.
- **Model file.** If `backend == "legacy"`, the model ships inside the mediapipe wheel and
  there is nothing to bundle. If `backend == "tasks"`, bundle `pose_landmarker_heavy.task`
  (~30 MB) in `assets/`. Either way, never download at runtime — the app must work fully
  offline.
- **Bundle `video_A.mp4` and `video_A_metrics.csv`.** Both are required.
- `run.py` starts uvicorn on an open port and opens the default browser.

---

## 12. Guards and error states

Each needs a clear UI state, not a stack trace.

| Condition | Detection | Behavior |
|---|---|---|
| Wrong kicking leg | B's `kick_side` ≠ A's `kick_side` | **Refuse.** No mirroring exists; comparing sign-flipped columns silently produces a confidently wrong score. |
| No kick detected | `summary.phases.warning` present (hip flexion range < 8°) | Refuse with "no teep detected in this video." |
| Low detection rate | `detection_rate < 0.85` | Score anyway; show a warning banner. |
| Phase too short | < `MIN_PHASE_FRAMES` (4) in B, except impact | Drop phase, redistribute weight, note it in the UI. |
| Unreadable video | decode failure | Clear error in the modal. |

`detection_rate` and `mean_pelvis_tilt_conf` are returned in `diagnostics` on every
result. They were shown in the results header until the redesign removed it, and are
currently **not surfaced anywhere in the UI** — see §10.3. When a score looks
inexplicable these two numbers usually explain it immediately, so they are worth a
home again. The low-detection warning banner still fires.

---

## 13. Build order

1. **Backend skeleton** — FastAPI, static serving, `/api/reference` loading the CSV
   (including the stray-title-line fix). Verify the reference parses.
2. **Extraction path** — `POST /api/analyze` runs `teep_extract.py` on an upload and returns
   raw metrics. No scoring yet. Verify end-to-end on a real clip.
3. **DTW** (§6) — active-window, apex-anchored. Output the warp path and eyeball it.
4. **Scoring** (§7) — all three phase methods. **Validate with the self-test in §14 before
   building any UI.**
5. **Feedback** (§8).
6. **Frontend shell** — layout, two videos, upload modal, SSE progress.
7. **Timelines + warp ribbon + apex tick.**
8. **Warp-synced playback + frame stepping.**
9. **Packaging** (§11).

---

## 14. Acceptance criteria

**The self-comparison test is the single most important check in this document.**

> Score `video_A` against `video_A`. The overall score **must be exactly 100.0** and the
> timing score **must be exactly 100.0**. The warp path must be the identity diagonal, and
> the feedback list must be empty.

Any deviation means a bug in alignment, tolerance handling, or aggregation. Do not proceed
past step 4 of the build order until this passes.

This holds for any value of the strictness dial or the run penalty — a zero deviation
always scores full credit, and the run penalty only ever charges for non-diagonal
steps — so retuning either can never mask a bug here.

Additional:

- A clip trimmed to start mid-chamber drops `ready` and redistributes its weight; the run
  does not crash.
- A right-legged teep is refused with a clear message.
- Uploading a second video replaces B without a page reload and without stale metrics
  persisting in the analytics strip.
- Frame-stepping through the full clip keeps A and B in corresponding phases at every step.
- The app runs with networking disabled.
- **The layout does not shift when a video is uploaded** — every block except the
  video row holds its height (§10).

All of the above are covered by `tests/` — 29 tests, all passing, run with
`uv run pytest`.
The suite builds its own fixtures from `video_A` (trimmed and mirrored clips), so it
needs no assets beyond the ones in `assets/`.
