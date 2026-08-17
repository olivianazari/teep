/**
 * Timelines — two phase-coloured tracks with the warp ribbon between them.
 *
 * All custom SVG; none of this maps onto a Radix primitive.
 *
 * Both tracks share one real-time axis, so the ribbon's compression and
 * stretching *is* the timing error made visible. That is the whole point of
 * drawing it, and it is the clearest evidence that alignment is working.
 *
 * Styled from the Figma design (node 47:775): 29px bands with a 2px radius and
 * a darker outline, and a playhead drawn as a hairline capped with a dot at
 * each end — the geometry of the design's Line 77 asset.
 *
 * The athlete's track is on top and the reference beneath it, which is the
 * opposite of the video panes above (reference left, athlete right). Geometry
 * here is written in terms of Y_TOP / Y_BOTTOM rather than of A and B, so the
 * two can be swapped by reassigning yA and yB and nothing else.
 */

import { useLayoutEffect, useRef, useState } from 'react'
import type { AnalyzeResult, FeedbackItem, ReferenceData } from '@/lib/api'
import { APEX_PATH } from '@/lib/apex'
import { GRADE_POOR } from '@/lib/grade'
import { frameAtTime, phaseSpansFromFrames } from '@/lib/warp'

/**
 * Vertical geometry, measured off the design (node 47:775).
 *
 * Tracks sit 29px apart and the upper caption lives *inside* that gap rather
 * than below the track, pinned to the left edge. The warp ribbon therefore
 * shares the gap with the caption — they do not collide in practice, because
 * the caption is a few characters at x=0 and the ribbon's strands fan across
 * the middle and right of the span.
 */
const TRACK_H = 13
const RIBBON_H = 31
/** Track tops. Which series occupies which is decided in the component. */
const Y_TOP = 0
const Y_BOTTOM = TRACK_H + RIBBON_H
/** Caption centre lines — each sits below its own track. */
const LABEL_TOP_MID = 28.5
const LABEL_BOTTOM_MID = 72.5
/** Playhead stroke width, from the design's Line 77 asset. */
const PLAY_W = 2

/**
 * Adjustment marker (design node 94:637), drawn at each frame the improvements
 * panel refers to. Exported at 20x18; scaled to the apex star's weight so it
 * sits centred on a 13px band the same way, with a pixel to spare.
 */
const ALERT_PATH =
  'M8.9591 9.94708C8.9591 10.503 9.4252 10.9537 10.0002 10.9537C10.5752 10.9537 11.0413 10.503 11.0413 9.94708V6.92724C11.0413 6.3713 10.5752 5.92062 10.0002 5.92062C9.4252 5.92062 8.9591 6.3713 8.9591 6.92724V9.94708ZM11.0413 12.9557C11.0413 12.3997 10.5752 11.949 10.0002 11.949C9.4252 11.949 8.9591 12.3997 8.9591 12.9557V12.9669C8.9591 13.5229 9.4252 13.9735 10.0002 13.9735C10.5752 13.9735 11.0413 13.5229 11.0413 12.9669V12.9557ZM7.26977 1.55328C8.45979 -0.51776 11.5403 -0.51776 12.7303 1.55328L19.6025 13.5136C20.759 15.5264 19.2536 18 16.8722 18H3.12778C0.746319 18 -0.758998 15.5264 0.397532 13.5136L7.26977 1.55328Z'
/** Sized to the apex star's weight so both sit centred on a 13px band. */
const ALERT_W = 12
const ALERT_H = (ALERT_W / 20) * 18

/**
 * Visual gap between adjacent phase bands.
 *
 * Applied by insetting each band's fill by half the gap on either side — NOT by
 * shifting anything. Band position is time (x = t / tMax * width), so moving
 * bands apart would make them lie about when a phase happened. Insetting the
 * fill leaves the playhead, the warp ribbon and the scrub hit areas on true
 * time; only the painted rectangle is trimmed, by at most 2.5px a side.
 */
const PHASE_GAP = 5
/**
 * A band is never trimmed below this. `impact` is 2-3 frames by construction,
 * so on a long clip or a narrow window it can be only a few pixels wide and a
 * full gap would erase it entirely.
 */
const MIN_BAND_W = 3

type Track = 'a' | 'b'

interface HoverState {
  x: number
}

interface Props {
  reference: ReferenceData
  result: AnalyzeResult | null
  frameA: number
  frameB: number
  onSeekA: (frame: number) => void
  onSeekB: (frame: number) => void
}

