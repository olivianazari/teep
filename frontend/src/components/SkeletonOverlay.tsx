import { useCallback, useEffect, useRef } from 'react'
import { drawPose, videoContentRect } from '@/lib/skeleton'
import { frameAtTime } from '@/lib/warp'

/**
 * Pose skeleton painted over a <video>, one frame at a time.
 *
 * A canvas rather than SVG: this repaints on every frame of playback, and the
 * cost of reconciling ~40 SVG nodes 30 times a second is real where a single
 * canvas clear-and-stroke is not.
 */


interface Props {
  videoRef: React.RefObject<HTMLVideoElement | null>
  /** Per-frame flat rows, or null when the clip has no landmarks. */
  landmarks?: number[][] | null
  /** Timestamp of every landmark row, used to match the frame on screen. */
  times?: number[]
  /** Requested frame. Only a fallback — see the note on presentedTime. */
  frame: number
  kickSide: string
  show: boolean
}

export function SkeletonOverlay({
  videoRef,
  landmarks,
  times,
  frame,
  kickSide,
  show,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  /**
   * Media time of the frame the video has actually put on screen.
   *
   * The requested frame cannot be used to pick the landmark row. Setting
   * `currentTime` returns immediately but the picture changes tens of
   * milliseconds later, so drawing on the state change paints the new pose over
   * the old frame. The torso barely moves between frames and looks fine; the
   * kicking leg moves a long way and looks badly out of sync. Tracking what the
   * video has presented keeps the skeleton on the body it belongs to.
   */
  const presentedTime = useRef<number | null>(null)

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    const video = videoRef.current
    if (!canvas || !video) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Back the canvas at device resolution so the strokes are not soft on a
    // retina display, but keep drawing in CSS pixels.
    const dpr = window.devicePixelRatio || 1
    const cw = video.clientWidth
    const ch = video.clientHeight
    const wantW = Math.round(cw * dpr)
    const wantH = Math.round(ch * dpr)
    if (canvas.width !== wantW || canvas.height !== wantH) {
      canvas.width = wantW
      canvas.height = wantH
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, cw, ch)

    if (!show) return
    // Prefer the presented frame; fall back to the requested one before the
    // video has shown anything, or where requestVideoFrameCallback is missing.
    const t = presentedTime.current
    const index =
      t !== null && times && times.length ? frameAtTime(times, t) : frame
    const row = landmarks?.[index]
    if (!row) return
    const rect = videoContentRect(video)
    if (!rect) return

    drawPose(ctx, row, rect, kickSide)
  }, [videoRef, landmarks, times, frame, kickSide, show])

  useEffect(() => {
    draw()
  }, [draw])

  // Repaint whenever the video presents a frame, so the skeleton follows the
  // picture rather than the request. The loop is kept stable and reads the
  // latest draw through a ref; re-registering it on every frame change would
  // cancel and restart the callback thirty times a second.
  const drawRef = useRef(draw)
  useEffect(() => {
    drawRef.current = draw
  }, [draw])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    let cancelled = false
    let handle = 0

    // `seeked` covers the fallback path: it fires once a seek has completed and
    // the new frame is available, which is also all we get without rVFC.
    const onSeeked = () => {
      presentedTime.current = video.currentTime
      drawRef.current()
    }
    video.addEventListener('seeked', onSeeked)

    const hasRvfc = typeof video.requestVideoFrameCallback === 'function'
    if (hasRvfc) {
      const tick = (_now: number, meta: VideoFrameCallbackMetadata) => {
        if (cancelled) return
        presentedTime.current = meta.mediaTime
        drawRef.current()
        handle = video.requestVideoFrameCallback(tick)
      }
      handle = video.requestVideoFrameCallback(tick)
    }

    return () => {
      cancelled = true
      video.removeEventListener('seeked', onSeeked)
      if (hasRvfc && handle) video.cancelVideoFrameCallback(handle)
    }
  }, [videoRef])

  // The picture's position depends on the element box and on the video's own
  // dimensions, so redraw when either changes. videoWidth is 0 until metadata
  // lands, which would otherwise leave the first frame un-drawn.
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const observer = new ResizeObserver(draw)
    observer.observe(video)
    video.addEventListener('loadedmetadata', draw)
    video.addEventListener('loadeddata', draw)
    return () => {
      observer.disconnect()
      video.removeEventListener('loadedmetadata', draw)
      video.removeEventListener('loadeddata', draw)
    }
  }, [videoRef, draw])

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="pointer-events-none absolute inset-0 size-full"
    />
  )
}
