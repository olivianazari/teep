import { METRIC_ORDER } from '@/lib/api'
import type { AnalyzeResult, MetricKey, ReferenceData } from '@/lib/api'
import { GRADE_CLASS, GRADE_INK, gradeOf } from '@/lib/grade'
import { Sparkline } from './Sparkline'

/**
 * Score row — overall score, timing score, then the four metric cards.
 *
 * Overall and timing are deliberately two cards rather than one figure. They
 * measure different things (shape after alignment vs tempo from the warping
 * path) and §6.4 forbids merging them: alignment warps time away, so a slow
 * sloppy teep warps neatly onto a fast crisp one and scores well on shape. Each
 * carries its own grade tint, because a rep can genuinely be green on one and
 * red on the other — that combination is the point.
 *
 * Card order follows the Figma design (lead hip, lead knee, rear knee, torso),
 * which differs from METRIC_ORDER's weighting order.
 */
const CARD_ORDER: MetricKey[] = [
  'lead_hip_flexion',
  'lead_knee_angle',
  'rear_knee_angle',
  'torso_tilt',
]

function fmt(v: number | null | undefined, digits = 1) {
  return v === null || v === undefined || !Number.isFinite(v) ? '—' : v.toFixed(digits)
}

interface MetricCardProps {
  metricKey: MetricKey
  reference: ReferenceData
  result: AnalyzeResult | null
  frameA: number
  frameB: number
}

function MetricCard({ metricKey, reference, result, frameA, frameB }: MetricCardProps) {
  const meta = reference.metrics[metricKey]
  const aSeries = reference.series[metricKey]

  const cell = result?.frames[frameB]?.metrics[metricKey]
  const bValue = cell?.value ?? null

  // The ideal figure shown is the one the score was actually measured against,
  // which inside the warp window is the aligned frame and outside it is the
  // phase median. Showing A's value at the current A frame instead would put a
  // number on screen that the score does not reconcile with.
  const aValue = cell ? cell.ref_value : (aSeries?.[frameA] ?? null)

  // Both traces on one shared time axis and one shared value axis, so the gap
  // between them is read directly. Real elapsed time, not phase-normalised — a
  // slower rep genuinely runs further right.
  const aTimes = reference.time_s
  const bTimes = result?.frames.map((f) => f.time_s) ?? []
  const bSeries = result?.frames.map((f) => f.metrics[metricKey].value) ?? []
  const tMax = Math.max(aTimes[aTimes.length - 1] ?? 0, bTimes[bTimes.length - 1] ?? 0)

  // The grade colours the figure and its own trace; the reference trace stays
  // neutral so the two are still told apart at a glance.
  const grade = gradeOf(cell?.score)
  const gradeInk = GRADE_INK[grade]

  const traces = [
    { t: aTimes, v: aSeries ?? [], className: 'text-ink-muted/50', dashed: true },
  ]
  if (bSeries.length) {
    traces.push({ t: bTimes, v: bSeries, className: gradeInk, dashed: false })
  }

  return (
    <div className="flex flex-1 flex-col rounded-card bg-surface p-[25px]">
      {/* The design's text stack keeps its 10px rhythm; the sparkline is an
          addition to it, so it takes a tighter margin and a short height to
          cost the video panes as little as possible. */}
      <div className="flex flex-col gap-[10px]">
        <span className="text-[10px] uppercase leading-none text-ink">
          {meta?.label ?? metricKey}
        </span>
        <span className={`text-[24px] uppercase leading-none tabular-nums ${gradeInk}`}>
          {fmt(bValue)}°
        </span>
        <span className="text-[10px] uppercase leading-none text-ink">
          ideal {fmt(aValue)}°
        </span>
      </div>
      <div className="mt-[8px]">
        <Sparkline traces={traces} tMax={tMax} playheadT={aTimes[frameA]} height={16} />
      </div>
    </div>
  )
}

interface Props {
  reference: ReferenceData
  result: AnalyzeResult | null
  frameA: number
  frameB: number
}

export function AnalyticsStrip({ reference, result, frameA, frameB }: Props) {
  return (
    <div className="flex gap-[10px]">
      {/* Rep-level, not frame-level: kept out of the per-frame row and pinned to
          the leading edge, so it reads first. */}
      <div
        className={`flex w-[268px] shrink-0 flex-col gap-[10px] rounded-card border p-[25px] ${
          GRADE_CLASS[gradeOf(result?.overall_score)]
        }`}
      >
        <span className="text-[10px] uppercase leading-none text-ink">Overall score</span>
        <span className="text-[40px] uppercase leading-none tabular-nums text-ink">
          {result ? Math.round(result.overall_score) : 'xx'}
        </span>
      </div>

      {/* Tempo, scored from the warping path rather than from post-warp
          deviation. Narrower than the overall card: it is the secondary
          reading, and the metric cards should not lose more width than
          necessary. */}
      <div
        className={`flex w-[160px] shrink-0 flex-col gap-[10px] rounded-card border p-[25px] ${
          GRADE_CLASS[gradeOf(result?.timing_score)]
        }`}
        title="Tempo, measured from the time alignment. Kept separate from the overall score, which measures shape."
      >
        <span className="text-[10px] uppercase leading-none text-ink">Timing</span>
        <span className="text-[40px] uppercase leading-none tabular-nums text-ink">
          {result ? Math.round(result.timing_score) : 'xx'}
        </span>
      </div>

      {CARD_ORDER.filter((k) => METRIC_ORDER.includes(k)).map((key) => (
        <MetricCard
          key={key}
          metricKey={key}
          reference={reference}
          result={result}
          frameA={frameA}
          frameB={frameB}
        />
      ))}
    </div>
  )
}
