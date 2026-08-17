"""
landmarks.py — per-frame pose landmarks for the on-video skeleton overlay.

What gets drawn is `image_s`: the gap-filled, Butterworth-smoothed image-space
landmark array that `compute_metrics` consumed. Drawing that array rather than
re-running a detector for display means the skeleton on screen is literally the
pose the scores were computed from — if a joint looks wrong in the overlay, the
metric derived from it is wrong in the same way. A second, cosmetically nicer
pose estimate would hide exactly the failures the overlay exists to expose.

Serialised flat — `[x0, y0, v0, x1, y1, v1, ...]` per frame, 33 landmarks —
rather than as nested objects: this is per-frame data for a whole clip, and key
names would otherwise outweigh the numbers several times over.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np

# BlazePose 33-landmark model, matching teep_extract's own `L` index map.
N_LM = 33
STRIDE = 3  # x, y, visibility

# Image coords are normalised 0-1, so 4 dp is ~1/10000 of frame width — well
# below the pose model's own error, and it keeps the payload small.
_XY_DP = 4
_VIS_DP = 3


def pack(image_s: np.ndarray, vis: np.ndarray) -> list[list[float]]:
    """
    (N, 33, 3) normalised image landmarks + (N, 33) visibility -> per-frame rows.

    Row *i* corresponds to row *i* of the metrics DataFrame, not to decoded frame
    *i*: variable-frame-rate input is resampled upstream. Both arrays come out of
    the same resampled track, so they stay in step by construction.
    """
    if image_s.ndim != 3 or image_s.shape[1] != N_LM:
        raise ValueError(f"expected (N, {N_LM}, 3) landmarks, got {image_s.shape}")

    n = int(image_s.shape[0])
    xy = np.round(np.nan_to_num(image_s[:, :, :2].astype(float), nan=0.0), _XY_DP)
    v = np.round(np.nan_to_num(np.asarray(vis, dtype=float), nan=0.0), _VIS_DP)

    rows: list[list[float]] = []
    for i in range(n):
        row: list[float] = []
        for j in range(N_LM):
            row.append(float(xy[i, j, 0]))
            row.append(float(xy[i, j, 1]))
            row.append(float(v[i, j]))
        rows.append(row)
    return rows


def write(path: Path, frames: list[list[float]], summary: dict) -> None:
    """Persist the reference clip's landmarks beside its metrics CSV."""
    path.write_text(
        json.dumps(
            {
                "video": summary.get("video"),
                "frames": len(frames),
                "fps": summary.get("fps"),
                "landmark_count": N_LM,
                "stride": STRIDE,
                "landmarks": frames,
            }
        )
        + "\n"
    )


def load(path: Path, expect_frames: int) -> tuple[Optional[list], Optional[str]]:
    """
    Load reference landmarks, returning `(landmarks, warning)`.

    Never raises. The overlay is a diagnostic aid, not part of scoring, so a
    missing or unreadable file degrades to "no overlay" rather than taking the
    whole app down with it.

    A frame-count mismatch is the case that actually matters: it means video_A
    was regenerated without rebuilding the landmarks, and drawing them anyway
    would paint a stale skeleton onto the wrong frames — a wrong overlay that
    still looks plausible is worse than no overlay.
    """
    if not path.exists():
        return None, (
            f"{path.name} is missing, so the reference skeleton overlay is unavailable. "
            "Build it with `uv run python -m backend.landmarks`."
        )
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"{path.name} could not be read ({exc}); skeleton overlay disabled."

    frames = payload.get("landmarks")
    if not isinstance(frames, list) or not frames:
        return None, f"{path.name} contains no landmark rows; skeleton overlay disabled."

    if len(frames) != expect_frames:
        return None, (
            f"{path.name} has {len(frames)} frames but the reference metrics have "
            f"{expect_frames}. The landmarks are stale — rebuild them with "
            "`uv run python -m backend.landmarks`."
        )
    return frames, None


def _main() -> None:
    """Regenerate assets/video_A_landmarks.json from the reference video."""
    from . import config
    from .pipeline import run_extraction

    _, summary, frames = run_extraction(config.REFERENCE_VIDEO, stem="video_A")
    write(config.REFERENCE_LANDMARKS, frames, summary)
    print(f"Wrote {config.REFERENCE_LANDMARKS} ({len(frames)} frames)")


if __name__ == "__main__":
    _main()
