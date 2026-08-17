"""
reference.py — loads, validates and caches video_A's metrics.

video_A is the reference. It is never scored. Everything downstream (tolerance
bands, z-normalisation statistics, the target phase durations used for timing)
is derived from what this module loads, so a silent problem here becomes a
silent problem in every score the app produces.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import config
from . import landmarks as lmk


class ReferenceError(RuntimeError):
    """Raised when the reference cannot be trusted. Never scored past this."""


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------
# Columns every metrics CSV must carry for the loader to consider a line to be
# the real header row.
_REQUIRED = ("frame", "time_s", "phase")


def _header_row_index(path: Path, scan: int = 8) -> int:
    """
    Locate the real header row.

    A reference CSV may carry a stray title line above the header (the shipped
    one did), so `pd.read_csv` cannot be trusted unaided — it would silently
    adopt the title as column names and every downstream lookup would fail with
    a confusing KeyError. Scan the first few lines for the row that actually
    parses as the header and skip everything above it.
    """
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        for i, fields in enumerate(csv.reader(fh)):
            if i >= scan:
                break
            names = {f.strip() for f in fields}
            if all(col in names for col in _REQUIRED):
                return i
    raise ReferenceError(
        f"{path.name}: no header row containing {_REQUIRED} in the first {scan} lines. "
        "The file is not a teep_extract metrics CSV."
    )


def load_metrics_csv(path: Path) -> pd.DataFrame:
    """Read a teep_extract metrics CSV, tolerating a stray title line."""
    if not path.exists():
        raise ReferenceError(f"Missing metrics CSV: {path}")
    df = pd.read_csv(path, skiprows=_header_row_index(path))
    df.columns = [c.strip() for c in df.columns]

    missing = [m["column"] for m in config.METRICS.values() if m["column"] not in df.columns]
    if missing:
        raise ReferenceError(f"{path.name}: missing scored metric columns {missing}")
    if df.empty:
        raise ReferenceError(f"{path.name}: contains no rows")
    return df


# ---------------------------------------------------------------------------
# Provenance (spec §4.3)
# ---------------------------------------------------------------------------
def write_provenance(path: Path, summary: dict) -> None:
    """Record exactly how the reference CSV was produced, alongside it."""
    payload = {
        "extraction": dict(config.EXTRACTION),
        "video": summary.get("video"),
        "frames": summary.get("frames"),
        "fps": summary.get("fps"),
        "kick_side": summary.get("kick_side"),
        "detection_rate": summary.get("detection_rate"),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def check_provenance(path: Path) -> list[str]:
    """
    Compare the recorded extraction parameters against the live config.

    A mismatch means the reference was measured with a different instrument than
    the one about to measure the upload. That produces plausible-looking wrong
    scores rather than an obvious failure, so it is a refusal, not a warning.
    """
    if not path.exists():
        raise ReferenceError(
            f"Missing {path.name}. The reference CSV carries no record of how it was "
            "made, so it cannot be trusted. Regenerate the reference (see README)."
        )
    try:
        recorded = json.loads(path.read_text()).get("extraction", {})
    except json.JSONDecodeError as exc:
        raise ReferenceError(f"{path.name} is not valid JSON: {exc}") from exc

    disagreements = []
    for key, want in config.EXTRACTION.items():
        got = recorded.get(key, "<absent>")
        if got != want:
            disagreements.append(f"{key}: reference={got!r} config={want!r}")
    return disagreements


# ---------------------------------------------------------------------------
# The reference object
# ---------------------------------------------------------------------------
@dataclass
class Reference:
    df: pd.DataFrame
    summary: dict
    fps: float
    frame_count: int
    kick_side: str
    apex: int
    active_start: int
    active_end: int              # inclusive
    tolerances: dict             # metric key -> {"full": float, "zero": float, "range": float}
    znorm: dict                  # metric key -> {"mean": float, "sd": float}
    phase_bounds: list           # [{"name","start","end"}], end inclusive
    detection_rate: float
    mean_pelvis_tilt_conf: float
    warnings: list = field(default_factory=list)
    # Per-frame skeleton for the overlay, or None if it was never built. Purely
    # diagnostic — nothing in scoring reads it.
    landmarks: Optional[list] = None

    @property
    def active_index(self) -> np.ndarray:
        return np.arange(self.active_start, self.active_end + 1)

    def metric_series(self, key: str) -> np.ndarray:
        return self.df[config.METRICS[key]["column"]].to_numpy(dtype=float)

    def feature_matrix(self, index: np.ndarray) -> np.ndarray:
        """Z-normalised (len(index), 4) feature matrix using video_A's statistics."""
        return build_features(self.df, index, self.znorm)


