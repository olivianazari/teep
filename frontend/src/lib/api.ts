/** Types and client for the FastAPI backend. */

export type MetricKey =
  | 'lead_hip_flexion'
  | 'lead_knee_angle'
  | 'torso_tilt'
  | 'rear_knee_angle'

export const METRIC_ORDER: MetricKey[] = [
  'lead_hip_flexion',
  'lead_knee_angle',
  'torso_tilt',
  'rear_knee_angle',
]

export interface PhaseSpan {
  name: string
  start: number
  end: number
  weight?: number
}

export interface ReferenceData {
  frame_count: number
  fps: number
  duration_s: number
  kick_side: string
  apex_frame: number
  active_start: number
  active_end: number
  detection_rate: number
  mean_pelvis_tilt_conf: number
  phases: PhaseSpan[]
  time_s: number[]
  series: Record<MetricKey, number[]>
  tolerances: Record<MetricKey, { range: number; full: number; zero: number }>
  metrics: Record<MetricKey, { label: string; weight: number; column: string }>
  phase_colors: Record<string, string>
  phase_border_colors: Record<string, string>
  /** Legend entries; ready/reset collapse into a single "Idle" row. */
  phase_legend: { name: string; label: string }[]
  phase_weights: Record<string, number>
  extraction: Record<string, string | number>
  warnings: string[]
  /** Per-frame pose landmarks for the overlay; null if they were never built. */
  landmarks: number[][] | null
}

export interface FrameMetric {
  value: number
  ref_value: number | null
  delta: number | null
  score: number | null
}

export interface FrameRow {
  frame: number
  time_s: number
  phase: string
  composite: number
  metrics: Record<MetricKey, FrameMetric>
}

export interface ScoredPhase {
  name: string
  start: number
  end: number
  frames: number
  score: number
  weight: number
  method: string
  metric_scores: Record<MetricKey, number>
}

export interface FeedbackItem {
  metric: MetricKey
  metric_label: string
  phase: string
  text: string
  delta: number
  score: number
  severity: number
  /** Frames of video_B this item refers to. */
  start_frame: number
  end_frame: number
  /** The single frame in that span where this metric is furthest off. */
  worst_frame: number
}

export interface AnalyzeResult {
  overall_score: number
  timing_score: number
  kick_side: string
  fps: number
  frame_count: number
  apex_frame: number
  phases: ScoredPhase[]
  dropped_phases: { name: string; frames: number }[]
  timing_phases: {
    name: string
    duration_a_s: number
    duration_b_s: number
    ratio: number
    deviation: number
    score: number
  }[]
  frames: FrameRow[]
  warp_path: [number, number][]
  feedback: FeedbackItem[]
  diagnostics: {
    detection_rate: number
    mean_pelvis_tilt_conf: number
    warnings: string[]
  }
  reference: {
    frame_count: number
    fps: number
    apex_frame: number
    kick_side: string
    active_start: number
    active_end: number
  }
  upload_token: string
  video_url: string
  filename: string
  /** Per-frame pose landmarks for the overlay. */
  landmarks: number[][] | null
}

export interface ProgressEvent {
  stage: string
  pct: number
}

export class ApiError extends Error {
  code: string
  constructor(code: string, message: string) {
    super(message)
    this.code = code
  }
}

const UNREACHABLE =
  'Cannot reach the local server. It may have been stopped — restart it with ' +
  '`uv run run.py` and try again.'

/**
 * fetch, with a transport failure turned into something a human can act on.
 *
 * A stopped backend otherwise surfaces as a bare "TypeError: Failed to fetch",
 * which tells the user nothing about what to do next.
 */
async function request(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init)
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError('server_unreachable', UNREACHABLE)
  }
}

export async function fetchReference(): Promise<ReferenceData> {
  const res = await request('/api/reference')
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(body.code ?? 'reference_unavailable', body.message ?? res.statusText)
  }
  return res.json()
}

/**
 * POST the upload and consume the SSE stream.
 *
 * EventSource only speaks GET, so the stream is parsed by hand off the fetch
 * body reader.
 */
export async function analyzeVideo(
  file: File,
  onProgress: (p: ProgressEvent) => void,
  signal?: AbortSignal,
): Promise<AnalyzeResult> {
  const form = new FormData()
  form.append('file', file)

  const res = await request('/api/analyze', { method: 'POST', body: form, signal })

  // Guard failures short-circuit before the stream starts and come back as JSON.
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(body.code ?? 'request_failed', body.message ?? res.statusText)
  }
  if (!res.body) throw new ApiError('no_stream', 'The server returned no response body.')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result: AnalyzeResult | null = null

  while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>
    try {
      chunk = await reader.read()
    } catch {
      // The connection dropped part-way through the analysis.
      throw new ApiError('server_unreachable', UNREACHABLE)
    }
    const { done, value } = chunk
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let split: number
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, split)
      buffer = buffer.slice(split + 2)

      let event = 'message'
      const dataLines: string[] = []
      for (const line of raw.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
      }
      if (!dataLines.length) continue
      const data = JSON.parse(dataLines.join('\n'))

      if (event === 'progress') onProgress(data as ProgressEvent)
      else if (event === 'result') result = data as AnalyzeResult
      else if (event === 'error') throw new ApiError(data.code, data.message)
    }
  }

  if (!result) throw new ApiError('no_result', 'Analysis ended without returning a result.')
  return result
}
