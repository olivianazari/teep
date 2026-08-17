/**
 * Warp-path helpers.
 *
 * The backend's warp path covers the active window only. Playback needs a
 * correspondence for every frame of the clip, including the `ready` and `reset`
 * stretches that sit outside it, so the ends are extended by constant offset
 * from the nearest aligned frame. Those stretches are someone standing still —
 * a fixed offset is honest there, and it keeps the mapping monotone.
 */

import type { AnalyzeResult, FrameRow, ReferenceData } from './api'

export interface FrameMap {
  aToB: number[]
  bToA: number[]
}

function clamp(v: number, lo: number, hi: number) {
  return v < lo ? lo : v > hi ? hi : v
}

export function buildFrameMap(
  warpPath: [number, number][],
  aCount: number,
  bCount: number,
): FrameMap {
  const aToBLists = new Map<number, number[]>()
  const bToALists = new Map<number, number[]>()
  for (const [a, b] of warpPath) {
    if (!aToBLists.has(a)) aToBLists.set(a, [])
    if (!bToALists.has(b)) bToALists.set(b, [])
    aToBLists.get(a)!.push(b)
    bToALists.get(b)!.push(a)
  }

  const mean = (xs: number[]) => Math.round(xs.reduce((s, x) => s + x, 0) / xs.length)

  const build = (
    lists: Map<number, number[]>,
    selfCount: number,
    otherCount: number,
  ): number[] => {
    const keys = [...lists.keys()].sort((x, y) => x - y)
    const out = new Array<number>(selfCount)
    if (!keys.length) {
      for (let i = 0; i < selfCount; i++) out[i] = clamp(i, 0, otherCount - 1)
      return out
    }
    const first = keys[0]
    const last = keys[keys.length - 1]
    const firstVal = mean(lists.get(first)!)
    const lastVal = mean(lists.get(last)!)

    for (let i = 0; i < selfCount; i++) {
      if (i < first) out[i] = clamp(firstVal - (first - i), 0, otherCount - 1)
      else if (i > last) out[i] = clamp(lastVal + (i - last), 0, otherCount - 1)
      else if (lists.has(i)) out[i] = clamp(mean(lists.get(i)!), 0, otherCount - 1)
      else {
        // Not every index inside the window is necessarily a path vertex.
        let prev = i
        while (prev > first && !lists.has(prev)) prev--
        out[i] = clamp(mean(lists.get(prev)!), 0, otherCount - 1)
      }
    }
    return out
  }

  return {
    aToB: build(aToBLists, aCount, bCount),
    bToA: build(bToALists, bCount, aCount),
  }
}

/**
 * Frame index for a timestamp.
 *
 * Keyed on time_s rather than array position: the pipeline resamples
 * variable-frame-rate input, after which row n no longer corresponds to decoded
 * frame n, and indexing by position would drift.
 */
export function frameAtTime(times: number[], t: number): number {
  if (!times.length) return 0
  let lo = 0
  let hi = times.length - 1
  if (t <= times[0]) return 0
  if (t >= times[hi]) return hi
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (times[mid] < t) lo = mid + 1
    else hi = mid
  }
  // Snap to whichever neighbour is nearer.
  if (lo > 0 && Math.abs(times[lo - 1] - t) <= Math.abs(times[lo] - t)) return lo - 1
  return lo
}

export function phaseSpansFromFrames(frames: FrameRow[]) {
  const spans: { name: string; start: number; end: number }[] = []
  let start = 0
  for (let i = 1; i <= frames.length; i++) {
    if (i === frames.length || frames[i].phase !== frames[start].phase) {
      spans.push({ name: frames[start].phase, start, end: i - 1 })
      start = i
    }
  }
  return spans
}

export function referenceTimes(ref: ReferenceData) {
  return ref.time_s
}

export function resultTimes(result: AnalyzeResult) {
  return result.frames.map((f) => f.time_s)
}
