#!/usr/bin/env python3
"""
teep_extract.py — frame-by-frame biomechanical extraction for Muay Thai teeps.

Pipeline:
    video -> MediaPipe PoseLandmarker (Heavy, VIDEO mode)
          -> gap fill + zero-lag Butterworth smoothing
          -> joint angles, segment-mass CoM, limb extension, stability
          -> phase segmentation (stance/chamber/extension/impact/retraction/recovery)
          -> metrics.csv (+ optional raw landmark dump, + summary.json)

Requires:
    pip install mediapipe opencv-python numpy pandas scipy
    Model: pose_landmarker_heavy.task
    https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/1/pose_landmarker_heavy.task

Usage:
    python teep_extract.py --video video_A.mp4 --model pose_landmarker_heavy.task \
        --out out/ --dump-landmarks
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Landmark index map (BlazePose 33)
# ----------------------------------------------------------------------------
L = {
    "nose": 0,
    "l_ear": 7, "r_ear": 8,
    "l_shoulder": 11, "r_shoulder": 12,
    "l_elbow": 13, "r_elbow": 14,
    "l_wrist": 15, "r_wrist": 16,
    "l_index": 19, "r_index": 20,
    "l_hip": 23, "r_hip": 24,
    "l_knee": 25, "r_knee": 26,
    "l_ankle": 27, "r_ankle": 28,
    "l_heel": 29, "r_heel": 30,
    "l_foot": 31, "r_foot": 32,
}
N_LM = 33

# MediaPipe world frame: origin at pelvis midpoint, metres, +y is DOWN.
UP = np.array([0.0, -1.0, 0.0])

# Winter (2009) anthropometrics: (proximal, distal, mass_fraction, com_fraction_from_proximal)
SEGMENTS = [
    ("head",      "l_ear",      "r_ear",      0.0810, 0.500),  # ear midpoint proxy
    ("trunk",     "_mid_sho",   "_mid_hip",   0.4970, 0.500),
    ("l_uarm",    "l_shoulder", "l_elbow",    0.0280, 0.436),
    ("r_uarm",    "r_shoulder", "r_elbow",    0.0280, 0.436),
    ("l_farm",    "l_elbow",    "l_wrist",    0.0160, 0.430),
    ("r_farm",    "r_elbow",    "r_wrist",    0.0160, 0.430),
    ("l_hand",    "l_wrist",    "l_index",    0.0060, 0.506),
    ("r_hand",    "r_wrist",    "r_index",    0.0060, 0.506),
    ("l_thigh",   "l_hip",      "l_knee",     0.1000, 0.433),
    ("r_thigh",   "r_hip",      "r_knee",     0.1000, 0.433),
    ("l_shank",   "l_knee",     "l_ankle",    0.0465, 0.433),
    ("r_shank",   "r_knee",     "r_ankle",    0.0465, 0.433),
    ("l_foot",    "l_heel",     "l_foot",     0.0145, 0.500),
    ("r_foot",    "r_heel",     "r_foot",     0.0145, 0.500),
]  # mass fractions sum to 1.000

PHASES = ["ready", "chamber", "extension", "impact", "retraction", "recovery", "reset"]


# ----------------------------------------------------------------------------
# Vector maths (all vectorised over frames: arrays are (N, 3))
# ----------------------------------------------------------------------------
def _unit(v: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, eps)


def _norm(v: np.ndarray) -> np.ndarray:
    return np.linalg.norm(v, axis=-1)


def angle_at(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Interior angle at vertex b, in degrees. 180 deg = fully straight."""
    v1, v2 = _unit(a - b), _unit(c - b)
    cos = np.clip(np.sum(v1 * v2, axis=-1), -1.0, 1.0)
    return np.degrees(np.arccos(cos))


def angle_between(v1: np.ndarray, v2: np.ndarray) -> np.ndarray:
    cos = np.clip(np.sum(_unit(v1) * _unit(v2), axis=-1), -1.0, 1.0)
    return np.degrees(np.arccos(cos))


def deriv(x: np.ndarray, dt: float) -> np.ndarray:
    """Central-difference first derivative, per second."""
    if len(x) < 2:
        return np.zeros_like(x)
    return np.gradient(x, dt, axis=0, edge_order=1)


# ----------------------------------------------------------------------------
# Stage 1 — landmark extraction
# ----------------------------------------------------------------------------
@dataclass
class RawTrack:
    fps: float
    width: int
    height: int
    world: np.ndarray       # (N, 33, 3) metres, pelvis-centred
    image: np.ndarray       # (N, 33, 3) normalised 0-1 image coords (+ z)
    vis: np.ndarray         # (N, 33) visibility
    detected: np.ndarray    # (N,) bool
    t_s: np.ndarray         # (N,) true decode timestamps in seconds
    roi: np.ndarray         # (N, 4) tracked ROI in pixels (x0, y0, x1, y1)


