/**
 * The design's pane-header button (component sets 100:935, 104:1214, 100:947,
 * 100:953 — each with state=idle and state=hover).
 *
 * Two tones: `solid` is the dark primary used for upload, `subtle` is the light
 * grey used for export and the skeleton toggle. Both are 35px with a 10px
 * radius and a 5px inset around the glyph.
 *
 * Only the surface changes between states — the exported hover glyphs are
 * byte-identical to the idle ones, so one asset serves both and hover is pure
 * CSS. Disabled buttons keep the idle surface rather than picking up hover.
 */
interface Props {
  icon: string
  label: string
  tone?: 'solid' | 'subtle'
  /** Glyph edge in px. The design uses 18 for actions, 25 for the toggle. */
  glyph?: number
  pressed?: boolean
  disabled?: boolean
  onClick: () => void
}

export function IconButton({
  icon,
  label,
  tone = 'subtle',
  glyph = 18,
  pressed,
  disabled,
  onClick,
}: Props) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      className={`flex size-[35px] shrink-0 items-center justify-center rounded-[10px] p-[5px] transition-colors disabled:opacity-40 ${
        tone === 'solid'
          ? 'bg-ink enabled:hover:bg-[#545454]'
          : 'bg-[#f7f6f5] enabled:hover:bg-[#e5e2e0]'
      }`}
    >
      <img src={icon} alt="" width={glyph} height={glyph} />
    </button>
  )
}
