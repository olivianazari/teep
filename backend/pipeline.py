"""
pipeline.py — runs teep_extract.py's own functions over an uploaded video.

teep_extract is used as a module, unmodified. This mirrors its `main()`
sequence exactly so that video_B is measured by the same instrument as video_A;
reimplementing any of the biomechanics here would reintroduce precisely the
systematic bias the whole design is trying to avoid.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import config
from . import landmarks as lm
from . import teep_extract as tx

ProgressFn = Callable[[str, int], None]

_FRAME_TICK = re.compile(r"\.\.\.(\d+) frames")


class ExtractionError(RuntimeError):
    """Video could not be decoded or produced no usable pose data."""


class _StderrTee(io.TextIOBase):
    """
    Forwards teep_extract's stderr through, parsing its `...N frames` ticks.

    teep_extract has no progress callback and must not be modified, so its own
    console output is the only progress signal available. Reading it here keeps
    the extraction stage from looking frozen for 10-30 s. If the message format
    ever changes we simply stop getting intermediate ticks; nothing breaks.
    """

    def __init__(self, inner, total: int, on_tick: Callable[[int], None]):
        self._inner, self._total, self._on_tick = inner, total, on_tick

    def write(self, s: str) -> int:
        m = _FRAME_TICK.search(s)
        if m and self._total > 0:
            self._on_tick(min(int(m.group(1)), self._total))
        return self._inner.write(s)

    def flush(self) -> None:
        self._inner.flush()


def _probe_frame_count(path: Path) -> int:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ExtractionError(
            "Could not open the video file. It may be corrupt or in an unsupported format."
        )
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return n


def run_extraction(
    video_path: Path,
    progress: Optional[ProgressFn] = None,
    stem: str = "video_B",
) -> tuple[pd.DataFrame, dict, list]:
    """
    Full extraction for one video.

    Returns (metrics DataFrame, summary dict, per-frame landmark rows).

    The DataFrame is identical in shape, column order and rounding to what
    teep_extract writes to CSV, so the reference (loaded from disk) and an
    upload (held in memory) are the same kind of object.

    The landmark rows are the smoothed image-space array this function already
    builds on the way to the metrics; see landmarks.py for the format.
    """
    def emit(stage: str, pct: int) -> None:
        if progress:
            progress(stage, pct)

    ex = config.EXTRACTION
    total = _probe_frame_count(video_path)
    emit("extracting", 2)

    # --- [1/4] landmarks ----------------------------------------------------
    def tick(n: int) -> None:
        emit("extracting", 2 + int(32 * n / max(total, 1)))

    tee = _StderrTee(sys.stderr, total, tick)
    try:
        with contextlib.redirect_stderr(tee):
            if ex["backend"] == "legacy":
                trk = tx.extract_landmarks_legacy(
                    str(video_path),
                    det_conf=ex["det_conf"],
                    track_conf=ex["track_conf"],
                    fps_override=None,
                    person=ex["person"],
                    complexity=ex["complexity"],
                )
            else:
                model = config.ASSETS / "pose_landmarker_heavy.task"
                if not model.exists():
                    raise ExtractionError(
                        f"backend='tasks' requires {model.name} in assets/. "
                        "Bundle it or switch EXTRACTION['backend'] to 'legacy'."
                    )
                trk = tx.extract_landmarks(
                    str(video_path), str(model),
                    det_conf=ex["det_conf"], track_conf=ex["track_conf"],
                    fps_override=None,
                )
    except SystemExit as exc:
        # teep_extract raises SystemExit on decode failure; a web request must
        # not take the server down with it.
        raise ExtractionError(str(exc) or "Could not decode the video.") from exc

    n_raw = trk.world.shape[0]
    if n_raw == 0:
        raise ExtractionError("No frames could be decoded from the video.")

    # Variable-frame-rate input is resampled onto an even timebase, exactly as
    # teep_extract's main() does. Butterworth filtering and central-difference
    # derivatives both assume uniform sampling.
    dts = np.diff(trk.t_s)
    dts = dts[dts > 1e-9]
    if len(dts) and (dts.max() - dts.min()) > 0.2 * np.median(dts):
        trk = tx.resample_uniform(trk)

    n = trk.world.shape[0]
    det_rate = float(trk.detected.mean())
    emit("extracting", 34)

    # --- [2/4] smoothing ----------------------------------------------------
    emit("smoothing", 40)
    world_s = tx.fill_and_smooth(trk.world, trk.fps, ex["cutoff"])
    image_s = tx.fill_and_smooth(trk.image, trk.fps, ex["cutoff"])
    if np.isnan(world_s).all():
        raise ExtractionError("No pose was detected anywhere in the video.")
    emit("smoothing", 60)

    kick = ex["leg"] if ex["leg"] != "auto" else tx.detect_kick_side(world_s)

    # --- [3/4] metrics ------------------------------------------------------
    df, refs = tx.compute_metrics(world_s, image_s, trk.vis, trk.fps, kick)

    # --- [4/4] phases -------------------------------------------------------
    emit("segmenting", 68)
    phase, pct, notes = tx.segment_phases(df, trk.fps)
    emit("segmenting", 75)

    df.insert(0, "frame", np.arange(n))
    df.insert(1, "time_s", np.round(trk.t_s, 5))
    df.insert(2, "detected", trk.detected.astype(int))
    df.insert(3, "vis_min", trk.vis.min(axis=1))
    df.insert(4, "phase", phase)
    df.insert(5, "phase_pct", np.round(pct, 2))

    num = df.select_dtypes(include=[np.number]).columns
    df[num] = df[num].round(5)

    summary = {
        "video": video_path.name,
        "frames": n,
        "fps": round(trk.fps, 3),
        "duration_s": round(n / trk.fps, 3),
        "resolution": [trk.width, trk.height],
        "detection_rate": round(det_rate, 4),
        "kick_side": kick,
        "smoothing_cutoff_hz": ex["cutoff"],
        "scale_refs_m": {k: round(v, 4) if isinstance(v, float) else v
                         for k, v in refs.items()},
        "phases": notes,
        "peaks": {
            "max_kick_ankle_speed_torso_per_s": round(float(df["kick_ankle_speed"].max()), 3),
            "max_com_speed_torso_per_s": round(float(df["com_speed"].max()), 3),
            "max_trunk_lean_sagittal_deg": round(float(df["trunk_lean_sagittal"].max()), 2),
            "min_sup_knee_angle_deg": round(float(df["sup_knee_angle"].min()), 2),
            "mean_pelvis_tilt_conf": round(float(df["pelvis_tilt_conf"].mean()), 3),
        },
        # Every result carries the parameters that produced it (spec §4.3).
        "extraction": dict(ex),
    }
    # Landmarks come from image_s — the very array compute_metrics consumed —
    # so the overlay shows the pose the scores were taken from.
    return df, summary, lm.pack(image_s, trk.vis)
