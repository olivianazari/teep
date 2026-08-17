# Teep Detector

Full spec: @IMPLEMENTATION_PLAN.md — read it before writing code.

## Invariants (do not change without asking)
- Four scored metrics only: lead_hip_flexion, lead_knee_angle,
  torso_tilt, rear_knee_angle. Do not add hip_drive or rear_hip_flexion —
  both were removed for redundancy (see spec §1).
- No LLM anywhere. Feedback is deterministic templates.
- Do not modify teep_extract.py.
- video_A is reference only and is never scored.
- Gate: scoring video_A against itself must return exactly 100.0.
  Do not proceed past build step 4 until this passes.
