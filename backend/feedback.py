"""
feedback.py — deterministic template feedback (spec §8).

No model, no key, no network. A lookup table of four sentences filled from data
the scorer has already produced.

The direction words are the part that matters most. `*_knee_angle` is higher
when the knee is STRAIGHTER, not more flexed, so wiring the sign backwards here
would invert every knee cue while still reading like fluent coaching.
"""

from __future__ import annotations

from . import config

# metric key -> (sentence template, word when B > A, word when B < A)
TEMPLATES = {
    "lead_hip_flexion": (
        "Lead hip is {delta}° {dir} flexed than reference during {phase}.",
        "more", "less",
    ),
    "lead_knee_angle": (
        "Lead knee is {delta}° {dir} than reference during {phase}.",
        "straighter", "more bent",
    ),
    "rear_knee_angle": (
        "Rear knee is {delta}° {dir} than reference during {phase}.",
        "straighter", "more bent",
    ),
    "torso_tilt": (
        "Body is {delta}° {dir} than reference during {phase}.",
        "more upright", "further back",
    ),
}


def build_feedback(phases: list[dict], tolerances: dict) -> list[dict]:
    """
    Rank every (metric, phase) pair and return the ones worth flagging.

    Every pair scoring at or below FEEDBACK_SUPPRESS_ABOVE is returned, ranked
    worst-first. That threshold matches the UI's green band, so the list is
    exactly the set of yellow and red readings — one timeline marker each.

    A pair above the threshold is good enough not to flag, so a clean rep
    legitimately produces zero items and therefore no markers. That is a
    deliberate state, not an empty container to be filled with filler.
    """
    candidates = []

    for p in phases:
        scores = p.get("metric_scores") or {}
        deltas = p.get("metric_deltas") or {}
        for key in config.METRICS:
            if key not in scores or key not in deltas:
                continue
            score = scores[key]
            if score > config.FEEDBACK_SUPPRESS_ABOVE:
                continue

            signed = deltas[key]
            # Rounded to whole degrees: decimals imply a precision MediaPipe
            # does not have.
            magnitude = int(round(abs(signed)))
            if magnitude == 0:
                continue

            template, word_hi, word_lo = TEMPLATES[key]
            text = template.format(
                delta=magnitude,
                dir=word_hi if signed > 0 else word_lo,
                phase=config.PHASE_DISPLAY.get(p["name"], p["name"]),
            )

            # Severity in tolerance units, so metrics with different natural
            # ranges compete on equal terms, then weighted by how much this
            # metric and this phase actually matter to the overall score.
            full = tolerances[key]["full"]
            severity = (abs(signed) / full) * config.METRICS[key]["weight"] * p["weight"]

            worst = (p.get("metric_worst_frame") or {}).get(key)

            candidates.append({
                "metric": key,
                "metric_label": config.METRICS[key]["label"],
                "phase": p["name"],
                "text": text,
                "delta": magnitude,
                "score": round(score, 1),
                "severity": round(severity, 4),
                # Frames of video_B this item is talking about, so the UI can
                # send the viewer straight to them.
                "start_frame": int(p["start"]),
                "end_frame": int(p["end"]),
                "worst_frame": int(worst) if worst is not None else int(p["start"]),
            })

    # Every surviving pair is returned — the list is not truncated. The UI draws
    # a marker per item, and the timeline is meant to show one wherever a metric
    # is yellow or red in a phase, so any cap here would silently hide a fault
    # the scorer found. FEEDBACK_SUPPRESS_ABOVE is the only gate.
    #
    # Severity still decides the order, so the worst item leads in a grouped
    # hover tip. Sorting on the keys as a tiebreak keeps the output deterministic
    # when two pairs score identically, which dict ordering would not.
    candidates.sort(key=lambda c: (-c["severity"], c["metric"], c["phase"]))
    return candidates