def build_features(df: pd.DataFrame, index: np.ndarray, znorm: dict) -> np.ndarray:
    """
    Per-frame feature vector: the four scored metrics, z-normalised.

    Both series are normalised with video_A's active-window mean and SD so that
    A and B share one normalisation — normalising each series by its own
    statistics would rescale B's errors away before DTW ever sees them.
    """
    cols = []
    for key, meta in config.METRICS.items():
        v = df[meta["column"]].to_numpy(dtype=float)[index]
        st = znorm[key]
        cols.append((v - st["mean"]) / st["sd"])
    return np.stack(cols, axis=1)


def derive_tolerances(df: pd.DataFrame, index: np.ndarray) -> dict:
    """Tolerance bands from the reference's own active-window range (spec §5)."""
    out = {}
    for key, meta in config.METRICS.items():
        v = df[meta["column"]].to_numpy(dtype=float)[index]
        rng = float(np.nanmax(v) - np.nanmin(v))
        out[key] = {
            "range": rng,
            "full": max(config.FULL_CREDIT_FRAC * rng, config.FULL_CREDIT_FLOOR),
            "zero": max(config.ZERO_CREDIT_FRAC * rng, config.ZERO_CREDIT_FLOOR),
        }
    return out


def derive_znorm(df: pd.DataFrame, index: np.ndarray) -> dict:
    out = {}
    for key, meta in config.METRICS.items():
        v = df[meta["column"]].to_numpy(dtype=float)[index]
        sd = float(np.nanstd(v))
        out[key] = {"mean": float(np.nanmean(v)), "sd": sd if sd > 1e-9 else 1.0}
    return out


def phase_bounds_from(df: pd.DataFrame) -> list:
    """
    Contiguous [start, end] spans (end inclusive) per phase label, in clip order.

    Read off the labels rather than recomputed, so the app and teep_extract can
    never disagree about where a phase starts.
    """
    phases = df["phase"].astype(str).to_numpy()
    bounds, start = [], 0
    for i in range(1, len(phases) + 1):
        if i == len(phases) or phases[i] != phases[start]:
            bounds.append({"name": phases[start], "start": start, "end": i - 1})
            start = i
    return bounds


def active_window(df: pd.DataFrame) -> tuple[int, int]:
    """
    First and last frame of the active window: chamber through recovery.

    DTW runs on this window only. In the reference, most frames are someone
    standing still; including them lets idle frames dominate the cost matrix and
    the warping path will happily align A's `ready` onto B's `reset` because
    both are cheap. Segment first, align second.
    """
    mask = df["phase"].astype(str).isin(config.ACTIVE_PHASES).to_numpy()
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        raise ReferenceError("No active (chamber..recovery) frames present.")
    return int(idx[0]), int(idx[-1])


def build_reference(
    csv_path: Path = config.REFERENCE_CSV,
    summary_path: Path = config.REFERENCE_SUMMARY,
    provenance_path: Optional[Path] = config.REFERENCE_PROVENANCE,
) -> Reference:
    df = load_metrics_csv(csv_path)

    if not summary_path.exists():
        raise ReferenceError(f"Missing {summary_path.name} beside the reference CSV.")
    summary = json.loads(summary_path.read_text())

    if provenance_path is not None:
        disagreements = check_provenance(provenance_path)
        if disagreements:
            raise ReferenceError(
                "Reference was extracted with different parameters than this build uses, "
                "so its numbers are not comparable to an upload's:\n  "
                + "\n  ".join(disagreements)
                + "\nRegenerate the reference (see README) rather than scoring against it."
            )

    a0, a1 = active_window(df)
    index = np.arange(a0, a1 + 1)

    phase_notes = summary.get("phases", {}) or {}
    apex = phase_notes.get("apex")
    if apex is None:
        raise ReferenceError("summary.json has no phases.apex; cannot anchor alignment.")

    warnings: list[str] = []
    det = float(summary.get("detection_rate", 1.0))
    if det < config.MIN_DETECTION_RATE:
        warnings.append(
            f"Reference detection rate is {det:.0%}, below {config.MIN_DETECTION_RATE:.0%}."
        )

    ref_landmarks, lm_warning = lmk.load(config.REFERENCE_LANDMARKS, len(df))
    if lm_warning:
        warnings.append(lm_warning)

    return Reference(
        df=df,
        summary=summary,
        fps=float(summary.get("fps") or 30.0),
        frame_count=len(df),
        kick_side=str(summary.get("kick_side", df["kick_side"].iloc[0])),
        apex=int(apex),
        active_start=a0,
        active_end=a1,
        tolerances=derive_tolerances(df, index),
        znorm=derive_znorm(df, index),
        phase_bounds=phase_bounds_from(df),
        detection_rate=det,
        mean_pelvis_tilt_conf=float(
            (summary.get("peaks") or {}).get("mean_pelvis_tilt_conf", 0.0)
        ),
        warnings=warnings,
        landmarks=ref_landmarks,
    )


@lru_cache(maxsize=1)
def get_reference() -> Reference:
    """Process-wide cached reference. Loaded once at startup."""
    return build_reference()