def _bbox_from_norm(lm_xy: np.ndarray, w: int, h: int) -> tuple[float, float, float, float]:
    x = lm_xy[:, 0] * w
    y = lm_xy[:, 1] * h
    return float(np.min(x)), float(np.min(y)), float(np.max(x)), float(np.max(y))


def extract_landmarks_legacy(video_path: str, det_conf: float, track_conf: float,
                             fps_override: Optional[float], person: str,
                             complexity: int = 1, pad: float = 0.30) -> RawTrack:
    """
    Legacy mp.solutions.pose backend (model bundled in the wheel, no download).

    Detects a single pose per frame, so isolating one athlete in a two-person
    scene is done with an adaptive ROI: seed on the requested half of the frame,
    then follow that athlete's own landmark bbox with generous padding. A
    continuity gate rejects frames where the detection jumps sideways onto a
    different body.
    """
    import cv2
    import mediapipe as mp

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise SystemExit(f"Could not open video: {video_path}")
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    if person == "left":
        roi = [0.0, 0.0, 0.52 * W, float(H)]
    elif person == "right":
        roi = [0.48 * W, 0.0, float(W), float(H)]
    else:
        roi = [0.0, 0.0, float(W), float(H)]

    world, image, vis, detected, times, rois = [], [], [], [], [], []
    prev_cx: Optional[float] = None
    misses = 0

    pose = mp.solutions.pose.Pose(
        static_image_mode=False,
        model_complexity=complexity,
        smooth_landmarks=False,        # we apply our own zero-lag filter downstream
        enable_segmentation=False,
        min_detection_confidence=det_conf,
        min_tracking_confidence=track_conf,
    )

    idx = 0
    with pose:
        while True:
            t_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            ok, frame = cap.read()
            if not ok:
                break

            x0, y0, x1, y1 = (int(round(v)) for v in roi)
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(W, max(x1, x0 + 32)), min(H, max(y1, y0 + 32))
            crop = frame[y0:y1, x0:x1]
            cw, ch = x1 - x0, y1 - y0

            res = pose.process(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))

            got = bool(res.pose_landmarks and res.pose_world_landmarks)
            if got:
                # crop-normalised -> full-frame-normalised
                i = np.array([[(x0 + p.x * cw) / W, (y0 + p.y * ch) / H, p.z]
                              for p in res.pose_landmarks.landmark])
                w_ = np.array([[p.x, p.y, p.z] for p in res.pose_world_landmarks.landmark])
                v = np.array([p.visibility for p in res.pose_landmarks.landmark])

                bx0, by0, bx1, by1 = _bbox_from_norm(i[:, :2], W, H)
                cx = 0.5 * (bx0 + bx1)

                # continuity gate: reject a sideways jump onto the other athlete
                if prev_cx is not None and abs(cx - prev_cx) > 0.25 * W:
                    got = False
                else:
                    bw, bh = max(bx1 - bx0, 1.0), max(by1 - by0, 1.0)
                    m = pad * max(bw, bh)
                    roi = [bx0 - m, by0 - m, bx1 + m, by1 + m]
                    prev_cx = cx
                    misses = 0

            if got:
                world.append(w_); image.append(i); vis.append(v); detected.append(True)
            else:
                world.append(np.full((N_LM, 3), np.nan))
                image.append(np.full((N_LM, 3), np.nan))
                vis.append(np.zeros(N_LM))
                detected.append(False)
                misses += 1
                # widen the search a little on each consecutive miss
                gx = 0.15 * misses * (roi[2] - roi[0])
                gy = 0.15 * misses * (roi[3] - roi[1])
                roi = [roi[0] - gx, roi[1] - gy, roi[2] + gx, roi[3] + gy]

            times.append(t_ms / 1000.0)
            rois.append([x0, y0, x1, y1])
            idx += 1
            if idx % 25 == 0:
                print(f"  ...{idx} frames", file=sys.stderr)

    cap.release()
    if not world:
        raise SystemExit("No frames decoded.")

    t_s = np.array(times, dtype=float)
    dts = np.diff(t_s)
    dts = dts[dts > 1e-6]
    if fps_override:
        fps = fps_override
    elif len(dts):
        fps = 1.0 / float(np.median(dts))
    else:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    return RawTrack(fps=float(fps), width=W, height=H,
                    world=np.stack(world), image=np.stack(image),
                    vis=np.stack(vis), detected=np.array(detected),
                    t_s=t_s, roi=np.array(rois, dtype=float))


