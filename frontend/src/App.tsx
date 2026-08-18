import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'
import exportIcon from '@/assets/figma/btn-export.svg'
import skeletonOffIcon from '@/assets/figma/btn-skeleton-off.svg'
import skeletonOnIcon from '@/assets/figma/btn-skeleton-on.svg'
import uploadBtnIcon from '@/assets/figma/btn-upload.svg'
import nextIcon from '@/assets/figma/transport-next.svg'
import playIcon from '@/assets/figma/transport-play.svg'
import playingIcon from '@/assets/figma/transport-playing.svg'
import prevIcon from '@/assets/figma/transport-prev.svg'
import uploadIcon from '@/assets/figma/upload-placeholder.svg'
import { AnalyticsStrip } from '@/components/AnalyticsStrip'
import { DeviationSummary } from '@/components/DeviationSummary'
import { IconButton } from '@/components/IconButton'
import { PhaseLegend } from '@/components/PhaseLegend'
import { Timelines } from '@/components/Timelines'
import { SkeletonOverlay } from '@/components/SkeletonOverlay'
import { UploadDialog } from '@/components/UploadDialog'
import { ApiError, fetchReference } from '@/lib/api'
import type { AnalyzeResult, ReferenceData } from '@/lib/api'
import { exportExtension, renderWithSkeleton } from '@/lib/exportVideo'
import { buildFrameMap, frameAtTime } from '@/lib/warp'

