"""
main.py — FastAPI application: routes, SSE progress, static serving.

One process, one port. The built React bundle is served from dist/ by this same
app; there is no separate Node server in the shipped pack.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
import tempfile
import threading
import traceback
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .analysis import RefusalError, analyze
from .align import AlignmentError
from .pipeline import ExtractionError, run_extraction
from .reference import ReferenceError, get_reference

app = FastAPI(title="Teep Analyzer", docs_url=None, redoc_url=None)

# Uploads live in a temp dir for the life of the process only — the app is
# stateless per session, with no accounts, persistence or history.
UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="teep_uploads_"))
_uploads: dict[str, Path] = {}
_uploads_lock = threading.Lock()

# Startup reference check. A failure here is not fatal to the process: the
# server stays up so the browser can render the reason instead of failing to
# connect at all.
_REFERENCE_ERROR: Optional[str] = None
try:
    get_reference()
except ReferenceError as exc:
    _REFERENCE_ERROR = str(exc)
except Exception as exc:  # noqa: BLE001
    _REFERENCE_ERROR = f"Unexpected error loading the reference: {exc}"


def _reference_unavailable() -> Optional[JSONResponse]:
    if _REFERENCE_ERROR:
        return JSONResponse(
            status_code=503,
            content={"code": "reference_unavailable", "message": _REFERENCE_ERROR},
        )
    return None


# ---------------------------------------------------------------------------
# Range-capable file serving
# ---------------------------------------------------------------------------
def _serve_video(path: Path, request: Request) -> Response:
    """
    Serve a video with HTTP Range support.

    Without ranges the browser cannot seek, and frame stepping — which is most
    of what this UI is for — silently degrades to "restart from zero".
    """
    if not path.exists():
        return JSONResponse(status_code=404, content={"code": "not_found",
                                                      "message": "Video not found."})
    size = path.stat().st_size
    media_type = mimetypes.guess_type(str(path))[0] or "video/mp4"
    range_header = request.headers.get("range")

    if not range_header or not range_header.startswith("bytes="):
        return FileResponse(path, media_type=media_type,
                            headers={"Accept-Ranges": "bytes"})

    raw = range_header[len("bytes="):].split(",")[0].strip()
    start_s, _, end_s = raw.partition("-")
    try:
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
    except ValueError:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    start = max(0, start)
    end = min(end, size - 1)
    if start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    length = end - start + 1

    def chunks(chunk_size: int = 1024 * 512):
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                data = fh.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        chunks(),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        },
    )


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------
@app.get("/api/reference")
def get_reference_meta():
    unavailable = _reference_unavailable()
    if unavailable:
        return unavailable

    ref = get_reference()
    df = ref.df
    series = {
        key: [round(float(v), 3) for v in ref.metric_series(key)]
        for key in config.METRICS
    }
    return {
        "frame_count": ref.frame_count,
        "fps": ref.fps,
        "duration_s": round(ref.frame_count / ref.fps, 3),
        "kick_side": ref.kick_side,
        "apex_frame": ref.apex,
        "active_start": ref.active_start,
        "active_end": ref.active_end,
        "detection_rate": ref.detection_rate,
        "mean_pelvis_tilt_conf": ref.mean_pelvis_tilt_conf,
        "phases": [
            {**b, "weight": config.PHASE_WEIGHTS.get(b["name"], 0.0)}
            for b in ref.phase_bounds
        ],
        "time_s": [round(float(t), 4) for t in df["time_s"].to_numpy()],
        "series": series,
        "tolerances": {
            k: {kk: round(vv, 3) for kk, vv in v.items()}
            for k, v in ref.tolerances.items()
        },
        "metrics": {
            k: {"label": m["label"], "weight": m["weight"], "column": m["column"]}
            for k, m in config.METRICS.items()
        },
        "landmarks": ref.landmarks,
        "phase_colors": config.PHASE_COLORS,
        "phase_border_colors": config.PHASE_BORDER_COLORS,
        "phase_legend": [{"name": n, "label": l} for n, l in config.PHASE_LEGEND],
        "phase_weights": config.PHASE_WEIGHTS,
        "extraction": dict(config.EXTRACTION),
        "warnings": ref.warnings,
    }


@app.get("/api/reference/video")
def reference_video(request: Request):
    return _serve_video(config.REFERENCE_VIDEO, request)


@app.get("/api/upload/{token}/video")
def upload_video(token: str, request: Request):
    with _uploads_lock:
        path = _uploads.get(token)
    if path is None:
        return JSONResponse(status_code=404,
                            content={"code": "not_found", "message": "Upload expired."})
    return _serve_video(path, request)


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/analyze")
async def analyze_upload(file: UploadFile = File(...)):
    unavailable = _reference_unavailable()
    if unavailable:
        return unavailable

    suffix = Path(file.filename or "upload.mp4").suffix.lower()
    if suffix not in config.ALLOWED_VIDEO_SUFFIXES:
        return JSONResponse(
            status_code=400,
            content={
                "code": "unsupported_format",
                "message": f"'{suffix or 'that file'}' is not a supported video container. "
                           f"Use one of: {', '.join(sorted(config.ALLOWED_VIDEO_SUFFIXES))}.",
            },
        )

    token = uuid.uuid4().hex
    dest = UPLOAD_DIR / f"{token}{suffix}"

    # Read the upload fully before streaming the response: the request body has
    # to be consumed while the connection is still in request phase.
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > config.UPLOAD_MAX_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    return JSONResponse(
                        status_code=413,
                        content={
                            "code": "too_large",
                            "message": f"Video exceeds the "
                                       f"{config.UPLOAD_MAX_BYTES // (1024*1024)} MB limit.",
                        },
                    )
                out.write(chunk)
    finally:
        await file.close()

    if size == 0:
        dest.unlink(missing_ok=True)
        return JSONResponse(status_code=400,
                            content={"code": "empty_file", "message": "The file was empty."})

    with _uploads_lock:
        _uploads[token] = dest

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    def work() -> None:
        """Runs the whole pipeline off the event loop; every exit path emits."""
        try:
            def progress(stage: str, pct: int) -> None:
                emit("progress", {"stage": stage, "pct": pct})

            df_b, summary_b, landmarks_b = run_extraction(dest, progress=progress)

            emit("progress", {"stage": "aligning", "pct": 85})
            ref = get_reference()
            result = analyze(ref, df_b, summary_b)

            emit("progress", {"stage": "scoring", "pct": 95})
            result["landmarks"] = landmarks_b
            result["upload_token"] = token
            result["video_url"] = f"/api/upload/{token}/video"
            result["filename"] = file.filename
            emit("result", result)
        except RefusalError as exc:
            emit("error", {"code": exc.code, "message": exc.message})
        except ExtractionError as exc:
            emit("error", {"code": "decode_failed", "message": str(exc)})
        except AlignmentError as exc:
            emit("error", {"code": "alignment_failed", "message": str(exc)})
        except ReferenceError as exc:
            emit("error", {"code": "reference_unavailable", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            emit("error", {"code": "internal_error",
                           "message": f"Analysis failed: {exc}"})
        finally:
            emit("__done__", {})

    async def stream():
        threading.Thread(target=work, daemon=True).start()
        while True:
            event, data = await queue.get()
            if event == "__done__":
                break
            yield _sse(event, data)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@app.get("/api/health")
def health():
    return {"ok": _REFERENCE_ERROR is None, "reference_error": _REFERENCE_ERROR}


# ---------------------------------------------------------------------------
# Static bundle (mounted last so it never shadows /api)
# ---------------------------------------------------------------------------
if (config.DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(config.DIST), html=True), name="dist")
else:
    @app.get("/")
    def _no_bundle():
        return JSONResponse(
            status_code=503,
            content={
                "code": "frontend_not_built",
                "message": "The frontend bundle is missing. Run `npm --prefix frontend run build`.",
            },
        )