def extract_landmarks(video_path: str, model_path: str,
                      det_conf: float, track_conf: float,
                      fps_override: Optional[float]) -> RawTrack:
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise SystemExit(f"Could not open video: {video_path}")

    fps = fps_override or cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    options = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=model_path),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,                       # lock to one athlete; see README note
        min_pose_detection_confidence=det_conf,
        min_pose_presence_confidence=det_conf,
        min_tracking_confidence=track_conf,
        output_segmentation_masks=False,
    )

    world, image, vis, detected = [], [], [], []

    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            ts_ms = int(round(idx * 1000.0 / fps))   # must be monotonically increasing
            res = landmarker.detect_for_video(mp_img, ts_ms)

            if res.pose_world_landmarks and res.pose_landmarks:
                w = np.array([[p.x, p.y, p.z] for p in res.pose_world_landmarks[0]])
                i = np.array([[p.x, p.y, p.z] for p in res.pose_landmarks[0]])
                v = np.array([p.visibility for p in res.pose_landmarks[0]])
                detected.append(True)
            else:
                w = np.full((N_LM, 3), np.nan)
                i = np.full((N_LM, 3), np.nan)
                v = np.zeros(N_LM)
                detected.append(False)

            world.append(w)
            image.append(i)
            vis.append(v)
            idx += 1

            if idx % 30 == 0:
                print(f"  ...{idx} frames", file=sys.stderr)

    cap.release()
    if not world:
        raise SystemExit("No frames decoded.")

    return RawTrack(
        fps=float(fps), width=width, height=height,
        world=np.stack(world), image=np.stack(image),
        vis=np.stack(vis), detected=np.array(detected),
        t_s=np.arange(len(world)) / float(fps),
        roi=np.tile([0, 0, width, height], (len(world), 1)).astype(float),
    )


def resample_uniform(trk: "RawTrack", target_fps: Optional[float] = None) -> "RawTrack":
    """
    Put a variable-frame-rate clip on an even timebase.

    Butterworth filtering and central-difference derivatives both assume uniform
    sampling. Phone and edited-export .mov files frequently are not: duplicated
    timestamps and alternating 30/20 Hz intervals are common. Interpolating onto
    a fixed grid first keeps every velocity column honest.
    """
    t = trk.t_s.astype(float)
    keep = np.concatenate([[True], np.diff(t) > 1e-9])   # drop duplicate timestamps
    t, world, image = t[keep], trk.world[keep], trk.image[keep]
    vis, det = trk.vis[keep], trk.detected[keep]

    if len(t) < 3:
        return trk

    dts = np.diff(t)
    if target_fps is None:
        target_fps = float(np.round(1.0 / np.median(dts)))
    tu = np.arange(t[0], t[-1] + 1e-9, 1.0 / target_fps)

    def interp3(arr: np.ndarray) -> np.ndarray:
        n, m, d = arr.shape
        flat = arr.reshape(n, m * d)
        out = np.empty((len(tu), m * d))
        for c in range(m * d):
            col = flat[:, c]
            ok = ~np.isnan(col)
            out[:, c] = np.interp(tu, t[ok], col[ok]) if ok.sum() >= 2 else np.nan
        return out.reshape(len(tu), m, d)

    vis_u = np.stack([np.interp(tu, t, vis[:, j]) for j in range(vis.shape[1])], axis=1)
    det_u = np.interp(tu, t, det.astype(float)) > 0.5

    return RawTrack(fps=float(target_fps), width=trk.width, height=trk.height,
                    world=interp3(world), image=interp3(image),
                    vis=vis_u, detected=det_u, t_s=tu,
                    roi=np.stack([np.interp(tu, t, trk.roi[keep][:, c]) for c in range(4)],
                                 axis=1))


# ----------------------------------------------------------------------------
# Stage 2 — gap fill + smoothing
# ----------------------------------------------------------------------------
def fill_and_smooth(arr: np.ndarray, fps: float, cutoff_hz: float) -> np.ndarray:
    """Linear-interpolate NaN gaps, then zero-lag 4th-order Butterworth low-pass."""
    n, m, d = arr.shape
    flat = arr.reshape(n, m * d)
    df = pd.DataFrame(flat).interpolate(
        method="linear", limit_direction="both", axis=0
    ).bfill().ffill()
    filled = df.to_numpy()

    if np.isnan(filled).any():          # nothing was ever detected
        return filled.reshape(n, m, d)

    nyq = fps / 2.0
    if cutoff_hz <= 0 or cutoff_hz >= nyq or n < 16:
        return filled.reshape(n, m, d)

    try:
        from scipy.signal import butter, filtfilt
        b, a = butter(4, cutoff_hz / nyq, btype="low")
        # padlen must be < signal length
        padlen = min(3 * max(len(a), len(b)), n - 1)
        out = filtfilt(b, a, filled, axis=0, padlen=padlen)
    except Exception as e:                              # noqa: BLE001
        print(f"[warn] Butterworth unavailable ({e}); falling back to rolling mean.",
              file=sys.stderr)
        k = max(3, int(round(fps / max(cutoff_hz, 1e-6))) | 1)
        out = pd.DataFrame(filled).rolling(k, center=True, min_periods=1).mean().to_numpy()

    return out.reshape(n, m, d)


