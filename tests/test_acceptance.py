"""Acceptance criteria from spec §14 and the guard table in §12."""

from __future__ import annotations

import pytest

from backend import config
from backend.align import align
from backend.analysis import RefusalError, analyze
from backend.reference import derive_tolerances
from backend.scoring import band_score


# ---------------------------------------------------------------------------
# §14 — a clip trimmed to start mid-chamber
# ---------------------------------------------------------------------------
def test_mid_chamber_clip_drops_ready_and_survives(ref, extracted_mid_chamber):
    df_b, summary_b, _ = extracted_mid_chamber
    result = analyze(ref, df_b, summary_b)

    scored = {p["name"] for p in result["phases"]}
    assert "ready" not in scored, "a `ready` stance that was never filmed must not be scored"

    # Weight is redistributed across the survivors, not silently lost.
    assert sum(p["weight"] for p in result["phases"]) == pytest.approx(1.0, abs=5e-4)
    assert 0.0 <= result["overall_score"] <= 100.0
    assert any("ready" in w for w in result["diagnostics"]["warnings"])


# ---------------------------------------------------------------------------
# §12 — wrong kicking leg is refused, never mirrored
# ---------------------------------------------------------------------------
def test_right_legged_teep_is_refused(ref, extracted_mirrored):
    df_b, summary_b, _ = extracted_mirrored
    assert summary_b["kick_side"] != ref.kick_side, "fixture is not actually mirrored"

    with pytest.raises(RefusalError) as exc:
        analyze(ref, df_b, summary_b)
    assert exc.value.code == "kick_side_mismatch"
    assert ref.kick_side in exc.value.message


# ---------------------------------------------------------------------------
# §14 — frame stepping keeps A and B in corresponding phases
# ---------------------------------------------------------------------------
def test_warp_path_keeps_phases_corresponding(ref):
    """
    Every pair on the warping path must join frames in the same phase.

    This is the invariant behind "frame-stepping keeps A and B in corresponding
    phases at every step" — the UI's stepping is just a walk along this path.
    """
    alignment = align(ref, ref.df, ref.summary)
    a_phase = ref.df["phase"].astype(str).to_numpy()
    for a, b in alignment.pairs:
        assert a_phase[a] == a_phase[b]


def test_warp_path_is_monotone_and_complete(ref):
    alignment = align(ref, ref.df, ref.summary)
    pairs = alignment.pairs
    for (a0, b0), (a1, b1) in zip(pairs, pairs[1:]):
        assert a1 >= a0 and b1 >= b0, "warping path must never go backwards"
        assert (a1 - a0) <= 1 and (b1 - b0) <= 1, "path must not skip frames"
    # The whole active window is covered on both sides.
    assert pairs[0][0] == ref.active_start
    assert pairs[-1][0] == ref.active_end


# ---------------------------------------------------------------------------
# Warp-path run penalty
# ---------------------------------------------------------------------------
def test_run_penalty_preserves_the_identity_diagonal():
    """
    The penalty only ever charges for non-diagonal steps, so an identical pair
    must still align on the diagonal no matter how high it is set.
    """
    import numpy as np

    from backend.align import _dtw_path

    X = np.array([[0.0], [1.0], [2.0], [3.0], [2.0], [1.0]])
    for penalty in (0.0, 0.8, 5.0):
        assert _dtw_path(X, X, penalty) == [(i, i) for i in range(len(X))]


def test_run_penalty_spreads_compression_rather_than_clumping():
    """
    With the step count fixed by geometry, plain DTW is indifferent between one
    long freeze and several short ones. The penalty must break that tie toward
    spreading, or one video visibly stalls while the other plays on.
    """
    import numpy as np
    from collections import Counter

    # A ramp against the same ramp at half the sample count: four A frames must
    # share each B frame somewhere.
    X = np.linspace(0, 1, 12).reshape(-1, 1)
    Y = np.linspace(0, 1, 4).reshape(-1, 1)

    def longest_hold(penalty: float) -> int:
        return max(Counter(b for _, b in _dtw_path_wrapper(X, Y, penalty)).values())

    from backend.align import _dtw_path as _dtw_path_wrapper

    assert longest_hold(config.WARP_RUN_PENALTY) <= longest_hold(0.0)