export default function App() {
  const [reference, setReference] = useState<ReferenceData | null>(null)
  const [refError, setRefError] = useState<string | null>(null)
  const [result, setResult] = useState<AnalyzeResult | null>(null)
  const [frameA, setFrameA] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  // The design puts a skeleton toggle in each pane's header rather than one in
  // a global header, so the two overlays are controlled independently.
  const [showSkeletonA, setShowSkeletonA] = useState(true)
  const [showSkeletonB, setShowSkeletonB] = useState(true)

  // Export renders in real time, so it needs a progress readout of its own.
  const [exporting, setExporting] = useState(false)
  const [exportPct, setExportPct] = useState(0)
  const [exportError, setExportError] = useState<string | null>(null)

  const videoA = useRef<HTMLVideoElement>(null)
  const videoB = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    fetchReference()
      .then(setReference)
      .catch((e) =>
        setRefError(e instanceof ApiError ? e.message : 'Could not load the reference.'),
      )
  }, [])

  const aTimes = reference?.time_s ?? []
  const bTimes = useMemo(() => result?.frames.map((f) => f.time_s) ?? [], [result])

  const frameMap = useMemo(() => {
    if (!reference || !result) return null
    return buildFrameMap(result.warp_path, reference.frame_count, result.frame_count)
  }, [reference, result])

  const frameB = frameMap ? (frameMap.aToB[frameA] ?? 0) : 0

  /**
   * How many reference frames land on the current frame of B.
   *
   * Where the athlete is faster than the reference, several reference frames
   * map onto one of theirs and their video legitimately holds still while the
   * reference plays on. That reads as a stalled player unless it is labelled,
   * so the count is surfaced rather than left to look like a bug.
   */
  const holdRun = useMemo(() => {
    if (!frameMap) return 1
    let n = 0
    for (const b of frameMap.aToB) if (b === frameB) n++
    return n
  }, [frameMap, frameB])

  const halfFrameA = reference ? 1 / (2 * reference.fps) : 0
  const halfFrameB = result ? 1 / (2 * result.fps) : 0

  /** Canonical seek: drive A, and let the frameB effect carry B along. */
  const seekA = useCallback(
    (frame: number) => {
      if (!aTimes.length) return
      const f = Math.max(0, Math.min(frame, aTimes.length - 1))
      setFrameA(f)
      const v = videoA.current
      if (v) v.currentTime = aTimes[f] + halfFrameA
    },
    [aTimes, halfFrameA],
  )

  /** Stepping B maps back through the warp path to the corresponding A frame. */
  const seekB = useCallback(
    (frame: number) => {
      if (!frameMap || !bTimes.length) return
      const f = Math.max(0, Math.min(frame, bTimes.length - 1))
      seekA(frameMap.bToA[f] ?? 0)
    },
    [frameMap, bTimes, seekA],
  )

  // B is time-remapped through the warping path so both videos always show the
  // same phase. Tempo error is not lost by doing this — it is reported
  // separately as timing_score.
  useEffect(() => {
    const v = videoB.current
    if (!v || !bTimes.length) return
    const target = bTimes[frameB] + halfFrameB
    if (Math.abs(v.currentTime - target) > halfFrameB) v.currentTime = target
  }, [frameB, bTimes, halfFrameB])

  // Playback is driven off A's real presented frames rather than a timer, so
  // the readout matches what is actually on screen.
  useEffect(() => {
    const v = videoA.current
    if (!v || !playing || !aTimes.length) return
    let handle = 0
    let cancelled = false
    const tick = () => {
      if (cancelled) return
      setFrameA(frameAtTime(aTimes, v.currentTime))
      handle = v.requestVideoFrameCallback(tick)
    }
    handle = v.requestVideoFrameCallback(tick)
    return () => {
      cancelled = true
      v.cancelVideoFrameCallback(handle)
    }
  }, [playing, aTimes])

  const togglePlay = useCallback(() => {
    const v = videoA.current
    if (!v) return
    if (playing) {
      v.pause()
      setPlaying(false)
    } else {
      if (aTimes.length && frameA >= aTimes.length - 1) seekA(0)
      void v.play()
      setPlaying(true)
    }
  }, [playing, frameA, aTimes, seekA])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return
      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        setPlaying(false)
        videoA.current?.pause()
        seekA(frameA - 1)
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        setPlaying(false)
        videoA.current?.pause()
        seekA(frameA + 1)
      } else if (e.key === ' ') {
        e.preventDefault()
        togglePlay()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [frameA, seekA, togglePlay])

  /**
   * Save the clip with the pose skeleton burned in.
   *
   * The overlay only exists as a canvas painted over the video, so the file has
   * to be re-rendered rather than copied. renderWithSkeleton replays the clip
   * offscreen and records the composite; it uses the same drawing routine as
   * the on-screen overlay, so the export matches what was watched.
   */
  const exportVideo = useCallback(async () => {
    if (!result || exporting) return
    setExporting(true)
    setExportPct(0)
    try {
      const { blob, mime } = await renderWithSkeleton({
        src: result.video_url,
        landmarks: result.landmarks,
        times: bTimes,
        kickSide: result.kick_side,
        fps: result.fps,
        onProgress: (f) => setExportPct(Math.round(f * 100)),
      })
      const stem = (result.filename || 'teep').replace(/\.[^.]+$/, '')
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${stem}-skeleton.${exportExtension(mime)}`
      document.body.appendChild(a)
      a.click()
      a.remove()
      // Revoke on the next turn: revoking synchronously can cancel the download
      // before the browser has taken hold of the blob.
      setTimeout(() => URL.revokeObjectURL(url), 10_000)
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export failed.')
    } finally {
      setExporting(false)
    }
  }, [result, exporting, bTimes])

  const onResult = useCallback((r: AnalyzeResult) => {
    // Replaces B without a page reload; frame state resets so no stale metrics
    // linger in the analytics strip.
    setResult(r)
    setFrameA(0)
    setPlaying(false)
    const a = videoA.current
    if (a) {
      a.pause()
      a.currentTime = 0
    }
  }, [])

  if (refError) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <Alert variant="destructive">
          <AlertCircle className="size-4" />
          <AlertTitle>Reference unavailable</AlertTitle>
          <AlertDescription className="whitespace-pre-wrap">{refError}</AlertDescription>
        </Alert>
      </div>
    )
  }

  if (!reference) {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-44 w-full" />
        <Skeleton className="h-80 w-full" />
      </div>
    )
  }

  const phaseNow = result?.frames[frameB]?.phase ?? '—'
  const diag = result?.diagnostics
  const warnings = [
    ...reference.warnings,
    ...(diag?.warnings ?? []),
    ...(exportError ? [exportError] : []),
  ]

  return (
    <div className="flex h-dvh flex-col gap-[10px] overflow-hidden bg-canvas p-[30px]">
      {/* ---- warnings ----
          Reference-level warnings are shown alongside the per-upload ones.
          Stale landmarks disable the overlay, and without this the skeleton
          would just silently not appear — the message names the command that
          fixes it. */}
      {warnings.length > 0 && (
        <Alert className="shrink-0 py-2">
          <AlertCircle className="size-4" />
          <AlertDescription>
            <ul className="list-inside list-disc text-xs">
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      {/* ---- biggest deviation ----
          The one reading that drove the score down, said outright. The timeline
          markers carry every yellow and red pair, but they are all the same size
          and only give up their sentence on hover, so the worst is no more
          visible than the fifth. */}
      <DeviationSummary
        result={result}
        onSeek={(f) => {
          setPlaying(false)
          videoA.current?.pause()
          seekB(f)
        }}
      />

      {/* ---- videos ----
          Every item is also reachable from the alert markers on the timeline,
          which carry the same sentence and jump to the same frame. */}
      <div className="grid min-h-0 flex-1 grid-cols-2 gap-[10px]">
        <VideoPane
          label="you"
          sublabel={
            result
              ? `frame ${frameB} · ${phaseNow}${holdRun > 2 ? ` · holding ${holdRun} ref frames` : ''}`
              : ''
          }
          videoRef={videoB}
          src={result?.video_url}
          actions={
            <>
              <IconButton
                icon={uploadBtnIcon}
                label="Upload a teep"
                tone="solid"
                onClick={() => setUploadOpen(true)}
              />
              <IconButton
                icon={exportIcon}
                label={
                  exporting
                    ? `Rendering… ${exportPct}%`
                    : result
                      ? 'Export video with skeleton'
                      : 'Upload a teep first'
                }
                disabled={!result || exporting}
                onClick={() => void exportVideo()}
              />
              <IconButton
                icon={showSkeletonB ? skeletonOnIcon : skeletonOffIcon}
                label={showSkeletonB ? 'Hide pose skeleton' : 'Show pose skeleton'}
                glyph={25}
                pressed={showSkeletonB}
                onClick={() => setShowSkeletonB((s) => !s)}
              />
            </>
          }
          overlay={
            result && (
              <SkeletonOverlay
                videoRef={videoB}
                landmarks={result.landmarks}
                times={bTimes}
                frame={frameB}
                kickSide={result.kick_side}
                show={showSkeletonB}
              />
            )
          }
          placeholder={
            <button
              onClick={() => setUploadOpen(true)}
              className="flex size-full flex-col items-center justify-center gap-[25px] text-[10px] uppercase text-ink-muted"
            >
              <img src={uploadIcon} alt="" width={18} height={18} />
              Upload teep to compare
            </button>
          }
        />
        <VideoPane
          label="ideal"
          sublabel={`frame ${frameA}`}
          videoRef={videoA}
          src="/api/reference/video"
          onEnded={() => setPlaying(false)}
          actions={
            <IconButton
              icon={showSkeletonA ? skeletonOnIcon : skeletonOffIcon}
              label={showSkeletonA ? 'Hide pose skeleton' : 'Show pose skeleton'}
              glyph={25}
              pressed={showSkeletonA}
              onClick={() => setShowSkeletonA((s) => !s)}
            />
          }
          overlay={
            <SkeletonOverlay
              videoRef={videoA}
              landmarks={reference.landmarks}
              times={aTimes}
              frame={frameA}
              kickSide={reference.kick_side}
              show={showSkeletonA}
            />
          }
        />
      </div>

      {/* ---- score row ----
          Below the videos in this revision, so the footage gets the top of the
          page and the numbers read as a summary of what you just watched. */}
      <div className="shrink-0">
        <AnalyticsStrip
          reference={reference}
          result={result}
          frameA={frameA}
          frameB={frameB}
        />
      </div>

      {/* ---- timelines + transport ---- */}
      <div className="shrink-0 rounded-card bg-surface px-[30px] pb-[22px] pt-[23px]">
        {/* One row above the tracks: the phase key sits at the leading edge and
            the transport is centred on the card independently of it, so the
            play button stays on the card's centre line however wide the key
            grows. The prev/play/next glyphs are the design's own exported
            artwork, split out of the single strip it was drawn as. */}
        <div className="relative mb-[20px] flex min-h-[18px] items-center">
          <PhaseLegend reference={reference} />
          <div className="absolute left-1/2 flex -translate-x-1/2 items-center gap-[50px] text-ink">
          <button
            type="button"
            aria-label="Previous frame"
            onClick={() => {
              setPlaying(false)
              videoA.current?.pause()
              seekA(frameA - 1)
            }}
          >
            <img src={prevIcon} alt="" width={19} height={19} />
          </button>
          <button type="button" onClick={togglePlay} aria-label="Play or pause">
            {/* The design now exports a playing-state glyph (a pause bar pair),
                so the lucide stand-in is gone. */}
            {playing ? (
              <img src={playingIcon} alt="" width={14} height={18} />
            ) : (
              <img src={playIcon} alt="" width={17} height={19} />
            )}
          </button>
          <button
            type="button"
            aria-label="Next frame"
            onClick={() => {
              setPlaying(false)
              videoA.current?.pause()
              seekA(frameA + 1)
            }}
          >
            <img src={nextIcon} alt="" width={19} height={19} />
          </button>
          </div>
        </div>

        <Timelines
          reference={reference}
          result={result}
          frameA={frameA}
          frameB={frameB}
          onSeekA={(f) => {
            setPlaying(false)
            videoA.current?.pause()
            seekA(f)
          }}
          onSeekB={(f) => {
            setPlaying(false)
            videoA.current?.pause()
            seekB(f)
          }}
        />
      </div>

      <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} onResult={onResult} />
    </div>
  )
}

interface PaneProps {
  label: string
  sublabel: string
  videoRef: React.RefObject<HTMLVideoElement | null>
  src?: string
  placeholder?: React.ReactNode
  onEnded?: () => void
  /** Drawn on top of the video, sized to the letterboxed content box. */
  overlay?: React.ReactNode
  /** Controls pinned to the right of the pane's header. */
  actions?: React.ReactNode
}

function VideoPane({
  label,
  sublabel,
  videoRef,
  src,
  placeholder,
  onEnded,
  overlay,
  actions,
}: PaneProps) {
  return (
    <div className="flex min-h-0 flex-col overflow-hidden rounded-card bg-surface p-[25px]">
      {/* The frame/phase readout sits next to the label rather than opposite it:
          the right edge now belongs to the controls. The generous bottom gap
          keeps the 35px buttons clear of the video's top edge — at 10px they
          sat almost flush against it. */}
      <div className="mb-[20px] flex h-[35px] shrink-0 items-center gap-[10px]">
        <span className="text-[10px] uppercase leading-none text-ink-muted">{label}</span>
        <span className="min-w-0 flex-1 truncate text-[10px] uppercase leading-none tabular-nums text-ink-muted">
          {sublabel}
        </span>
        {actions}
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center">
        {src ? (
          <>
            <video
              ref={videoRef}
              src={src}
              preload="auto"
              playsInline
              muted
              onEnded={onEnded}
              className="size-full object-contain"
            />
            {overlay}
          </>
        ) : (
          placeholder
        )}
      </div>
    </div>
  )
}