# ----------------------------------------------------------------------------
# Stage 3 — derived quantities
# ----------------------------------------------------------------------------
def pt(w: np.ndarray, name: str) -> np.ndarray:
    """Named point, including virtual midpoints."""
    if name == "_mid_hip":
        return 0.5 * (w[:, L["l_hip"]] + w[:, L["r_hip"]])
    if name == "_mid_sho":
        return 0.5 * (w[:, L["l_shoulder"]] + w[:, L["r_shoulder"]])
    return w[:, L[name]]


def compute_com(w: np.ndarray) -> np.ndarray:
    """Whole-body centre of mass via 14-segment Winter model. Returns (N, 3)."""
    com = np.zeros((w.shape[0], 3))
    total = 0.0
    for _, prox, dist, mass, frac in SEGMENTS:
        p, d = pt(w, prox), pt(w, dist)
        com += mass * (p + frac * (d - p))
        total += mass
    return com / total


def detect_kick_side(w: np.ndarray) -> str:
    """Kicking leg = the ankle with the greatest excursion from its own baseline."""
    mid_hip = pt(w, "_mid_hip")[:, None, :]
    rel = w - mid_hip
    spread = {}
    for side in ("l", "r"):
        a = rel[:, L[f"{side}_ankle"]]
        base = np.median(a, axis=0)
        spread[side] = float(np.max(np.linalg.norm(a - base, axis=-1)))
    return "left" if spread["l"] >= spread["r"] else "right"


def build_frame(w: np.ndarray, kick: str) -> tuple[np.ndarray, np.ndarray, int]:
    """
    Anatomical horizontal frame, fixed across the clip:
      AP = anterior-posterior, pointing in the kick direction
      ML = medio-lateral (AP rotated 90 deg about vertical)
    Returns (AP, ML, apex_frame_guess).
    """
    hip = pt(w, "_mid_hip")
    ankle = w[:, L[f"{kick[0]}_ankle"]]
    v = ankle - hip
    v_h = v - np.outer(v @ UP, UP)                 # strip vertical component
    reach = np.linalg.norm(v_h, axis=-1)
    apex = int(np.argmax(reach))
    ap = _unit(v_h[apex])
    ml = np.cross(UP, ap)
    ml = ml / max(np.linalg.norm(ml), 1e-9)
    return ap, ml, apex