# ---------------------------------------------------------------------------
# §5 — tolerances are derived, not hardcoded
# ---------------------------------------------------------------------------
def test_tolerances_follow_the_formula(ref):
    import numpy as np

    idx = ref.active_index
    for key, meta in config.METRICS.items():
        v = ref.df[meta["column"]].to_numpy(dtype=float)[idx]
        rng = float(np.nanmax(v) - np.nanmin(v))
        assert ref.tolerances[key]["full"] == pytest.approx(
            max(config.FULL_CREDIT_FRAC * rng, config.FULL_CREDIT_FLOOR))
        assert ref.tolerances[key]["zero"] == pytest.approx(
            max(config.ZERO_CREDIT_FRAC * rng, config.ZERO_CREDIT_FLOOR))


def test_tolerances_rescale_when_the_reference_changes(ref):
    """A wider reference range must widen the bands, or they would go stale."""
    import numpy as np

    doubled = ref.df.copy()
    for meta in config.METRICS.values():
        col = meta["column"]
        centre = doubled[col].mean()
        doubled[col] = centre + (doubled[col] - centre) * 2.0

    widened = derive_tolerances(doubled, ref.active_index)
    for key in config.METRICS:
        assert widened[key]["range"] > ref.tolerances[key]["range"]
        # Only bands sitting above their floor are expected to move.
        if ref.tolerances[key]["full"] > config.FULL_CREDIT_FLOOR:
            assert widened[key]["full"] > ref.tolerances[key]["full"]


# ---------------------------------------------------------------------------
# §4.3 — provenance
# ---------------------------------------------------------------------------
def test_provenance_mismatch_refuses_to_score(tmp_path):
    """
    A reference measured with different parameters than the upload will be is not
    comparable to it. That must be a refusal, not a warning: the failure is
    silent otherwise, producing plausible-looking wrong scores.
    """
    import json

    from backend.reference import ReferenceError, build_reference

    bad = json.loads(config.REFERENCE_PROVENANCE.read_text())
    bad["extraction"]["backend"] = "tasks"
    path = tmp_path / "reference_provenance.json"
    path.write_text(json.dumps(bad))

    with pytest.raises(ReferenceError, match="different parameters"):
        build_reference(provenance_path=path)


def test_missing_provenance_refuses_to_score(tmp_path):
    from backend.reference import ReferenceError, build_reference

    with pytest.raises(ReferenceError, match="Missing"):
        build_reference(provenance_path=tmp_path / "absent.json")


def test_stray_title_line_above_the_header_is_skipped(tmp_path):
    """The shipped reference CSV had one. pd.read_csv cannot be trusted unaided."""
    from backend.reference import load_metrics_csv

    path = tmp_path / "titled.csv"
    path.write_text("video_A metrics — generated by teep_extract\n"
                    + config.REFERENCE_CSV.read_text())
    df = load_metrics_csv(path)
    assert "frame" in df.columns and "phase" in df.columns
    assert len(df) == len(load_metrics_csv(config.REFERENCE_CSV))


# ---------------------------------------------------------------------------
# §7.1 — the scoring curve
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "delta,expected",
    [(0.0, 100.0), (5.0, 100.0), (10.0, 50.0), (15.0, 0.0), (100.0, 0.0), (-10.0, 50.0)],
)
def test_band_score_is_linear_between_the_bands(delta, expected):
    assert band_score(delta, full=5.0, zero=15.0) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# §1 — the four scored metrics, and only those
# ---------------------------------------------------------------------------
def test_exactly_four_scored_metrics():
    assert set(config.METRICS) == {
        "lead_hip_flexion", "lead_knee_angle", "torso_tilt", "rear_knee_angle",
    }
    assert "hip_drive" not in config.METRICS
    assert "rear_hip_flexion" not in config.METRICS
    assert sum(m["weight"] for m in config.METRICS.values()) == pytest.approx(1.0)


