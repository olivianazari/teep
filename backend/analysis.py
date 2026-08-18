"""
analysis.py — ties alignment, scoring and feedback into the §9 result object.

The API and the self-comparison gate both run through here, so the thing the
test certifies is the same thing the server serves.
"""

from __future__ import annotations

import pandas as pd

from . import config
from .align import align, timing_score
from .feedback import build_feedback
from .reference import Reference
from .scoring import Scorer


class RefusalError(RuntimeError):
    """A guard condition that makes scoring meaningless (spec §12)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def analyze(ref: Reference, df_b: pd.DataFrame, summary_b: dict) -> dict:
    phase_notes = summary_b.get("phases") or {}

    # --- guards (§12) -------------------------------------------------------
    # No kick: segment_phases sets this when hip flexion range < 8 deg, having
    # labelled every frame `ready`. There is nothing to align or score.
    if phase_notes.get("warning"):
        raise RefusalError(
            "no_kick_detected",
            "No teep detected in this video. The hip barely flexes, so there is no "
            "kick to compare against the reference.",
        )

    # Wrong leg: no mirroring exists anywhere in this app by design. Comparing
    # sign-flipped columns would silently produce a confidently wrong score,
    # which is worse than refusing.
    side_b = str(summary_b.get("kick_side", "")).lower()
    if side_b and side_b != ref.kick_side.lower():
        raise RefusalError(
            "kick_side_mismatch",
            f"This video shows a {side_b}-legged teep, but the reference is "
            f"{ref.kick_side}-legged. Upload a {ref.kick_side}-legged teep — the app "
            "does not mirror sides.",
        )

    fps_b = float(summary_b.get("fps") or ref.fps)

    alignment = align(ref, df_b, summary_b)
    scorer = Scorer(ref, df_b, summary_b, alignment)
    scored = scorer.run()
    timing, timing_phases = timing_score(alignment, ref, df_b, fps_b)

    fb = build_feedback(scored["phases"], ref.tolerances)

    # --- diagnostics --------------------------------------------------------
    det = float(summary_b.get("detection_rate", 1.0))
    warnings: list[str] = []
    if det < config.MIN_DETECTION_RATE:
        warnings.append(
            f"Pose was detected in only {det:.0%} of frames (below "
            f"{config.MIN_DETECTION_RATE:.0%}). The score is still shown, but treat it "
            "with caution — check lighting and that the whole body stays in frame."
        )
    for d in scored["dropped_phases"]:
        warnings.append(
            f"Phase '{d['name']}' had {d['frames']} frame(s), fewer than "
            f"{config.MIN_PHASE_FRAMES}, so it was dropped and its weight redistributed."
        )

    return {
        "overall_score": round(scored["overall"], 1),
        # The two halves of the overall, so the UI can say why a score is low
        # rather than only that it is.
        "shape_score": round(scored["shape_score"], 1),
        "rom_score": round(scored["rom"]["score"], 1),
        "rom": scored["rom"]["metrics"],
        "timing_score": round(timing, 1),
        "kick_side": side_b or ref.kick_side,
        "fps": round(fps_b, 3),
        "frame_count": int(len(df_b)),
        "apex_frame": int(alignment.b_apex),
        "phases": [
            {
                "name": p["name"],
                "start": int(p["start"]),
                "end": int(p["end"]),
                "frames": int(p["frames"]),
                "score": round(p["score"], 1),
                "weight": round(p["weight"], 4),
                "method": p["method"],
                "metric_scores": {k: round(v, 1) for k, v in p["metric_scores"].items()},
            }
            for p in scored["phases"]
        ],
        "dropped_phases": scored["dropped_phases"],
        "timing_phases": [
            {**t, "score": round(t["score"], 1)} for t in timing_phases
        ],
        "frames": scorer.frame_rows(),
        "warp_path": alignment.as_json(),
        "feedback": fb,
        "diagnostics": {
            "detection_rate": round(det, 4),
            "mean_pelvis_tilt_conf": round(
                float((summary_b.get("peaks") or {}).get("mean_pelvis_tilt_conf", 0.0)), 3
            ),
            "warnings": warnings,
        },
        "extraction": dict(config.EXTRACTION),
        "reference": {
            "frame_count": ref.frame_count,
            "fps": ref.fps,
            "apex_frame": ref.apex,
            "kick_side": ref.kick_side,
            "active_start": ref.active_start,
            "active_end": ref.active_end,
        },
    }