def compute_metrics(w: np.ndarray, img: np.ndarray, vis: np.ndarray,
                    fps: float, kick: str) -> tuple[pd.DataFrame, dict]:
    n = w.shape[0]
    dt = 1.0 / fps
    k, s = kick[0], "r" if kick == "left" else "l"     # kick / support prefixes

    mid_hip, mid_sho = pt(w, "_mid_hip"), pt(w, "_mid_sho")
    trunk_vec = mid_sho - mid_hip                       # points cranially
    ap, ml, apex_guess = build_frame(w, kick)

    # --- scale references (robust across clip) -------------------------------
    torso_len = float(np.median(_norm(trunk_vec)))
    # Reference leg length uses a high percentile of the per-frame segment sum:
    # MediaPipe segment lengths are not perfectly rigid, and a median denominator
    # lets the ratio drift above 1.0 during upright stance.
    thigh_pf = _norm(w[:, L[f"{k}_knee"]] - w[:, L[f"{k}_hip"]])
    shank_pf = _norm(w[:, L[f"{k}_ankle"]] - w[:, L[f"{k}_knee"]])
    thigh_len = float(np.median(thigh_pf))
    shank_len = float(np.median(shank_pf))
    leg_len = float(np.percentile(thigh_pf + shank_pf, 90))
    sup_leg_len = float(np.percentile(
        _norm(w[:, L[f"{s}_knee"]] - w[:, L[f"{s}_hip"]])
        + _norm(w[:, L[f"{s}_ankle"]] - w[:, L[f"{s}_knee"]]), 90))
    scale = torso_len if torso_len > 1e-6 else 1.0

    d = {}
    d["kick_side"] = kick

    # --- joint angles --------------------------------------------------------
    for tag, pfx in (("kick", k), ("sup", s)):
        d[f"{tag}_knee_angle"] = angle_at(
            w[:, L[f"{pfx}_hip"]], w[:, L[f"{pfx}_knee"]], w[:, L[f"{pfx}_ankle"]])
        # hip flexion: 0 deg = thigh in line with trunk, 90 deg = thigh horizontal
        d[f"{tag}_hip_flexion"] = 180.0 - angle_between(
            trunk_vec, w[:, L[f"{pfx}_knee"]] - w[:, L[f"{pfx}_hip"]])
        # ankle: 90 deg ~ neutral. Low reliability (2 foot landmarks only).
        d[f"{tag}_ankle_angle"] = angle_at(
            w[:, L[f"{pfx}_knee"]], w[:, L[f"{pfx}_ankle"]], w[:, L[f"{pfx}_foot"]])
        # hip abduction: thigh deviation from the sagittal plane
        thigh = _unit(w[:, L[f"{pfx}_knee"]] - w[:, L[f"{pfx}_hip"]])
        d[f"{tag}_hip_abduction"] = np.degrees(np.arcsin(np.clip(thigh @ ml, -1, 1)))
        d[f"{tag}_elbow_angle"] = angle_at(
            w[:, L[f"{pfx}_shoulder"]], w[:, L[f"{pfx}_elbow"]], w[:, L[f"{pfx}_wrist"]])

    # --- trunk & pelvis ------------------------------------------------------
    tu = _unit(trunk_vec)
    d["trunk_lean_total"] = angle_between(trunk_vec, np.broadcast_to(UP, trunk_vec.shape))
    d["trunk_lean_sagittal"] = np.degrees(np.arcsin(np.clip(tu @ ap, -1, 1)))   # + = forward
    d["trunk_lean_frontal"] = np.degrees(np.arcsin(np.clip(tu @ ml, -1, 1)))

    hip_vec = w[:, L["r_hip"]] - w[:, L["l_hip"]]
    sho_vec = w[:, L["r_shoulder"]] - w[:, L["l_shoulder"]]
    hu = _unit(hip_vec)
    # frontal obliquity: + = right hip higher than left
    d["pelvis_tilt_frontal"] = np.degrees(np.arcsin(np.clip(hu @ UP, -1, 1)))
    # transverse rotation of pelvis and shoulders relative to the fixed AP axis
    d["pelvis_rotation"] = np.degrees(np.arctan2(hu @ ap, hu @ ml))
    d["shoulder_rotation"] = np.degrees(np.arctan2(_unit(sho_vec) @ ap, _unit(sho_vec) @ ml))
    sep = d["shoulder_rotation"] - d["pelvis_rotation"]
    d["shoulder_hip_separation"] = (sep + 180.0) % 360.0 - 180.0

    # --- pelvis-tilt confidence (camera-angle guard) -------------------------
    # When the pelvis turns edge-on, projected hip width collapses and the tilt
    # estimate becomes ill-conditioned. Foreshortening is measured in IMAGE space
    # (the actual camera projection); world-space hip width is nearly constant by
    # construction, so it cannot detect this on its own.
    # Hip width is divided by image-space torso length first: without that, a
    # camera zoom changes hip width in pixels and would be misread as rotation.
    img_hip_w = np.linalg.norm(img[:, L["r_hip"], :2] - img[:, L["l_hip"], :2], axis=-1)
    img_torso = np.linalg.norm(
        0.5 * (img[:, L["l_shoulder"], :2] + img[:, L["r_shoulder"], :2])
        - 0.5 * (img[:, L["l_hip"], :2] + img[:, L["r_hip"], :2]), axis=-1)
    hip_w_rel = img_hip_w / np.maximum(img_torso, 1e-9)
    ref_hip_w = float(np.percentile(hip_w_rel, 90)) or 1.0
    fore = np.clip(hip_w_rel / max(ref_hip_w, 1e-9), 0.0, 1.0)
    hip_vis = np.minimum(vis[:, L["l_hip"]], vis[:, L["r_hip"]])
    d["pelvis_foreshorten_ratio"] = fore
    d["pelvis_tilt_conf"] = np.clip(fore, 0, 1) ** 1.5 * np.clip(hip_vis, 0, 1)

    # --- limb extension ------------------------------------------------------
    kick_hip_to_ankle = _norm(w[:, L[f"{k}_ankle"]] - w[:, L[f"{k}_hip"]])
    d["kick_leg_ext_ratio"] = kick_hip_to_ankle / max(leg_len, 1e-9)
    d["sup_leg_ext_ratio"] = _norm(
        w[:, L[f"{s}_ankle"]] - w[:, L[f"{s}_hip"]]) / max(sup_leg_len, 1e-9)

    ankle_rel = w[:, L[f"{k}_ankle"]] - mid_hip
    d["kick_ankle_reach_ap"] = (ankle_rel @ ap) / scale
    d["kick_ankle_reach_ml"] = (ankle_rel @ ml) / scale
    d["kick_ankle_height"] = (ankle_rel @ UP) / scale        # + = above pelvis
    d["kick_knee_height"] = ((w[:, L[f"{k}_knee"]] - mid_hip) @ UP) / scale

    sup_ankle_rel = w[:, L[f"{s}_ankle"]] - mid_hip
    d["sup_ankle_height"] = (sup_ankle_rel @ UP) / scale

    # --- guard ---------------------------------------------------------------
    for tag, pfx in (("kick", k), ("sup", s)):
        d[f"{tag}_wrist_height"] = ((w[:, L[f"{pfx}_wrist"]] - mid_sho) @ UP) / scale

    # --- centre of mass ------------------------------------------------------
    com = compute_com(w)
    com_rel = com - mid_hip
    d["com_ap"] = (com_rel @ ap) / scale
    d["com_ml"] = (com_rel @ ml) / scale
    d["com_height"] = (com_rel @ UP) / scale

    com_v = deriv(com, dt)
    d["com_speed"] = _norm(com_v) / scale                      # torso-lengths / s
    d["com_vel_ap"] = (com_v @ ap) / scale
    d["com_vel_vert"] = (com_v @ UP) / scale

    # --- stability -----------------------------------------------------------
    # Support base: midpoint of heel and toe of the planted foot.
    sup_base = 0.5 * (w[:, L[f"{s}_heel"]] + w[:, L[f"{s}_foot"]])
    off = com - sup_base
    d["com_over_base_ap"] = (off @ ap) / scale                 # + = CoM ahead of foot
    d["com_over_base_ml"] = (off @ ml) / scale
    d["com_base_dist"] = np.sqrt(d["com_over_base_ap"] ** 2 + d["com_over_base_ml"] ** 2)

    sup_ankle_v = deriv(w[:, L[f"{s}_ankle"]], dt)
    d["sup_ankle_speed"] = _norm(sup_ankle_v) / scale          # plant slip / pivot rate
    d["sup_foot_planted"] = (d["sup_ankle_speed"] < 0.6).astype(int)

    # --- velocities ----------------------------------------------------------
    for col in ("kick_knee_angle", "kick_hip_flexion", "sup_knee_angle",
                "trunk_lean_sagittal", "pelvis_tilt_frontal", "kick_leg_ext_ratio"):
        d[f"{col}_vel"] = deriv(d[col], dt)
    d["kick_ankle_speed"] = _norm(deriv(w[:, L[f"{k}_ankle"]], dt)) / scale

    df = pd.DataFrame({key: val for key, val in d.items() if key != "kick_side"})
    df.insert(0, "kick_side", kick)

    refs = {
        "torso_len_m": torso_len,
        "kick_leg_len_m": leg_len,
        "thigh_len_m": thigh_len,
        "shank_len_m": shank_len,
        "apex_guess_frame": apex_guess,
    }
    return df, refs


