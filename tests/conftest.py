"""Shared fixtures. Derived clips are built once per session from video_A."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend import config
from backend.pipeline import run_extraction
from backend.reference import build_reference


@pytest.fixture(scope="session")
def ref():
    return build_reference()


@pytest.fixture(scope="session")
def tmp_videos(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("videos")


def _write_clip(src: Path, dst: Path, start: int = 0, end: int | None = None,
                mirror: bool = False) -> Path:
    import cv2

    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i >= start and (end is None or i <= end):
            writer.write(cv2.flip(frame, 1) if mirror else frame)
        i += 1
    cap.release()
    writer.release()
    return dst


@pytest.fixture(scope="session")
def mid_chamber_clip(tmp_videos):
    """Starts mid-chamber, so `ready` was never filmed (spec §14)."""
    return _write_clip(config.REFERENCE_VIDEO, tmp_videos / "mid_chamber.mp4", start=38)


@pytest.fixture(scope="session")
def mirrored_clip(tmp_videos):
    """Horizontally flipped, which makes it a right-legged teep."""
    return _write_clip(config.REFERENCE_VIDEO, tmp_videos / "mirrored.mp4", mirror=True)


@pytest.fixture(scope="session")
def extracted_mid_chamber(mid_chamber_clip):
    return run_extraction(mid_chamber_clip)


@pytest.fixture(scope="session")
def extracted_mirrored(mirrored_clip):
    return run_extraction(mirrored_clip)
