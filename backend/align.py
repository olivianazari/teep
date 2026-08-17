"""
align.py — temporal alignment of video_B onto video_A (spec §6).

DTW over the active window only, anchored at each video's apex, plus the timing
score read back off the warping path.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from . import config
from .reference import Reference, build_features


class AlignmentError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Core DTW
# ---------------------------------------------------------------------------
# Step directions, used as the third DP dimension.
_DIAG, _A_ONLY, _B_ONLY = 0, 1, 2


def _dtw_path(
    X: np.ndarray, Y: np.ndarray, run_penalty: float = 0.0
) -> list[tuple[int, int]]:
    """
    DTW with (1,1)/(1,0)/(0,1) steps and a penalty on *consecutive* same-direction
    steps. Returns window-relative pairs.

    The run penalty is what stops one video freezing on a single frame while the
    other plays on. When the clips differ in length the path must take a fixed
    number of non-diagonal steps — that count is pure geometry and no penalty can
    change it — but it is free to take them all in one run or to spread them out.
    Unpenalised DTW has no preference and routinely picks a single long run,
    which on screen is a stalled video. Charging for a repeat of the same
    direction makes the spread-out path strictly cheaper, while still allowing a
    long run where the data genuinely demands one.

    A flat per-step penalty would not do this: with the step count fixed, it adds
    the same total either way. A hard slope constraint (Itakura) would, but it
    caps the compression ratio at 2:1 and a real athlete can easily replant three
    or four times faster than the reference — at which point the path would be
    forced across phase boundaries to stay feasible. This stays soft and is
    always satisfiable.

    Ties break toward the diagonal, which is what makes an identical pair of
    clips produce the identity diagonal the §14 gate requires.
    """
    n, m = len(X), len(Y)
    if n == 0 or m == 0:
        raise AlignmentError("Cannot align an empty window.")

    # Pairwise euclidean distance in the 4-D z-normalised feature space.
    cost = np.sqrt(((X[:, None, :] - Y[None, :, :]) ** 2).sum(axis=2))

    inf = float("inf")
    # D[i][j][d] = best cost reaching cell (i, j) having arrived by direction d.
    D = [[[inf, inf, inf] for _ in range(m + 1)] for _ in range(n + 1)]
    P = [[[0, 0, 0] for _ in range(m + 1)] for _ in range(n + 1)]
    D[0][0] = [0.0, 0.0, 0.0]

    for i in range(1, n + 1):
        ci = cost[i - 1]
        for j in range(1, m + 1):
            c = float(ci[j - 1])
            cell, par = D[i][j], P[i][j]

            prev = D[i - 1][j - 1]
            k = 0 if prev[0] <= prev[1] and prev[0] <= prev[2] else (1 if prev[1] <= prev[2] else 2)
            cell[_DIAG] = prev[k] + c
            par[_DIAG] = k

            # A advances while B holds — the direction that produces a freeze.
            prev = D[i - 1][j]
            best, bk = inf, 0
            for t in (_DIAG, _A_ONLY, _B_ONLY):
                v = prev[t] + (run_penalty if t == _A_ONLY else 0.0)
                if v < best:
                    best, bk = v, t
            cell[_A_ONLY] = best + c
            par[_A_ONLY] = bk

            prev = D[i][j - 1]
            best, bk = inf, 0
            for t in (_DIAG, _A_ONLY, _B_ONLY):
                v = prev[t] + (run_penalty if t == _B_ONLY else 0.0)
                if v < best:
                    best, bk = v, t
            cell[_B_ONLY] = best + c
            par[_B_ONLY] = bk

    path: list[tuple[int, int]] = []
    i, j = n, m
    end = D[n][m]
    d = 0 if end[0] <= end[1] and end[0] <= end[2] else (1 if end[1] <= end[2] else 2)
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        nd = P[i][j][d]
        if d == _DIAG:
            i, j = i - 1, j - 1
        elif d == _A_ONLY:
            i -= 1
        else:
            j -= 1
        d = nd
    path.reverse()
    return path


def _dtw_anchored(
    X: np.ndarray, Y: np.ndarray, ax: int, ay: int, run_penalty: float = 0.0
) -> list[tuple[int, int]]:
    """
    DTW constrained to pass through the apex pair (ax, ay).

    Peak leg extension is a real physical extremum, and forcing the path through
    it stops the alignment drifting in low-contrast stretches where chamber
    frames all look much alike. Run as two independent DTWs either side of the
    apex and concatenate — simpler and more robust than a constrained single
    pass.
    """
    ax = int(np.clip(ax, 0, len(X) - 1))
    ay = int(np.clip(ay, 0, len(Y) - 1))

    head = _dtw_path(X[: ax + 1], Y[: ay + 1], run_penalty)
    tail = _dtw_path(X[ax:], Y[ay:], run_penalty)
    # tail is relative to the apex; shift it back and drop its first pair, which
    # is the apex itself and already present at the end of head.
    shifted = [(i + ax, j + ay) for i, j in tail[1:]]
    return head + shifted


# ---------------------------------------------------------------------------
# Alignment result
# ---------------------------------------------------------------------------
class Alignment:
    """Warp path between A and B, in absolute frame indices."""

    def __init__(self, pairs: list[tuple[int, int]], ref: Reference,
                 b_active: tuple[int, int], b_apex: int):
        self.pairs = pairs
        self.ref = ref
        self.b_active_start, self.b_active_end = b_active
        self.b_apex = b_apex

        # B frame -> the A frames matched to it. DTW is many-to-many, so a slow
        # stretch in B legitimately maps several B frames onto one A frame and
        # vice versa.
        self.b_to_a: dict[int, list[int]] = {}
        self.a_to_b: dict[int, list[int]] = {}
        for a, b in pairs:
            self.b_to_a.setdefault(b, []).append(a)
            self.a_to_b.setdefault(a, []).append(b)

    @property
    def is_identity(self) -> bool:
        """True when the path is the exact diagonal (equal windows, no warping)."""
        return all(
            a - self.pairs[0][0] == b - self.pairs[0][1] for a, b in self.pairs
        ) and len(self.pairs) == len({a for a, _ in self.pairs}) == len(
            {b for _, b in self.pairs}
        )

    def as_json(self) -> list[list[int]]:
        return [[int(a), int(b)] for a, b in self.pairs]


def align(
    ref: Reference,
    df_b: pd.DataFrame,
    summary_b: dict,
) -> Alignment:
    """Align B's active window onto A's, anchored at both apexes."""
    a0, a1 = ref.active_start, ref.active_end

    mask_b = df_b["phase"].astype(str).isin(config.ACTIVE_PHASES).to_numpy()
    idx_b = np.flatnonzero(mask_b)
    if idx_b.size == 0:
        raise AlignmentError(
            "No active kick phases were found in this video, so there is nothing to align."
        )
    b0, b1 = int(idx_b[0]), int(idx_b[-1])

    a_index = np.arange(a0, a1 + 1)
    b_index = np.arange(b0, b1 + 1)

    # Both series are z-normalised with video_A's statistics so they share one
    # normalisation (spec §6.2).
    X = build_features(ref.df, a_index, ref.znorm)
    Y = build_features(df_b, b_index, ref.znorm)

    b_apex = (summary_b.get("phases") or {}).get("apex")
    if b_apex is None:
        raise AlignmentError("Upload summary has no apex frame; cannot anchor alignment.")
    b_apex = int(b_apex)

    pairs_rel = _dtw_anchored(
        X, Y, ax=ref.apex - a0, ay=b_apex - b0, run_penalty=config.WARP_RUN_PENALTY
    )
    pairs = [(i + a0, j + b0) for i, j in pairs_rel]
    return Alignment(pairs, ref, (b0, b1), b_apex)


# ---------------------------------------------------------------------------
# Timing score (spec §6.4)
# ---------------------------------------------------------------------------
def timing_score(
    alignment: Alignment,
    ref: Reference,
    df_b: pd.DataFrame,
    fps_b: float,
) -> tuple[float, list[dict]]:
    """
    Score tempo from the warping path, separately from shape.

    DTW deliberately warps time away, so post-warp deviation cannot see a tempo
    error at all — a slow, sloppy teep warps onto a fast, crisp one and scores
    well on shape. Timing is therefore measured here and reported as its own
    number, never blended into the overall score.

    A phase's duration in B is the span of B frames the path maps onto that
    phase's frames in A, so the measurement comes from the alignment itself
    rather than from B's own phase labels.
    """
    a_phase = ref.df["phase"].astype(str).to_numpy()
    per_phase: list[dict] = []

    for name in config.TIER1_PHASES:
        a_frames = np.flatnonzero(a_phase == name)
        if a_frames.size == 0:
            continue

        b_frames = sorted({b for a in a_frames.tolist() for b in alignment.a_to_b.get(a, [])})
        if not b_frames:
            continue

        dur_a = a_frames.size / ref.fps
        dur_b = len(b_frames) / fps_b
        if dur_a <= 0 or dur_b <= 0:
            continue

        deviation = abs(math.log2(dur_b / dur_a))
        if deviation <= config.TIMING_DEV_FULL:
            score = 100.0
        elif deviation >= config.TIMING_DEV_ZERO:
            score = 0.0
        else:
            score = 100.0 * (config.TIMING_DEV_ZERO - deviation) / (
                config.TIMING_DEV_ZERO - config.TIMING_DEV_FULL
            )

        per_phase.append({
            "name": name,
            "duration_a_s": round(dur_a, 4),
            "duration_b_s": round(dur_b, 4),
            "ratio": round(dur_b / dur_a, 4),
            "deviation": round(deviation, 4),
            "score": score,
            "weight": config.PHASE_WEIGHTS[name],
        })

    if not per_phase:
        return 0.0, []

    # Renormalised over the Tier-1 phases that actually survived.
    total_w = sum(p["weight"] for p in per_phase)
    overall = sum(p["score"] * p["weight"] for p in per_phase) / total_w
    return overall, per_phase
