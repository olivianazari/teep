"""
scoring.py — the scoring engine (spec §7).

Strictness lives in the tolerance band widths, never in the shape of the curve:
the per-metric response is linear between full credit and zero credit.

Three different phase-scoring methods are used, and applying one of them
uniformly would be wrong in a different way each time:
  * chamber/extension/retraction/recovery — mean of warp-aligned per-frame composites
  * impact                                — an event, scored over apex +- 2 frames
  * ready/reset                           — static, median against median
"""

from __future__ import annotations

import math

from typing import Optional

import numpy as np
import pandas as pd

from . import config
from .align import Alignment
from .reference import Reference

# Metric weights, normalised so a composite of four perfect metrics is exactly
# 100.0 rather than 99.99999999999999.
_MW = {k: m["weight"] for k, m in config.METRICS.items()}
_MW_TOTAL = sum(_MW.values())
METRIC_W = {k: w / _MW_TOTAL for k, w in _MW.items()}


def band_score(delta: float, full: float, zero: float) -> float:
    """Linear tolerance band: 100 inside `full`, 0 beyond `zero`."""
    if not np.isfinite(delta):
        return 0.0
    delta = abs(delta)
    if delta <= full:
        return 100.0
    if delta >= zero:
        return 0.0
    return 100.0 * (zero - delta) / (zero - full)


def power_mean(values: list[float], weights: list[float], power: float) -> float:
    """
    Weighted power mean, on a 0-100 scale.

    power = 1 is the arithmetic mean, where every fault dilutes into the
    average. Below 1 the worst value dominates, which is how a coach grades a
    rep — by its worst fault, not its average correctness. power = 0 is the
    geometric mean, taken as the limit.

    All-100 inputs return exactly 100 at any power, which is what keeps the §14
    self-comparison gate intact.
    """
    total_w = sum(weights)
    if total_w <= 0:
        return 0.0

    # A single zero means zero for power <= 0, which is the intended behaviour:
    # nothing rescues a rep that scored nothing on a weighted component. Clamp
    # off exact zeros so the maths stays finite.
    vals = [max(min(v, 100.0), 1e-9) for v in values]

    if abs(power) < 1e-9:
        acc = sum(w * math.log(v) for v, w in zip(vals, weights)) / total_w
        return math.exp(acc)
    acc = sum(w * (v ** power) for v, w in zip(vals, weights)) / total_w
    return acc ** (1.0 / power)


def _composite(metric_scores: dict) -> float:
    keys = list(config.METRICS)
    return power_mean(
        [metric_scores[k] for k in keys],
        [METRIC_W[k] for k in keys],
        config.AGGREGATION_POWER,
    )


