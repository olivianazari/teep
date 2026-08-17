/**
 * Geometry and colour for the on-video pose overlay.
 *
 * The landmarks are BlazePose's 33-point model in normalised image coordinates,
 * exactly as the backend measured them (see backend/landmarks.py). Each frame
 * arrives flat: [x0, y0, v0, x1, y1, v1, ...].
 */

export const STRIDE = 3

/** BlazePose indices this overlay cares about. */
export const LM = {
  lShoulder: 11,
  rShoulder: 12,
  lElbow: 13,
  rElbow: 14,
  lWrist: 15,
  rWrist: 16,
  lHip: 23,
  rHip: 24,
  lKnee: 25,
  rKnee: 26,
  lAnkle: 27,
  rAnkle: 28,
  lHeel: 29,
  rHeel: 30,
  lFoot: 31,
  rFoot: 32,
} as const

/**
 * Bones to draw. Deliberately excludes the face points (0-10): a coaching
 * overlay needs the torso and limbs, and skipping the face keeps the drawing
 * legible instead of a dot cluster around the head.
 */
export const POSE_CONNECTIONS: Array<[number, number]> = [
  [11, 12], // shoulders
  [11, 13],
  [13, 15], // left arm
  [12, 14],
  [14, 16], // right arm
  [11, 23],
  [12, 24], // torso sides
  [23, 24], // hips
  [23, 25],
  [25, 27], // left leg
  [27, 29],
  [29, 31],
  [27, 31], // left foot
  [24, 26],
  [26, 28], // right leg
  [28, 30],
  [30, 32],
  [28, 32], // right foot
]

/**
 * Limb colours are functional, not decorative — they encode which leg is doing
 * the kicking — so like the phase colours they are the sanctioned exception to
 * staying on default shadcn tokens. Defined here only, so they can be swapped
 * wholesale.
 */
export const SKELETON_COLORS = {
  // From the design's skeleton-colours frame (node 111:1251). They reuse the
  // phase palette deliberately, so a limb on the video and a band on the
  // timeline speak the same visual language.
  lead: '#c8e9ef', // kicking leg
  rear: '#ffdfb3', // support leg
  trunk: '#e6dfe0', // body and arms
  // These are pale by design, so the dark underlay is doing more work than
  // before — without it the skeleton disappears against light footage.
  halo: 'rgba(2, 6, 23, 0.75)',
} as const

// Explicitly Set<number>: LM is `as const`, so without this these infer as sets
// of literal types, and picking between them with a ternary narrows `.has()`'s
// parameter to the intersection of the two literal unions — which is `never`.
const LEFT_LEG = new Set<number>([LM.lHip, LM.lKnee, LM.lAnkle, LM.lHeel, LM.lFoot])
const RIGHT_LEG = new Set<number>([LM.rHip, LM.rKnee, LM.rAnkle, LM.rHeel, LM.rFoot])

/**
 * The joints that are actually vertices of a scored angle — both hips, knees
 * and ankles, plus the shoulders that define the trunk vector for torso tilt.
 * Drawn larger, because these are the points the four metrics are computed from.
 */
export const SCORED_JOINTS = new Set<number>([
  LM.lShoulder,
  LM.rShoulder,
  LM.lHip,
  LM.rHip,
  LM.lKnee,
  LM.rKnee,
  LM.lAnkle,
  LM.rAnkle,
])

/** Every landmark touched by a drawn bone. */
export const DRAWN_JOINTS: number[] = [...new Set(POSE_CONNECTIONS.flat())].sort(
  (a, b) => a - b,
)

/** Colour for one landmark, given which side is kicking. */
export function jointColor(index: number, kickSide: string): string {
  const leadIsLeft = kickSide.toLowerCase() === 'left'
  const lead = leadIsLeft ? LEFT_LEG : RIGHT_LEG
  const rear = leadIsLeft ? RIGHT_LEG : LEFT_LEG
  if (lead.has(index)) return SKELETON_COLORS.lead
  if (rear.has(index)) return SKELETON_COLORS.rear
  return SKELETON_COLORS.trunk
}

