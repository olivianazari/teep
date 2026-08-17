import alertIcon from '@/assets/figma/alert.svg'
import type { ReferenceData } from '@/lib/api'
import { APEX_PATH } from '@/lib/apex'

/**
 * Phase key for the timeline.
 *
 * Lives outside Timelines because the design places it on the transport row
 * above the tracks rather than beneath them, so the two are siblings now.
 * Colours come from the backend (config.PHASE_COLORS), which is the single
 * source for the palette.
 */
export function PhaseLegend({ reference }: { reference: ReferenceData }) {
  const fill = (n: string) => reference.phase_colors[n] ?? '#e4dee1'
  const stroke = (n: string) => reference.phase_border_colors?.[n] ?? '#cdc0c6'

  return (
    <div className="flex flex-wrap items-center gap-x-[15px] gap-y-[6px]">
      {(reference.phase_legend ?? []).map(({ name, label }) => (
        <span key={name} className="flex items-center gap-[5px] text-[10px] text-ink">
          <span
            className="inline-block size-[15px] rounded-band border"
            style={{ backgroundColor: fill(name), borderColor: stroke(name) }}
          />
          {label}
        </span>
      ))}
      <span className="flex items-center gap-[5px] text-[10px] text-ink">
        <svg width="12" height="11" viewBox="0 0 12 11" aria-hidden>
          <path d={APEX_PATH} fill="#323232" />
        </svg>
        Apex
      </span>
      <span className="flex items-center gap-[5px] text-[10px] text-ink">
        <img src={alertIcon} alt="" width={12} height={10.8} />
        Bad form
      </span>
    </div>
  )
}
