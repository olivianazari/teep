"""
The self-comparison gate (spec §14).

Scoring video_A against video_A must return exactly 100.0 overall and exactly
100.0 for timing, with an identity warp path and no feedback. Any deviation
means a bug in alignment, tolerance handling or aggregation — so nothing past
build step 4 is worth trusting until this passes.
"""

from __future__ import annotations

import pytest

from backend import config
from backend.align import align, timing_score
from backend.analysis import analyze
from backend.reference import build_reference


@pytest.fixture(scope="module")
def ref():
    return build_reference()


@pytest.fixture(scope="module")
def result(ref):
    # video_B *is* video_A: same frames, same summary, same instrument.
    return analyze(ref, ref.df, ref.summary)


def test_overall_score_is_exactly_100(result):
    assert result["overall_score"] == 100.0


def test_timing_score_is_exactly_100(result):
    assert result["timing_score"] == 100.0


def test_warp_path_is_the_identity_diagonal(ref):
    alignment = align(ref, ref.df, ref.summary)
    expected = [(f, f) for f in range(ref.active_start, ref.active_end + 1)]
    assert alignment.pairs == expected
    assert alignment.is_identity


def test_feedback_is_empty(result):
    assert result["feedback"] == []


def test_every_scored_phase_is_100(result):
    for phase in result["phases"]:
        assert phase["score"] == 100.0, f"{phase['name']} scored {phase['score']}"


def test_phase_weights_sum_to_one(ref, result):
    # The weights the scorer actually aggregates with must sum to exactly 1.0.
    # The copies in the result object are rounded to 4 dp for display, so they
    # are only checked to within that rounding.
    from backend.align import align as _align
    from backend.scoring import Scorer

    scored = Scorer(ref, ref.df, ref.summary, _align(ref, ref.df, ref.summary)).run()
    assert sum(p["weight"] for p in scored["phases"]) == pytest.approx(1.0, abs=1e-12)
    assert sum(p["weight"] for p in result["phases"]) == pytest.approx(1.0, abs=5e-4)


def test_timing_phases_all_unit_ratio(ref):
    _, phases = timing_score(align(ref, ref.df, ref.summary), ref, ref.df, ref.fps)
    assert phases, "no Tier-1 phases were scored for timing"
    for p in phases:
        assert p["ratio"] == pytest.approx(1.0)
        assert p["deviation"] == pytest.approx(0.0)


def test_no_refusals_or_warnings(result):
    assert result["diagnostics"]["detection_rate"] >= config.MIN_DETECTION_RATE
