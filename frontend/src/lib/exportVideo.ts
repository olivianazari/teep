/**
 * Render a clip to a file with the pose skeleton burned in.
 *
 * The overlay is a canvas painted live over a <video>, so there is nothing on
 * disk to hand over — the composite only exists on screen. This replays the
 * clip into an offscreen canvas, drawing each frame plus its pose, and records
 * that canvas.
 *
 * Frames are *stepped*, not played. An earlier version simply called play() and
 * captured the stream in real time; that produced a 0.03s file whenever the tab
 * was not frontmost, because playback and requestVideoFrameCallback are both
 * throttled in a hidden document. Seeking is not, so stepping works whatever
 * the tab is doing — and a user is very likely to switch away during an export.
 *
 * The canvas stream is put in manual mode (`captureStream(0)`) and fed exactly
 * one frame per step via `requestFrame()`. MediaRecorder timestamps those by
 * wall clock, so each step is paced to the clip's own frame interval; without
 * that the output would be correct but play back at seek speed.
 */

import { drawPose } from './skeleton'

export interface ExportOptions {
  /** Source clip. Must already be loaded and same-origin. */
  src: string
  /** Per-frame landmark rows, or null to export the clip unchanged. */
  landmarks: number[][] | null
  /** Timestamp of every frame, in order. Drives the stepping. */
  times: number[]
  kickSide: string
  fps: number
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
}

export class ExportError extends Error {}

interface ManualCaptureTrack extends MediaStreamTrack {
  requestFrame?: () => void
}

/** MediaRecorder's format support varies; take the best the browser offers. */
function pickMimeType(): string | undefined {
  const candidates = [
    'video/mp4;codecs=avc1',
    'video/webm;codecs=vp9',
    'video/webm;codecs=vp8',
    'video/webm',
  ]
  return candidates.find(
    (t) => typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(t),
  )
}

export function exportExtension(mime: string | undefined): string {
  return mime?.startsWith('video/mp4') ? 'mp4' : 'webm'
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, Math.max(ms, 0)))

export async function renderWithSkeleton({
  src,
  landmarks,
  times,
  kickSide,
  fps,
  onProgress,
  signal,
}: ExportOptions): Promise<{ blob: Blob; mime: string | undefined }> {
  if (typeof MediaRecorder === 'undefined') {
    throw new ExportError('This browser cannot record video (MediaRecorder is missing).')
  }
  if (!times.length) throw new ExportError('The clip has no frames to export.')

  // A private element, so exporting cannot disturb the one being watched.
  const video = document.createElement('video')
  video.src = src
  video.muted = true
  video.playsInline = true
  video.preload = 'auto'

  await new Promise<void>((resolve, reject) => {
    video.onloadeddata = () => resolve()
    video.onerror = () => reject(new ExportError('Could not load the video for export.'))
  })

  const w = video.videoWidth
  const h = video.videoHeight
  if (!w || !h) throw new ExportError('The video reported no dimensions.')

  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new ExportError('Could not open a 2D drawing context.')

  const mime = pickMimeType()
  // 0 fps = manual mode: frames only reach the recorder via requestFrame().
  const stream = canvas.captureStream(0)
  const track = stream.getVideoTracks()[0] as ManualCaptureTrack
  if (!track?.requestFrame) {
    throw new ExportError('This browser cannot capture canvas frames on demand.')
  }

  const recorder = new MediaRecorder(stream, {
    ...(mime ? { mimeType: mime } : {}),
    videoBitsPerSecond: 8_000_000,
  })
  const chunks: BlobPart[] = []
  recorder.ondataavailable = (e) => {
    if (e.data.size) chunks.push(e.data)
  }
  const done = new Promise<Blob>((resolve, reject) => {
    recorder.onstop = () => resolve(new Blob(chunks, { type: mime ?? 'video/webm' }))
    recorder.onerror = () => reject(new ExportError('Recording failed.'))
  })

  // The whole picture fills the canvas — no letterboxing here, unlike the
  // on-screen overlay where the element is rarely the video's aspect ratio.
  const rect = { x: 0, y: 0, w, h }
  const half = 1 / (2 * fps)
  const interval = 1000 / fps

  const seekTo = (t: number) =>
    new Promise<void>((resolve) => {
      const onSeeked = () => {
        video.removeEventListener('seeked', onSeeked)
        resolve()
      }
      video.addEventListener('seeked', onSeeked)
      video.currentTime = t
    })

  recorder.start()
  const started = performance.now()

  try {
    for (let i = 0; i < times.length; i++) {
      if (signal?.aborted) break

      // Land mid-frame: seeking to an exact boundary can decode either side.
      await seekTo(times[i] + half)
      ctx.drawImage(video, 0, 0, w, h)
      const row = landmarks?.[i]
      if (row) drawPose(ctx, row, rect, kickSide)
      track.requestFrame()

      onProgress?.((i + 1) / times.length)
      // Hold the frame for its real duration so the file plays at true speed.
      await sleep(started + (i + 1) * interval - performance.now())
    }
  } finally {
    if (recorder.state !== 'inactive') recorder.stop()
    video.removeAttribute('src')
    video.load()
  }

  const blob = await done
  if (!blob.size) throw new ExportError('The export produced an empty file.')
  return { blob, mime }
}