def test_phase_weights_are_three_to_one():
    """
    §7.6's "Tier 1 : Tier 2 ratio is 3:1" is a per-phase ratio, not a ratio of
    tier totals: each Tier-1 phase carries 20% and each Tier-2 phase 6.67%, so
    the totals come out 80:20.
    """
    tier2_phases = ("ready", "impact", "reset")
    tier1 = sum(config.PHASE_WEIGHTS[p] for p in config.TIER1_PHASES)
    tier2 = sum(config.PHASE_WEIGHTS[p] for p in tier2_phases)
    assert tier1 == pytest.approx(0.8)
    assert tier2 == pytest.approx(0.2)

    for a in config.TIER1_PHASES:
        assert config.PHASE_WEIGHTS[a] == pytest.approx(0.20)
        for b in tier2_phases:
            assert config.PHASE_WEIGHTS[a] / config.PHASE_WEIGHTS[b] == pytest.approx(3.0)

    assert sum(config.PHASE_WEIGHTS.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# §8 — feedback wording carries the right sign
# ---------------------------------------------------------------------------
def test_knee_direction_words_are_not_inverted():
    """
    Higher knee angle means STRAIGHTER, not more flexed. Getting this backwards
    would invert every knee cue while still reading like fluent coaching.
    """
    from backend.feedback import build_feedback

    tol = {k: {"full": 4.0, "zero": 12.0} for k in config.METRICS}
    phase = {
        "name": "extension",
        "weight": 0.2,
        "start": 40,
        "end": 47,
        "metric_scores": {k: 0.0 for k in config.METRICS},
        "metric_deltas": {"lead_knee_angle": 12.0, "lead_hip_flexion": -12.0,
                          "torso_tilt": -9.0, "rear_knee_angle": -7.0},
        "metric_worst_frame": {k: 44 for k in config.METRICS},
    }
    texts = {f["metric"]: f["text"] for f in build_feedback([phase], tol)}

    assert "straighter" in texts["lead_knee_angle"]
    assert "more bent" not in texts["lead_knee_angle"]

    phase["metric_deltas"]["lead_knee_angle"] = -12.0
    texts = {f["metric"]: f["text"] for f in build_feedback([phase], tol)}
    assert "more bent" in texts["lead_knee_angle"]


def test_feedback_suppresses_good_scores_and_caps_the_list():
    from backend.feedback import build_feedback

    tol = {k: {"full": 4.0, "zero": 12.0} for k in config.METRICS}
    good = {
        "name": "extension", "weight": 0.2, "start": 40, "end": 47,
        "metric_scores": {k: 95.0 for k in config.METRICS},
        "metric_deltas": {k: 9.0 for k in config.METRICS},
        "metric_worst_frame": {k: 44 for k in config.METRICS},
    }
    assert build_feedback([good], tol) == []

    bad = [
        {"name": n, "weight": 0.2, "start": 40, "end": 47,
         "metric_scores": {k: 10.0 for k in config.METRICS},
         "metric_deltas": {k: 9.0 for k in config.METRICS},
         "metric_worst_frame": {k: 44 for k in config.METRICS}}
        for n in ("chamber", "extension", "retraction")
    ]
    assert len(build_feedback(bad, tol)) == config.FEEDBACK_TOP_N


def test_feedback_caps_items_per_metric():
    """
    One bad metric must not fill the whole list.

    Only torso_tilt is out of tolerance here, and it is out of tolerance in
    more phases than FEEDBACK_MAX_PER_METRIC allows. Without the cap it would
    take every slot — which is what it did on a real upload, hiding a
    lead_knee_angle and a lead_hip_flexion the scorer had already found.
    """
    from backend.feedback import build_feedback

    tol = {k: {"full": 4.0, "zero": 12.0} for k in config.METRICS}
    phases = [
        {"name": n, "weight": 0.2, "start": 40, "end": 47,
         "metric_scores": {k: (10.0 if k == "torso_tilt" else 95.0) for k in config.METRICS},
         "metric_deltas": {k: 9.0 for k in config.METRICS},
         "metric_worst_frame": {k: 44 for k in config.METRICS}}
        for n in ("chamber", "extension", "retraction", "recovery")
    ]
    items = build_feedback(phases, tol)

    assert [i["metric"] for i in items] == ["torso_tilt"] * config.FEEDBACK_MAX_PER_METRIC
    # The phases it kept are distinct — the cap trims the list, it does not
    # collapse it onto one phase.
    assert len({i["phase"] for i in items}) == len(items)
