"""
config.py — every tunable constant in the application.

Nothing in this file may be duplicated at a call site. If a number matters to
scoring, it lives here so that a single edit changes behaviour everywhere.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
DIST = ROOT / "dist"

REFERENCE_VIDEO = ASSETS / "video_A.mp4"
REFERENCE_CSV = ASSETS / "video_A_metrics.csv"
REFERENCE_SUMMARY = ASSETS / "video_A_summary.json"
REFERENCE_PROVENANCE = ASSETS / "reference_provenance.json"
# Per-frame pose landmarks for the on-video skeleton overlay. Rebuilt with
# `uv run python -m backend.landmarks` whenever video_A changes.
REFERENCE_LANDMARKS = ASSETS / "video_A_landmarks.json"

# ---------------------------------------------------------------------------
# Extraction parameters (spec §4)
# ---------------------------------------------------------------------------
# These are passed EXPLICITLY on every extraction run, for video_A and video_B
# alike. Never rely on teep_extract.py's argparse defaults: a parameter drift
# between the reference and an upload shows up as the athlete's error, which is
# the worst failure mode available to this application.
#
# `legacy` (mp.solutions.pose) carries its model inside the mediapipe wheel, so
# the app is fully offline. `tasks` would require bundling a ~30 MB .task file
# AND produces different numbers — the two backends are not interchangeable.
EXTRACTION = {
    "backend": "legacy",
    "complexity": 1,      # legacy only: 0 lite, 1 full, 2 heavy
    "cutoff": 10.0,       # Butterworth low-pass, Hz
    "det_conf": 0.5,
    "track_conf": 0.5,
    "leg": "auto",
    "person": "any",      # legacy only
}

# ---------------------------------------------------------------------------
# Metrics (spec §5)
# ---------------------------------------------------------------------------
# Four scored metrics. hip_drive and rear_hip_flexion were removed for
# redundancy (r = -0.955 and -0.98 against torso tilt) and must not come back.
#
# `higher_means` is the semantic direction, and it is what keeps feedback
# wording honest:
#   hip flexion  — higher = MORE FLEXED
#   knee angle   — higher = STRAIGHTER (180 deg is fully extended, not folded)
#   torso tilt   — higher = MORE UPRIGHT (teep values are negative, leaning back)
METRICS = {
    "lead_hip_flexion": {
        "column": "kick_hip_flexion",
        "label": "Lead hip flexion",
        "weight": 0.33,
        "higher_means": "more flexed",
    },
    "lead_knee_angle": {
        "column": "kick_knee_angle",
        "label": "Lead knee angle",
        "weight": 0.33,
        "higher_means": "straighter",
    },
    # Key stays `torso_tilt` — it is the metric identity used by the scorer,
    # the API and the tests. Only the display label reads "Body".
    "torso_tilt": {
        "column": "trunk_lean_sagittal",
        "label": "Body tilt",
        "weight": 0.29,
        "higher_means": "more upright",
    },
    # Low signal by design: the support leg is near-straight throughout and its
    # range is close to MediaPipe's own error on the joint. It carries 5% weight
    # and leans on the wide tolerance floor. Do not tighten its band.
    "rear_knee_angle": {
        "column": "sup_knee_angle",
        "label": "Rear knee angle",
        "weight": 0.05,
        "higher_means": "straighter",
    },
}

# ---------------------------------------------------------------------------
# Tolerance derivation (spec §5) — THE STRICTNESS DIAL
# ---------------------------------------------------------------------------
# Tolerances are derived at load time from the reference CSV's own active-window
# range, never hardcoded in degrees. Regenerating video_A (different backend,
# different cutoff, a re-shoot at 60 fps) then updates the bands automatically
# instead of letting stale constants drift out from under the data.
#
#     full_credit = max(FULL_CREDIT_FRAC * range_A, FULL_CREDIT_FLOOR)
#     zero_credit = max(ZERO_CREDIT_FRAC * range_A, ZERO_CREDIT_FLOOR)
#
# These four numbers are the only values to touch to make grading harsher or
# softer.
#
# Loosened from the spec's 0.08 / 0.25 / 4.0 / 12.0, which graded too harshly in
# practice. The floors were doing most of the damage: torso_tilt's active range
# is 46.5 deg, so 0.08 * 46.5 = 3.7 and 0.25 * 46.5 = 11.6 both fell below the
# floors and the floors won. That left torso tolerating +-12 deg while the knee
# tolerated +-24.3 deg, despite torso carrying 29% of the weight — and it put
# 44% of torso frames on a hard zero on a competent real rep.
#
# Raising the floors alongside the fractions keeps the four metrics comparable
# to each other rather than just moving every score up.
FULL_CREDIT_FRAC = 0.13
ZERO_CREDIT_FRAC = 0.44
FULL_CREDIT_FLOOR = 7.0
ZERO_CREDIT_FLOOR = 22.0

# Static phases (ready/reset) get their bands multiplied by this.
#
# Those phases are scored median-against-median, but through tolerances derived
# from the *kick's* active-window range — and a kick's range is enormous next to
# a held stance. lead_hip_flexion tolerates +-11.3 deg of full credit, while two
# people standing in a guard differ by ~2 deg. The result was that ready and
# reset returned exactly 100.0 on essentially every upload, handing out 13.3% of
# the total weight as free marks and floating every score upward: a rep whose
# kick phases averaged 75 still came out at 80.
#
# A deviation that is unremarkable mid-kick is meaningful when nothing is
# moving, so the stance is judged on a tighter band than the movement. Raise
# toward 1.0 to go back to kick-sized tolerances; lower to grade the stance
# harder.
STATIC_TOLERANCE_SCALE = 0.35

# ---------------------------------------------------------------------------
# Phases (spec §7.6)
# ---------------------------------------------------------------------------
PHASES = ["ready", "chamber", "extension", "impact", "retraction", "recovery", "reset"]

# Tier 1 (the kick itself) to Tier 2 (stance + the impact event) at 3:1.
PHASE_WEIGHTS = {
    "chamber": 0.20,
    "extension": 0.20,
    "retraction": 0.20,
    "recovery": 0.20,
    "ready": 0.2 / 3,
    "impact": 0.2 / 3,
    "reset": 0.2 / 3,
}

# Phases inside the DTW active window, scored frame-by-frame against the warp.
ACTIVE_PHASES = ["chamber", "extension", "impact", "retraction", "recovery"]
# Tier-1 phases: the only ones that contribute to the timing score.
TIER1_PHASES = ["chamber", "extension", "retraction", "recovery"]
# Scored statically (median-to-median), because they sit outside the warp and
# have no frame correspondence to compare against.
STATIC_PHASES = ["ready", "reset"]

PHASE_DISPLAY = {
    "ready": "ready",
    "chamber": "chamber",
    "extension": "extension",
    "impact": "impact",
    "retraction": "retraction",
    "recovery": "recovery",
    "reset": "reset",
}

# Impact is an event, not a phase (spec §7.4). segment_phases builds it as
# +-40 ms around apex, which at 30 fps is 2-3 frames — far too few samples for a
# stable mean. The scorer overrides that with apex +- this many frames, using
# each video's own apex, for an identical sample count every time. This is done
# in the scorer only; widening it inside segment_phases would move video_A's
# phase boundaries and force a reference regeneration.
IMPACT_HALF_WINDOW = 2

# A phase in video_B shorter than this is dropped and its weight redistributed
# proportionally across the survivors, so a clip that starts mid-kick is not
# scored on a `ready` stance that was never filmed. Impact is exempt: it is
# always exactly 2*IMPACT_HALF_WINDOW+1 frames by construction.
#
# Lowered from the spec's 5 to 4 so that `chamber` is actually scored. At 30 fps
# a crisp chamber genuinely lasts 4 frames — it is 4 in the reference, and §4's
# own reference facts list `chamber 4 | retraction 4` — so a floor of 5 dropped
# both and moved 40% of the phase weight onto the surviving phases for any rep
# at reference tempo. A 4-frame phase is still only ~130 ms, which is thin, so
# treat chamber and retraction scores as noisier than the longer phases.
MIN_PHASE_FRAMES = 4

# ---------------------------------------------------------------------------
# Timing (spec §6.4)
# ---------------------------------------------------------------------------
# DTW warps time away, so post-warp deviation cannot see tempo error at all: a
# slow sloppy teep warps neatly onto a fast crisp one and scores well on shape.
# Timing is therefore measured from the warping path itself and reported as its
# own number. Never blend it into the overall score — "wrong shape" and "wrong
# tempo" call for completely different coaching.
TIMING_DEV_FULL = 0.20   # |log2(duration ratio)| at or below this scores 100
TIMING_DEV_ZERO = 1.00   # at or above this (2x too slow or fast) scores 0

# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------
# Cost added when the warping path repeats a non-diagonal step. Where the two
# clips differ in length the path must take a fixed number of non-diagonal
# steps, but nothing in plain DTW says whether to take them all at once or to
# spread them out — and taking them at once means one video freezes on a single
# frame while the other plays on. This makes the spread-out path cheaper.
#
# In units of the z-normalised feature distance, where a typical frame-to-frame
# distance is order 1. Raising it spreads compression harder; 0.0 restores plain
# DTW. TUNABLE.
#
# 0.8 is the highest value that costs nothing in fidelity. Above it the path
# starts borrowing frames across phase boundaries to spread holds: phase
# correspondence falls off and phase durations — which the timing score is read
# from — stop reflecting the actual rep. Smoother playback is not worth a
# fabricated timing number.
#
# This only redistributes compression, it cannot remove it. The number of
# non-diagonal steps is fixed by the two clips' lengths, so a phase the athlete
# takes 19 reference frames' worth of time to cover in 5 of their own frames
# will hold for ~4 frames however the path is drawn.
WARP_RUN_PENALTY = 0.8

# ---------------------------------------------------------------------------
# Feedback (spec §8)
# ---------------------------------------------------------------------------
# A metric scoring above this in a phase is not flagged.
#
# 80 rather than the spec's 90 so it lines up with the UI's green band
# (100-80). A card that reads green and a panel that still writes the metric up
# would be telling the athlete two different things about the same number.
# Keep this in step with GRADE_GOOD in frontend/src/lib/grade.ts.
FEEDBACK_SUPPRESS_ABOVE = 80.0
# No cap on how many items are rendered.
#
# There was one (top 5, at most 2 per metric) to keep a written list short. The
# list is gone; feedback now surfaces only as timeline markers, and the timeline
# is meant to show one wherever a metric reads yellow or red. A cap there hides
# a fault the scorer already found, with nothing else on the page to reveal it.
# FEEDBACK_SUPPRESS_ABOVE is the only gate.

# ---------------------------------------------------------------------------
# Guards (spec §12)
# ---------------------------------------------------------------------------
MIN_DETECTION_RATE = 0.85        # below this: still score, but warn
UPLOAD_MAX_BYTES = 512 * 1024 * 1024
ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
# Phase colours are functional rather than decorative (they encode which phase a
# timeline segment is), so they are the one sanctioned exception to staying on
# default shadcn tokens. Defined here so they can be swapped wholesale.
#
# Taken from the Figma design (file F0U2Kxe24jI5YBI1HIWhrr, node 47:775). The
# design's legend names the stance phase "Idle" and shows six bands, so `ready`
# and `reset` — which are the same standing stance either side of the kick —
# share one colour, as the design intends.
PHASE_COLORS = {
    "ready": "#e4dee1",
    "chamber": "#ded0ee",
    "extension": "#c8e9ef",
    "impact": "#fececb",
    "retraction": "#ffdfb3",
    "recovery": "#b1afbd",
    "reset": "#e4dee1",
}

# Each band is drawn with a matching darker outline in the design.
PHASE_BORDER_COLORS = {
    "ready": "#cdc0c6",
    "chamber": "#b9a9cb",
    "extension": "#9ecbd2",
    "impact": "#e6a7a4",
    "retraction": "#f4c78a",
    "recovery": "#706d83",
    "reset": "#cdc0c6",
}

# Display names for the legend. `ready`/`reset` collapse into one entry.
PHASE_LEGEND = [
    ("ready", "Idle"),
    ("chamber", "Chamber"),
    ("extension", "Extension"),
    ("impact", "Impact"),
    ("retraction", "Retraction"),
    ("recovery", "Recovery"),
]