class Scorer:
    def __init__(self, ref: Reference, df_b: pd.DataFrame, summary_b: dict,
                 alignment: Alignment):
        self.ref = ref
        self.df_b = df_b
        self.summary_b = summary_b
        self.al = alignment
        self.fps_b = float(summary_b.get("fps") or ref.fps)

        self.a_vals = {k: ref.metric_series(k) for k in config.METRICS}
        self.b_vals = {
            k: df_b[m["column"]].to_numpy(dtype=float) for k, m in config.METRICS.items()
        }
        self.a_phase = ref.df["phase"].astype(str).to_numpy()
        self.b_phase = df_b["phase"].astype(str).to_numpy()

        # Median of each metric over each phase in A — the comparison target for
        # the statically scored phases, and the per-frame reference shown in the
        # UI for frames that lie outside the warp window.
        self.a_phase_median: dict[str, dict[str, float]] = {}
        for name in config.PHASES:
            idx = np.flatnonzero(self.a_phase == name)
            if idx.size:
                self.a_phase_median[name] = {
                    k: float(np.nanmedian(self.a_vals[k][idx])) for k in config.METRICS
                }

    # -- per frame ---------------------------------------------------------
    def _ref_value(self, key: str, b_frame: int) -> Optional[float]:
        """
        A's value corresponding to a frame of B.

        Inside the active window this comes through the warp path; DTW is
        many-to-many, so when several A frames map onto one B frame their mean
        is used. Outside the window there is no frame correspondence at all, so
        the phase median stands in — which is exactly the number those phases
        are scored against anyway (§7.5).
        """
        matched = self.al.b_to_a.get(b_frame)
        if matched:
            return float(np.mean([self.a_vals[key][a] for a in matched]))
        med = self.a_phase_median.get(self.b_phase[b_frame])
        return None if med is None else med[key]

    def frame_rows(self) -> list[dict]:
        rows = []
        times = self.df_b["time_s"].to_numpy(dtype=float)
        for b in range(len(self.df_b)):
            metrics, scores = {}, {}
            for key in config.METRICS:
                val = float(self.b_vals[key][b])
                rv = self._ref_value(key, b)
                tol = self.ref.tolerances[key]
                if rv is None:
                    metrics[key] = {"value": round(val, 2), "ref_value": None,
                                    "delta": None, "score": None}
                    scores[key] = 0.0
                else:
                    delta = val - rv
                    s = band_score(delta, tol["full"], tol["zero"])
                    scores[key] = s
                    metrics[key] = {
                        "value": round(val, 2),
                        "ref_value": round(rv, 2),
                        "delta": round(delta, 2),
                        "score": round(s, 1),
                    }
            rows.append({
                "frame": b,
                "time_s": round(float(times[b]), 4),
                "phase": self.b_phase[b],
                "composite": round(_composite(scores), 1),
                "metrics": metrics,
            })
        return rows

    def _frame_metric_scores(self, b: int) -> tuple[dict, dict]:
        """Per-metric (score, signed delta) for one frame of B."""
        scores, deltas = {}, {}
        for key in config.METRICS:
            rv = self._ref_value(key, b)
            if rv is None:
                scores[key], deltas[key] = 0.0, 0.0
            else:
                tol = self.ref.tolerances[key]
                deltas[key] = float(self.b_vals[key][b]) - rv
                scores[key] = band_score(deltas[key], tol["full"], tol["zero"])
        return scores, deltas

    # -- phase methods -----------------------------------------------------
    def _score_warped(self, name: str) -> Optional[dict]:
        """Mean of per-frame composites over the phase's frames in B."""
        idx = np.flatnonzero(self.b_phase == name)
        if idx.size == 0:
            return None
        per_metric = {k: [] for k in config.METRICS}
        per_delta = {k: [] for k in config.METRICS}
        composites = []
        for b in idx.tolist():
            ms, ds = self._frame_metric_scores(b)
            composites.append(_composite(ms))
            for k in config.METRICS:
                per_metric[k].append(ms[k])
                per_delta[k].append(ds[k])
        return {
            "name": name,
            "start": int(idx[0]),
            "end": int(idx[-1]),
            "frames": int(idx.size),
            "score": float(np.mean(composites)),
            "metric_scores": {k: float(np.mean(v)) for k, v in per_metric.items()},
            "metric_deltas": {k: float(np.mean(v)) for k, v in per_delta.items()},
            # The single frame of B where this metric is furthest off. The mean
            # deviation says how bad a phase is; this says where to look.
            "metric_worst_frame": {
                k: int(idx[int(np.argmax(np.abs(v)))]) for k, v in per_delta.items()
            },
            "method": "warped",
        }

    def _score_impact(self) -> Optional[dict]:
        """
        Impact is an event, not a phase (§7.4).

        segment_phases builds impact as +-40 ms around apex, which at 30 fps is
        2-3 frames in any video it processes. A phase score built on 2 samples
        swings wildly between near-identical uploads, so it is scored here over
        apex +- IMPACT_HALF_WINDOW frames using each video's own apex, giving an
        identical sample count every time. Frames are paired by their offset
        from apex rather than through the warp path, since both windows are
        already anchored on the same physical event.
        """
        h = config.IMPACT_HALF_WINDOW
        a_apex, b_apex = self.ref.apex, self.al.b_apex
        offsets = [
            o for o in range(-h, h + 1)
            if 0 <= a_apex + o < len(self.ref.df) and 0 <= b_apex + o < len(self.df_b)
        ]
        if not offsets:
            return None

        per_metric = {k: [] for k in config.METRICS}
        per_delta = {k: [] for k in config.METRICS}
        composites = []
        for o in offsets:
            ms = {}
            for key in config.METRICS:
                tol = self.ref.tolerances[key]
                delta = float(self.b_vals[key][b_apex + o]) - float(self.a_vals[key][a_apex + o])
                ms[key] = band_score(delta, tol["full"], tol["zero"])
                per_metric[key].append(ms[key])
                per_delta[key].append(delta)
            composites.append(_composite(ms))

        return {
            "name": "impact",
            "start": b_apex + offsets[0],
            "end": b_apex + offsets[-1],
            "frames": len(offsets),
            "score": float(np.mean(composites)),
            "metric_scores": {k: float(np.mean(v)) for k, v in per_metric.items()},
            "metric_deltas": {k: float(np.mean(v)) for k, v in per_delta.items()},
            "metric_worst_frame": {
                k: int(b_apex + offsets[int(np.argmax(np.abs(v)))])
                for k, v in per_delta.items()
            },
            "method": "event",
        }

    def _score_static(self, name: str) -> Optional[dict]:
        """
        ready/reset sit outside the warp window, so there is no frame
        correspondence and nothing to compare frame-to-frame. Compare single
        numbers instead: median against median (§7.5).
        """
        b_idx = np.flatnonzero(self.b_phase == name)
        a_med = self.a_phase_median.get(name)
        if b_idx.size == 0 or a_med is None:
            return None

        # Tighter bands than the moving phases use. The derived tolerances are
        # sized to the kick's range, which is enormous next to a held stance —
        # applied unscaled they returned 100.0 here on essentially every rep.
        # See STATIC_TOLERANCE_SCALE in config.py.
        scale = config.STATIC_TOLERANCE_SCALE

        ms, ds, worst = {}, {}, {}
        for key in config.METRICS:
            tol = self.ref.tolerances[key]
            ds[key] = float(np.nanmedian(self.b_vals[key][b_idx])) - a_med[key]
            ms[key] = band_score(ds[key], tol["full"] * scale, tol["zero"] * scale)
            # Scored on medians, but for "show me" purposes point at the frame
            # that sits furthest from the reference's median.
            offsets = np.abs(self.b_vals[key][b_idx] - a_med[key])
            worst[key] = int(b_idx[int(np.nanargmax(offsets))])

        return {
            "name": name,
            "start": int(b_idx[0]),
            "end": int(b_idx[-1]),
            "frames": int(b_idx.size),
            "score": _composite(ms),
            "metric_scores": ms,
            "metric_deltas": ds,
            "metric_worst_frame": worst,
            "method": "static",
        }

    def _score_rom(self) -> dict:
        """
        Amplitude, per metric, over the active window.

        Per-frame comparison is blind to this: a kick travelling through half the
        reference's trunk rotation, at the right times and in the right order,
        reads as slightly-off-everywhere rather than as the different technique
        it is. Penalised in both directions — overshooting the reference is not
        better form, it is a different movement.
        """
        a_idx = self.ref.active_index
        b_mask = np.isin(self.b_phase, config.ACTIVE_PHASES)
        b_idx = np.flatnonzero(b_mask)

        metrics, ratios = {}, {}
        for key, meta in config.METRICS.items():
            a = self.ref.df[meta["column"]].to_numpy(dtype=float)[a_idx]
            b = self.b_vals[key][b_idx]
            a_rng = float(np.nanmax(a) - np.nanmin(a)) if a.size else 0.0
            b_rng = float(np.nanmax(b) - np.nanmin(b)) if b.size else 0.0
            if a_rng <= 1e-6:
                continue
            ratio = b_rng / a_rng
            metrics[key] = band_score(
                abs(1.0 - ratio), config.ROM_FULL_CREDIT, config.ROM_ZERO_CREDIT
            )
            ratios[key] = {
                "ratio": round(ratio, 3),
                "range_a": round(a_rng, 1),
                "range_b": round(b_rng, 1),
                "score": round(metrics[key], 1),
            }

        return {
            "score": _composite(metrics) if metrics else 100.0,
            "metric_scores": metrics,
            "metrics": ratios,
        }

    # -- aggregate ---------------------------------------------------------
    def run(self) -> dict:
        raw: list[dict] = []
        for name in config.PHASES:
            if name == "impact":
                p = self._score_impact()
            elif name in config.STATIC_PHASES:
                p = self._score_static(name)
            else:
                p = self._score_warped(name)
            if p is not None:
                raw.append(p)

        # Minimum frame floor (§7.6). A clip that starts mid-kick otherwise gets
        # scored on a `ready` stance that was never filmed. Impact is exempt: it
        # is a fixed-width event window by construction.
        kept, dropped = [], []
        for p in raw:
            if p["name"] != "impact" and p["frames"] < config.MIN_PHASE_FRAMES:
                dropped.append({"name": p["name"], "frames": p["frames"]})
            else:
                kept.append(p)
        for name in config.PHASES:
            if not any(p["name"] == name for p in raw):
                dropped.append({"name": name, "frames": 0})

        if not kept:
            raise ValueError("No phase in this video had enough frames to score.")

        # Weight by phase, not by frame: chamber is 4 frames and reset is 27 in
        # the reference, so a flat frame average would let standing still drown
        # out the kick entirely.
        total_w = sum(config.PHASE_WEIGHTS[p["name"]] for p in kept)
        for p in kept:
            p["weight"] = config.PHASE_WEIGHTS[p["name"]] / total_w

        shape = power_mean(
            [p["score"] for p in kept],
            [p["weight"] for p in kept],
            config.AGGREGATION_POWER,
        )

        # Shape and amplitude are combined through the same worst-sensitive mean,
        # so a rep that hits every position but only travels half as far cannot
        # average its way back to a good score.
        rom = self._score_rom()
        overall = power_mean(
            [shape, rom["score"]],
            [1.0 - config.ROM_WEIGHT, config.ROM_WEIGHT],
            config.AGGREGATION_POWER,
        )

        return {
            "phases": kept,
            "dropped_phases": dropped,
            "overall": overall,
            "shape_score": shape,
            "rom": rom,
        }
