import alertIcon from '@/assets/figma/alert.svg'
import type { AnalyzeResult } from '@/lib/api'
import { GRADE_POOR } from '@/lib/grade'

/**
 * The single worst deviation in the rep, at the top of the page.
 *
 * The timeline markers carry every yellow and red reading, but they are all the
 * same size and only give up their sentence on hover — so the one fault that
 * matters most is no more visible than the fifth. This says it outright.
 *
 * It shows one item, not a list, because the scorer is now worst-fault
 * dominant: `AGGREGATION_POWER` is what drove the overall down, and this is the
 * reading that drove it. Naming several would blur the connection between the
 * score and its cause.
 *
 * `feedback` arrives ranked by severity — deviation in tolerance units, weighted
 * by how much the metric and the phase actually matter — so item 0 is the one
 * to show. It is not simply the lowest score: a mild miss on a heavily weighted
 * metric outranks a catastrophic one on `rear_knee_angle` at 5%.
 *
 * The block renders in every state and holds its height. Vertical space is the
 * scarce dimension here and the video row absorbs all the slack, so a block that
 * appeared on result would shrink both videos at the moment the athlete wants to
 * look at them.
 */

interface Props {
  result: AnalyzeResult | null
  onSeek: (frame: number) => void
}

export function DeviationSummary({ result, onSeek }: Props) {
  const worst = result?.feedback?.[0]

  // Same two variants as the timeline's hover tip, keyed on the same threshold,
  // so a marker and this block never disagree about how bad a reading is.
  const bad = worst ? worst.score < GRADE_POOR : false
  const tint = !worst
    ? 'bg-surface'
    : bad
      ? 'bg-[#fbd5d6]'
      : 'bg-[#ffdfb3]'

  return (
    <div className={`shrink-0 rounded-card px-[25px] py-[18px] ${tint}`}>
      <div className="flex items-center gap-[15px]">
        <span className="shrink-0 text-[10px] uppercase leading-none text-ink">
          Biggest deviation
        </span>

        {worst ? (
          <button
            type="button"
            onClick={() => onSeek(worst.worst_frame)}
            title={`Jump to frame ${worst.worst_frame}`}
            className="flex min-w-0 flex-1 items-center gap-[10px] text-left"
          >
            <img src={alertIcon} alt="" width={15} height={14} className="shrink-0" />
            <span className="truncate text-[16px] leading-tight text-ink">
              {worst.text}
            </span>
            <span className="ml-auto flex shrink-0 items-center gap-[8px]">
              <span className="text-[10px] uppercase leading-none text-ink/60">
                {worst.phase}
              </span>
              <span
                className="size-[15px] shrink-0 rounded-band"
                style={{ backgroundColor: bad ? '#ff4e51' : '#e48300' }}
              />
              <span className="text-[15px] leading-none tabular-nums text-ink">
                {Math.round(worst.score)}
              </span>
            </span>
          </button>
        ) : (
          <span className="truncate text-[16px] leading-tight text-ink-muted">
            {result
              ? 'Nothing above threshold — every metric is in the green.'
              : 'Upload a teep to see where it differs most from the reference.'}
          </span>
        )}
      </div>
    </div>
  )
}
