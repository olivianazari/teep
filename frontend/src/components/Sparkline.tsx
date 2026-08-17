/**
 * Sparkline — custom SVG. No shadcn equivalent exists for this.
 *
 * Both traces share one time axis and one value axis so the gap between them is
 * read directly. The x axis is real time, not phase-normalised: a slower rep
 * genuinely runs further to the right.
 */

interface Trace {
  t: number[]
  v: number[]
  className: string
  width?: number
  dashed?: boolean
}

interface Props {
  traces: Trace[]
  tMax: number
  playheadT?: number
  height?: number
}

export function Sparkline({ traces, tMax, playheadT, height = 34 }: Props) {
  const W = 100
  const H = height
  const pad = 2

  const all = traces.flatMap((tr) => tr.v).filter((v) => Number.isFinite(v))
  if (!all.length || tMax <= 0) return <div style={{ height: H }} />

  let lo = Math.min(...all)
  let hi = Math.max(...all)
  if (hi - lo < 1e-6) {
    lo -= 1
    hi += 1
  }

  const x = (t: number) => (t / tMax) * W
  const y = (v: number) => H - pad - ((v - lo) / (hi - lo)) * (H - 2 * pad)

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="w-full"
      style={{ height: H }}
      aria-hidden="true"
    >
      {traces.map((tr, i) => {
        const d = tr.t
          .map((t, j) => `${j === 0 ? 'M' : 'L'}${x(t).toFixed(2)},${y(tr.v[j]).toFixed(2)}`)
          .join(' ')
        return (
          <path
            key={i}
            d={d}
            fill="none"
            stroke="currentColor"
            className={tr.className}
            strokeWidth={tr.width ?? 1.2}
            strokeDasharray={tr.dashed ? '2 2' : undefined}
            vectorEffect="non-scaling-stroke"
            strokeLinejoin="round"
          />
        )
      })}
      {playheadT !== undefined && (
        <line
          x1={x(playheadT)}
          x2={x(playheadT)}
          y1={0}
          y2={H}
          stroke="currentColor"
          className="text-ink/40"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  )
}
