/**
 * Score-to-grade banding.
 *
 *   green  100-80
 *   yellow  79-60
 *   red     59-0
 *
 * GRADE_GOOD is matched by the backend's FEEDBACK_SUPPRESS_ABOVE, so a card is
 * green exactly when the improvements panel stays silent about that metric.
 * Colour and written advice can never contradict each other. Keep the two in
 * step if either is retuned.
 */
export const GRADE_GOOD = 80
export const GRADE_POOR = 60

export type Grade = 'good' | 'mid' | 'poor' | 'none'

export function gradeOf(score: number | null | undefined): Grade {
  if (score === null || score === undefined || !Number.isFinite(score)) return 'none'
  if (score >= GRADE_GOOD) return 'good'
  if (score >= GRADE_POOR) return 'mid'
  return 'poor'
}

/**
 * Tinted card surfaces. Used by the overall-score box, which the design draws
 * as a filled card per grade.
 */
export const GRADE_CLASS: Record<Grade, string> = {
  good: 'bg-grade-good border-grade-good-edge',
  mid: 'bg-grade-mid border-grade-mid-edge',
  poor: 'bg-grade-poor border-grade-poor-edge',
  none: 'bg-surface border-transparent',
}

/**
 * Foreground colour per grade, for the four metric cards.
 *
 * Those keep the plain white surface of node 92:524 and signal the grade
 * through the figure and its sparkline instead of a fill. A good score is
 * deliberately left at the default ink — only a problem gets coloured, so
 * colour on the page always means "look here".
 */
export const GRADE_INK: Record<Grade, string> = {
  good: 'text-ink',
  mid: 'text-grade-mid-ink',
  poor: 'text-grade-poor-ink',
  none: 'text-ink',
}