/** Colour for a bone: a limb's own colour, or neutral where it crosses over. */
export function boneColor(a: number, b: number, kickSide: string): string {
  const ca = jointColor(a, kickSide)
  const cb = jointColor(b, kickSide)
  return ca === cb ? ca : SKELETON_COLORS.trunk
}

export interface ContentRect {
  x: number
  y: number
  w: number
  h: number
}

/**
 * Where the picture actually sits inside a `object-contain` <video> box.
 *
 * The element is almost never the video's aspect ratio, so it letterboxes.
 * Landmarks are normalised against the *picture*, not the element, and painting
 * them onto the element box would slide the skeleton off the body by exactly the
 * size of the bars.
 */
export function videoContentRect(video: HTMLVideoElement): ContentRect | null {
  const { videoWidth: vw, videoHeight: vh, clientWidth: cw, clientHeight: ch } = video
  if (!vw || !vh || !cw || !ch) return null
  const scale = Math.min(cw / vw, ch / vh)
  const w = vw * scale
  const h = vh * scale
  return { x: (cw - w) / 2, y: (ch - h) / 2, w, h }
}

/** Below this visibility a landmark is guesswork and is not drawn at all. */
export const VIS_HIDE = 0.35
/** At or above this it is drawn fully opaque; between the two it fades in. */
export const VIS_SOLID = 0.75

export function alphaFor(vis: number): number {
  if (vis >= VIS_SOLID) return 1
  return Math.max(0, (vis - VIS_HIDE) / (VIS_SOLID - VIS_HIDE))
}

/**
 * Paint one pose row into a 2D context, inside `rect`.
 *
 * Shared by the on-screen overlay and the video export so the two can never
 * drift apart — an exported clip shows exactly the skeleton that was on screen.
 * The caller owns the transform and any clearing; this only draws.
 */
export function drawPose(
  ctx: CanvasRenderingContext2D,
  row: number[],
  rect: ContentRect,
  kickSide: string,
): void {
  const px = (i: number) => rect.x + row[i * STRIDE] * rect.w
  const py = (i: number) => rect.y + row[i * STRIDE + 1] * rect.h
  const vis = (i: number) => row[i * STRIDE + 2]

  // Weights scale off the picture, not the element, so the skeleton keeps its
  // proportions whether it is drawn into a 400px pane or a 1080p export.
  const bone = Math.max(1.5, rect.w * 0.005)
  const dot = Math.max(2, rect.w * 0.007)

  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'

  // Two passes: a dark halo underneath, then the coloured bone. Without the
  // halo the skeleton disappears wherever the footage is bright.
  for (const pass of ['halo', 'color'] as const) {
    for (const [a, b] of POSE_CONNECTIONS) {
      const alpha = alphaFor(Math.min(vis(a), vis(b)))
      if (alpha <= 0) continue
      ctx.globalAlpha = pass === 'halo' ? alpha * 0.8 : alpha
      ctx.strokeStyle = pass === 'halo' ? SKELETON_COLORS.halo : boneColor(a, b, kickSide)
      ctx.lineWidth = pass === 'halo' ? bone + 2.5 : bone
      ctx.beginPath()
      ctx.moveTo(px(a), py(a))
      ctx.lineTo(px(b), py(b))
      ctx.stroke()
    }
  }

  for (const i of DRAWN_JOINTS) {
    const alpha = alphaFor(vis(i))
    if (alpha <= 0) continue
    const r = SCORED_JOINTS.has(i) ? dot * 1.6 : dot
    ctx.globalAlpha = alpha
    ctx.beginPath()
    ctx.arc(px(i), py(i), r, 0, Math.PI * 2)
    ctx.fillStyle = jointColor(i, kickSide)
    ctx.fill()
    // A dark rim keeps adjacent joints from merging into one blob.
    ctx.lineWidth = Math.max(1, bone * 0.35)
    ctx.strokeStyle = SKELETON_COLORS.halo
    ctx.stroke()
  }

  ctx.globalAlpha = 1
}