# ----------------------------------------------------------------------------
# Stage 4 — phase segmentation
# ----------------------------------------------------------------------------
def segment_phases(df: pd.DataFrame, fps: float) -> tuple[list[str], np.ndarray, dict]:
    """
    Teep phase model, driven by kicking-knee angle + hip flexion + ankle reach.

        ready      baseline stance
        chamber    hip flexes, knee folds -> peak knee flexion
        extension  knee drives out toward the target
        impact     +/- window around peak extension
        retraction knee re-folds, foot pulled back
        recovery   foot descends toward replant
        reset      post-replant stance
    """
    n = len(df)
    knee = df["kick_knee_angle"].to_numpy()
    hipf = df["kick_hip_flexion"].to_numpy()
    ext = df["kick_leg_ext_ratio"].to_numpy()
    ah = df["kick_ankle_height"].to_numpy()
    reach = df["kick_ankle_reach_ap"].to_numpy()

    notes = {}

    # Active window: hip flexion meaningfully above its own baseline.
    hb = float(np.percentile(hipf, 10))
    hpk = float(np.max(hipf))
    hip_range = hpk - hb
    if hip_range < 8.0:            # essentially no kick in the clip
        notes["warning"] = "hip flexion range < 8 deg; phase model is unreliable"
        return ["ready"] * n, np.zeros(n), notes

    on_thresh = hb + 0.20 * hip_range
    active = hipf > on_thresh
    idx_active = np.flatnonzero(active)
    a0, a1 = int(idx_active[0]), int(idx_active[-1])

    # Apex: peak forward reach inside the active window (falls back to extension ratio).
    win = slice(a0, a1 + 1)
    apex = a0 + int(np.argmax(reach[win]))
    if ext[apex] < 0.80:
        apex = a0 + int(np.argmax(ext[win]))

    # Chamber peak: deepest knee fold between kick onset and apex.
    chamber_peak = a0 + int(np.argmin(knee[a0:apex + 1])) if apex > a0 else a0

    # Chamber onset: last frame before chamber_peak where hip flexion is at baseline.
    pre = np.flatnonzero(hipf[:chamber_peak + 1] <= hb + 0.10 * hip_range)
    chamber_start = int(pre[-1]) if len(pre) else 0

    # Impact window: +/- 40 ms around apex.
    half = max(1, int(round(0.040 * fps)))
    imp0, imp1 = max(chamber_peak + 1, apex - half), min(n - 1, apex + half)

    # Retraction end: knee re-folds to within 25% of its chamber depth.
    knee_target = knee[chamber_peak] + 0.25 * (knee[apex] - knee[chamber_peak])
    post = np.flatnonzero(knee[imp1:] <= knee_target)
    retract_end = int(imp1 + post[0]) if len(post) else min(n - 1, imp1 + int(0.25 * fps))

    # Replant: kicking ankle returns to support-ankle height.
    ah_base = float(np.percentile(ah[:max(chamber_start, 1)], 50)) if chamber_start > 0 \
        else float(np.percentile(ah, 10))
    post2 = np.flatnonzero(ah[retract_end:] <= ah_base + 0.05)
    replant = int(retract_end + post2[0]) if len(post2) else n - 1

    bounds = [
        ("ready", 0, chamber_start),
        ("chamber", chamber_start, chamber_peak),
        ("extension", chamber_peak, imp0),
        ("impact", imp0, imp1),
        ("retraction", imp1, retract_end),
        ("recovery", retract_end, replant),
        ("reset", replant, n - 1),
    ]

    phase = ["ready"] * n
    pct = np.zeros(n)
    # Half-open intervals: without this the next phase overwrites the previous
    # phase's final frame, which silently shortens the impact window and skews
    # any phase-weighted score built on these labels.
    for bi, (name, lo, hi) in enumerate(bounds):
        lo, hi = int(max(0, lo)), int(min(n - 1, hi))
        last = bi == len(bounds) - 1
        stop = hi if last else hi - 1
        if stop < lo:
            continue
        span = max(hi - lo, 1)
        for i in range(lo, stop + 1):
            phase[i] = name
            pct[i] = 100.0 * (i - lo) / span

    notes.update({
        "chamber_start": chamber_start,
        "chamber_peak": chamber_peak,
        "apex": apex,
        "impact_window": [imp0, imp1],
        "retract_end": retract_end,
        "replant": replant,
        "chamber_knee_angle_deg": round(float(knee[chamber_peak]), 2),
        "apex_knee_angle_deg": round(float(knee[apex]), 2),
        "apex_extension_ratio": round(float(ext[apex]), 4),
        "peak_hip_flexion_deg": round(float(hpk), 2),
        "chamber_to_apex_ms": round(1000.0 * (apex - chamber_peak) / fps, 1),
        "apex_to_replant_ms": round(1000.0 * (replant - apex) / fps, 1),
    })
    return phase, pct, notes


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main() -> None:
    ap_ = argparse.ArgumentParser(description="Teep biomechanics extraction")
    ap_.add_argument("--video", required=True)
    ap_.add_argument("--model", default="pose_landmarker_heavy.task")
    ap_.add_argument("--out", default="out")
    ap_.add_argument("--cutoff", type=float, default=10.0,
                     help="Butterworth low-pass cutoff in Hz (default 10; use 12-15 for "
                          "high-speed footage, 6-8 for noisy phone video)")
    ap_.add_argument("--leg", choices=["auto", "left", "right"], default="auto")
    ap_.add_argument("--det-conf", type=float, default=0.5)
    ap_.add_argument("--track-conf", type=float, default=0.5)
    ap_.add_argument("--fps-override", type=float, default=None)
    ap_.add_argument("--backend", choices=["tasks", "legacy"], default="tasks",
                     help="tasks = PoseLandmarker (needs .task file, better accuracy); "
                          "legacy = mp.solutions.pose (model bundled in the wheel, offline)")
    ap_.add_argument("--person", choices=["any", "left", "right"], default="any",
                     help="which athlete to track in a multi-person scene (legacy backend)")
    ap_.add_argument("--complexity", type=int, default=1, choices=[0, 1, 2],
                     help="legacy model complexity: 0 lite, 1 full, 2 heavy")
    ap_.add_argument("--dump-landmarks", action="store_true",
                     help="also write landmarks_world.csv / landmarks_image.csv")
    args = ap_.parse_args()

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.video))[0]

    print(f"[1/4] Extracting landmarks from {args.video} ...", file=sys.stderr)
    if args.backend == "legacy":
        trk = extract_landmarks_legacy(args.video, args.det_conf, args.track_conf,
                                       args.fps_override, args.person, args.complexity)
    else:
        trk = extract_landmarks(args.video, args.model,
                                args.det_conf, args.track_conf, args.fps_override)
    n_raw = trk.world.shape[0]
    dts_raw = np.diff(trk.t_s)
    dts_raw = dts_raw[dts_raw > 1e-9]
    vfr = bool(len(dts_raw) and (dts_raw.max() - dts_raw.min()) > 0.2 * np.median(dts_raw))
    if vfr:
        print(f"[warn] Variable frame rate detected "
              f"(dt {dts_raw.min():.4f}-{dts_raw.max():.4f}s). Resampling to uniform.",
              file=sys.stderr)
        trk = resample_uniform(trk)
        print(f"      Resampled {n_raw} -> {trk.world.shape[0]} frames @ {trk.fps:.2f} fps",
              file=sys.stderr)

    n = trk.world.shape[0]
    det_rate = float(trk.detected.mean())
    print(f"      {n} frames @ {trk.fps:.2f} fps | detection rate {det_rate:.1%}",
          file=sys.stderr)
    if det_rate < 0.85:
        print("[warn] Low detection rate — check lighting, framing, or lower --det-conf.",
              file=sys.stderr)

    print(f"[2/4] Filling gaps + smoothing (cutoff {args.cutoff} Hz) ...", file=sys.stderr)
    world_s = fill_and_smooth(trk.world, trk.fps, args.cutoff)
    image_s = fill_and_smooth(trk.image, trk.fps, args.cutoff)

    kick = args.leg if args.leg != "auto" else detect_kick_side(world_s)
    print(f"      Kicking leg: {kick}", file=sys.stderr)

    print("[3/4] Computing joint angles, CoM, extension, stability ...", file=sys.stderr)
    df, refs = compute_metrics(world_s, image_s, trk.vis, trk.fps, kick)

    print("[4/4] Segmenting phases ...", file=sys.stderr)
    phase, pct, notes = segment_phases(df, trk.fps)

    # Assemble front matter
    df.insert(0, "frame", np.arange(n))
    df.insert(1, "time_s", np.round(trk.t_s, 5))
    df.insert(2, "detected", trk.detected.astype(int))
    df.insert(3, "vis_min", trk.vis.min(axis=1))
    df.insert(4, "phase", phase)
    df.insert(5, "phase_pct", np.round(pct, 2))

    num = df.select_dtypes(include=[np.number]).columns
    df[num] = df[num].round(5)

    csv_path = os.path.join(args.out, f"{stem}_metrics.csv")
    df.to_csv(csv_path, index=False)

    summary = {
        "video": os.path.basename(args.video),
        "frames": n,
        "fps": round(trk.fps, 3),
        "duration_s": round(n / trk.fps, 3),
        "resolution": [trk.width, trk.height],
        "detection_rate": round(det_rate, 4),
        "kick_side": kick,
        "smoothing_cutoff_hz": args.cutoff,
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
    }
    json_path = os.path.join(args.out, f"{stem}_summary.json")
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2)

    if args.dump_landmarks:
        names = {v: k for k, v in L.items()}
        for tag, arr in (("world", world_s), ("image", image_s)):
            cols, data = [], []
            for j in range(N_LM):
                nm = names.get(j, f"lm{j}")
                for axis, a in enumerate("xyz"):
                    cols.append(f"{nm}_{a}")
                    data.append(arr[:, j, axis])
            ld = pd.DataFrame(np.array(data).T, columns=cols).round(6)
            ld.insert(0, "frame", np.arange(n))
            ld.to_csv(os.path.join(args.out, f"{stem}_landmarks_{tag}.csv"), index=False)

    print(f"\nWrote {csv_path}  ({len(df)} rows x {len(df.columns)} cols)", file=sys.stderr)
    print(f"Wrote {json_path}", file=sys.stderr)
    print(json.dumps(summary["phases"], indent=2))


if __name__ == "__main__":
    main()