export function Timelines({
  reference,
  result,
  frameA,
  frameB,
  onSeekA,
  onSeekB,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const el = hostRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const aTimes = reference.time_s
  const bTimes = result?.frames.map((f) => f.time_s) ?? []

  const aDur = aTimes[aTimes.length - 1] ?? 1
  const bDur = bTimes[bTimes.length - 1] ?? 0
  const tMax = Math.max(aDur, bDur) || 1

  // The athlete rides on top, the reference underneath.
  const yB = Y_TOP
  const yA = Y_BOTTOM
  const H = LABEL_BOTTOM_MID + 8
  const MID = Y_TOP + TRACK_H + RIBBON_H / 2

  const x = (t: number) => (t / tMax) * width

  const aSpans = reference.phases
  const bSpans = result ? phaseSpansFromFrames(result.frames) : []

  const fillOf = (name: string) => reference.phase_colors[name] ?? '#e4dee1'
  const strokeOf = (name: string) => reference.phase_border_colors?.[name] ?? '#cdc0c6'

  // A frame's span on the axis: from its own timestamp to the next frame's.
  const spanX = (times: number[], start: number, end: number) => {
    const t0 = times[start] ?? 0
    const t1 = times[Math.min(end + 1, times.length - 1)] ?? t0
    const x0 = x(t0)
    const x1 = Math.max(x(t1), x0 + 1)
    return { x0, w: x1 - x0 }
  }

  /** Cursor x relative to the track area. */
  const localX = (clientX: number) => {
    const el = hostRef.current
    return el ? clientX - el.getBoundingClientRect().left : 0
  }

  const timeAt = (clientX: number) =>
    Math.max(0, Math.min((localX(clientX) / Math.max(width, 1)) * tMax, tMax))

  const dragging = useRef<Track | null>(null)
  const [hover, setHover] = useState<HoverState | null>(null)
  /** Improvement tip shown while an adjustment marker is hovered. */
  const [tip, setTip] = useState<{ x: number; items: FeedbackItem[] } | null>(null)

  const seekAt = (clientX: number, times: number[], onSeek: (f: number) => void) => {
    if (!width || !times.length) return
    onSeek(frameAtTime(times, timeAt(clientX)))
  }

  const updateHover = (clientX: number) => {
    if (!width) return setHover(null)
    setHover({ x: localX(clientX) })
  }

  /**
   * Scrub handlers.
   *
   * Pointer capture rather than a window mousemove listener: it keeps the drag
   * alive when the cursor wanders off the track vertically — which it does
   * constantly on a 29px-tall target — and releases automatically.
   */
  const scrubHandlers = (track: Track, times: number[], onSeek: (f: number) => void) => ({
    onPointerDown: (e: React.PointerEvent<SVGRectElement>) => {
      e.currentTarget.setPointerCapture(e.pointerId)
      dragging.current = track
      seekAt(e.clientX, times, onSeek)
      updateHover(e.clientX)
    },
    onPointerMove: (e: React.PointerEvent<SVGRectElement>) => {
      updateHover(e.clientX)
      if (dragging.current === track) seekAt(e.clientX, times, onSeek)
    },
    onPointerUp: (e: React.PointerEvent<SVGRectElement>) => {
      dragging.current = null
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
    },
    // A cancelled gesture or a lost capture never delivers pointerup, which
    // would otherwise leave the drag flag set forever — and with it a frozen
    // hover guide that no longer follows anything.
    onPointerCancel: () => {
      dragging.current = null
      setHover(null)
    },
    onPointerLeave: (e: React.PointerEvent<SVGRectElement>) => {
      if (e.buttons === 0) dragging.current = null
      if (!dragging.current) setHover(null)
    },
  })

  // Half a stroke of inset, so the playhead is not clipped in half at frame 0.
  const clampPlay = (v: number) =>
    Math.min(Math.max(v, PLAY_W / 2), Math.max(width - PLAY_W / 2, PLAY_W / 2))
  const playA = clampPlay(x(aTimes[frameA] ?? 0))
  const playB = clampPlay(x(bTimes[frameB] ?? 0))

  // The cursor wins while hovering; otherwise the guide sits on the reference
  // playhead, which is the clock driving playback.
  const guideX = hover ? hover.x : playA

  // One marker per feedback item. Several items can land on the same frame, so
  // they are grouped and share a marker rather than stacking icons on top of
  // each other.
  const alerts = (() => {
    const byFrame = new Map<number, FeedbackItem[]>()
    for (const f of result?.feedback ?? []) {
      const at = byFrame.get(f.worst_frame)
      if (at) at.push(f)
      else byFrame.set(f.worst_frame, [f])
    }
    return [...byFrame.entries()].map(([frame, items]) => ({ frame, items }))
  })()

  const band = (
    spans: { name: string; start: number; end: number }[],
    times: number[],
    y: number,
  ) =>
    spans.map((s, i) => {
      const { x0, w } = spanX(times, s.start, s.end)
      // Never let the inset eat a short phase; shrink the gap instead.
      const inset = Math.max(0, Math.min(PHASE_GAP / 2, (w - MIN_BAND_W) / 2))
      return (
        <rect
          key={i}
          x={x0 + inset}
          y={y}
          width={Math.max(w - inset * 2, MIN_BAND_W)}
          height={TRACK_H}
          rx={2}
          fill={fillOf(s.name)}
          stroke={strokeOf(s.name)}
          strokeWidth={1}
        >
          <title>{`${s.name} · frames ${s.start}–${s.end}`}</title>
        </rect>
      )
    })

  return (
    <div ref={hostRef} className="relative w-full select-none">
      {width > 0 && (
        <svg width={width} height={H} className="block">
          {/* ---- warp ribbon ------------------------------------------ */}
          {result && (
            <g>
              {result.warp_path.map(([a, b], i) => {
                // Strands run from the upper track down to the lower one, so
                // the athlete's frame is the top endpoint and the reference's
                // the bottom. The control points below assume y1 < y2.
                const x1 = x(bTimes[b] ?? 0)
                const x2 = x(aTimes[a] ?? 0)
                const y1 = Y_TOP + TRACK_H
                const y2 = Y_BOTTOM
                const active = a === frameA || b === frameB
                return (
                  <path
                    key={i}
                    d={`M${x1},${y1} C${x1},${y1 + RIBBON_H * 0.45} ${x2},${
                      y2 - RIBBON_H * 0.45
                    } ${x2},${y2}`}
                    fill="none"
                    stroke="#323232"
                    strokeOpacity={active ? 0.75 : 0.14}
                    strokeWidth={active ? 1.4 : 1}
                  />
                )
              })}
            </g>
          )}

          {result ? (
            band(bSpans, bTimes, yB)
          ) : (
            <rect
              x={0}
              y={yB}
              width={width}
              height={TRACK_H}
              rx={2}
              fill="#e4dee1"
              stroke="#cdc0c6"
              opacity={0.5}
            />
          )}
          <text x={0} y={LABEL_TOP_MID + 3.5} className="fill-ink text-[10px] uppercase">
            you
          </text>

          {band(aSpans, aTimes, yA)}
          <text x={0} y={LABEL_BOTTOM_MID + 3.5} className="fill-ink text-[10px] uppercase">
            ideal
          </text>

          {/* ---- apex ticks -------------------------------------------
              The exported star from the design, centred on each apex. The
              scored impact window is apex ± 2 frames, wider than the 2–3 frame
              `impact` band the labeller emits; the tick is what reconciles the
              two visually. */}
          <ApexTick x={x(aTimes[reference.apex_frame] ?? 0)} y={yA} />
          {result && <ApexTick x={x(bTimes[result.apex_frame] ?? 0)} y={yB} />}

          {/* ---- hover / playback guide -------------------------------- */}
          <line
            pointerEvents="none"
            x1={guideX}
            x2={guideX}
            y1={Y_TOP}
            y2={Y_BOTTOM + TRACK_H}
            data-role="guide"
            stroke="#323232"
            strokeOpacity={hover ? 0.45 : 0.2}
            strokeWidth={1}
            strokeDasharray="3 3"
          />

          {/* ---- playhead ---------------------------------------------
              The design's Line 77 is now a plain 2px rule with no end caps. It
              is drawn there as one line spanning both tracks because the mockup
              has them at the same instant; with a real upload the two sit at
              different x, so each track carries its own. */}
          <g pointerEvents="none">
            <line
              x1={playA}
              x2={playA}
              y1={yA}
              y2={yA + TRACK_H}
              stroke="#323232"
              strokeWidth={PLAY_W}
            />
            {result && (
              <line
                x1={playB}
                x2={playB}
                y1={yB}
                y2={yB + TRACK_H}
                stroke="#323232"
                strokeWidth={PLAY_W}
              />
            )}
          </g>

          {/* ---- hit areas --------------------------------------------
              Each track claims its half of the block, meeting in the middle of
              the ribbon, so the whole strip is grabbable rather than two
              29px bands. */}
          {result && (
            <rect
              x={0}
              y={0}
              width={width}
              height={MID}
              fill="transparent"
              className="cursor-ew-resize"
              {...scrubHandlers('b', bTimes, onSeekB)}
            />
          )}
          {/* With no upload there is no athlete track to grab, so the
              reference takes the whole strip rather than leaving half of it
              dead. */}
          <rect
            x={0}
            y={result ? MID : 0}
            width={width}
            height={result ? H - MID : H}
            fill="transparent"
            className="cursor-ew-resize"
            {...scrubHandlers('a', aTimes, onSeekA)}
          />

          {/* ---- adjustment markers -----------------------------------
              One per feedback item, centred on the athlete's own track —
              these describe what *they* did, so they never appear on the
              reference. Rendered last so they sit above the scrub hit areas
              and stay clickable; everything else in this SVG is inert. */}
          {alerts.map(({ frame, items }) => {
            const cx = x(bTimes[frame] ?? 0)
            const scale = ALERT_W / 20
            return (
              <g
                key={frame}
                className="cursor-pointer"
                onClick={() => onSeekB(frame)}
                onPointerDown={(e) => e.stopPropagation()}
                onMouseEnter={() => setTip({ x: cx, items })}
                onMouseLeave={() => setTip(null)}
              >
                {/* Generous invisible hit area: the glyph itself is ~14px. */}
                <rect
                  x={cx - 11}
                  y={yB - 4}
                  width={22}
                  height={TRACK_H + 8}
                  fill="transparent"
                />
                <g
                  transform={`translate(${cx - ALERT_W / 2}, ${
                    yB + (TRACK_H - ALERT_H) / 2
                  }) scale(${scale})`}
                >
                  <path d={ALERT_PATH} fill="#BE0000" />
                </g>
              </g>
            )
          })}
        </svg>
      )}

      {/* ---- improvement tip -------------------------------------------
          HTML rather than SVG: it needs a backdrop blur and text wrapping,
          neither of which SVG gives cheaply. Positioned over the marker and
          clamped to the track, and inert to pointers so it can never steal the
          hover that is keeping it open. */}
      {tip && (
        <div
          className="pointer-events-none absolute z-10 flex w-[245px] flex-col gap-[15px] rounded-tip border border-white bg-white/25 p-[25px] backdrop-blur-[25px]"
          style={{
            left: Math.max(0, Math.min(tip.x - 122, Math.max(width - 245, 0))),
            bottom: H - yB + 8,
          }}
        >
          {tip.items.map((f, i) => {
            // Two variants in the design: "bad" below the red threshold,
            // "intermediate" above it. Same banding the metric cards use.
            const bad = f.score < GRADE_POOR
            return (
              <div key={i} className="flex flex-col gap-[15px]">
                <div
                  className={`flex items-center gap-[10px] rounded-chip p-[5px] ${
                    bad ? 'bg-[#fbd5d6]' : 'bg-[#ffdfb3]'
                  }`}
                >
                  <span className="text-[20px] leading-none text-ink">{f.metric_label}</span>
                  <span
                    className="size-[15px] shrink-0 rounded-band"
                    style={{ backgroundColor: bad ? '#ff4e51' : '#e48300' }}
                  />
                  <span className="text-[15px] leading-none tabular-nums text-ink">
                    {Math.round(f.score)}
                  </span>
                </div>
                <div className="flex flex-col gap-[10px]">
                  <span className="text-[10px] uppercase leading-none text-ink">
                    {f.phase}
                  </span>
                  <span className="text-[14px] leading-snug text-[#6b6b6b]">{f.text}</span>
                </div>
              </div>
            )
          })}
        </div>
      )}

    </div>
  )
}


/**
 * Apex marker, centred on the band rather than floating above it.
 *
 * Above-the-band placement clipped the reference star against the top of the
 * SVG, and at a 13px track height there is no room above a band anyway. The
 * exported star is 12x11, so it sits inside the band with a pixel to spare.
 */
function ApexTick({ x, y }: { x: number; y: number }) {
  return (
    <g pointerEvents="none" transform={`translate(${x - 6}, ${y + (TRACK_H - 11) / 2})`}>
      <path d={APEX_PATH} fill="#323232" />
    </g>
  )
}
