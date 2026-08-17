import { useCallback, useRef, useState } from 'react'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import load1 from '@/assets/figma/load-1.svg'
import load2 from '@/assets/figma/load-2.svg'
import load3 from '@/assets/figma/load-3.svg'
import load4 from '@/assets/figma/load-4.svg'
import uploadCloud from '@/assets/figma/upload-cloud.svg'
import { ApiError, analyzeVideo } from '@/lib/api'
import type { AnalyzeResult } from '@/lib/api'

const LOADERS = [load1, load2, load3, load4]

/**
 * Loading state (design node 104:1094).
 *
 * The four figures are static exports; the animation is a staggered pulse over
 * them. No stage caption — the design's bottom block is an empty 35px spacer,
 * so the animation carries the wait on its own.
 */
function ScoringAnimation() {
  return (
    <div className="flex flex-col items-center gap-[50px] pt-[30px]">
      <p className="text-[24px] leading-[28px] text-ink">Scoring your teep…</p>
      <div className="flex items-start gap-[10px]">
        {LOADERS.map((src, i) => (
          <img
            key={i}
            src={src}
            alt=""
            width={92.646}
            height={110.94}
            className="animate-teep-kick"
            style={{ animationDelay: `${i * 160}ms` }}
          />
        ))}
      </div>
      <div className="h-[35px] w-full" />
    </div>
  )
}

const ERROR_TITLE: Record<string, string> = {
  kick_side_mismatch: 'Wrong kicking leg',
  no_kick_detected: 'No teep detected',
  server_unreachable: 'Server not running',
  unsupported_format: 'Unsupported file type',
  too_large: 'File too large',
  decode_failed: 'Could not read this video',
  reference_unavailable: 'Reference unavailable',
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  onResult: (result: AnalyzeResult, file: File) => void
}

export function UploadDialog({ open, onOpenChange, onResult }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const run = useCallback(
    async (file: File) => {
      setBusy(true)
      setError(null)
      try {
        // Progress is not displayed in this design, but the callback keeps
        // the SSE stream consumed rather than buffering.
        const result = await analyzeVideo(file, () => {})
        onResult(result, file)
        onOpenChange(false)
      } catch (err) {
        if (err instanceof ApiError) setError({ code: err.code, message: err.message })
        else setError({ code: 'unknown', message: String(err) })
      } finally {
        setBusy(false)
      }
    },
    [onResult, onOpenChange],
  )

  const pick = (files: FileList | null) => {
    const file = files?.[0]
    if (file) void run(file)
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <DialogContent
        showCloseButton={!busy}
        className="gap-[25px] rounded-tip border-[#dcdcdc] p-0 pt-[25px] sm:max-w-[556px]"
      >
        {busy ? (
          <>
            <DialogTitle className="sr-only">Scoring your teep</DialogTitle>
            <ScoringAnimation />
          </>
        ) : (
          <>
            <div className="flex w-full flex-col items-center gap-[10px]">
              <div className="flex w-full justify-center px-[50px] pt-[24px]">
                <DialogTitle className="text-[24px] leading-[28px] text-ink">
                  Upload a teep
                </DialogTitle>
              </div>
              <div className="flex w-full justify-center px-[50px]">
                <p className="text-center text-[12px] leading-[18px] text-[#8a8a8a]">
                  Full body, fixed camera, side profile, left kick stance.
                </p>
              </div>
            </div>

            <div className="flex w-full flex-col items-center px-[50px] py-[10px]">
              <div
                role="button"
                tabIndex={0}
                onClick={() => inputRef.current?.click()}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click()
                }}
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragging(false)
                  pick(e.dataTransfer.files)
                }}
                className={`flex w-full cursor-pointer flex-col items-center gap-[12px] rounded-[12px] border-2 border-dashed px-[25px] py-[50px] transition-colors ${
                  dragging ? 'border-ink bg-[#f7f6f5]' : 'border-[#cdc0c6] bg-white'
                }`}
              >
                <img src={uploadCloud} alt="" width={20} height={20} />
                <div className="flex flex-col items-center gap-[4px]">
                  <span className="text-[14px] leading-[20px] text-ink">
                    Drop video or click to upload
                  </span>
                  <span className="text-center text-[12px] leading-[18px] text-[#8a8a8a]">
                    .mp4, .mov, .avi, .mkv, or .webm
                  </span>
                </div>
                <input
                  ref={inputRef}
                  type="file"
                  accept="video/mp4,video/quicktime,video/x-m4v,video/x-msvideo,video/x-matroska,video/webm,.mp4,.mov,.m4v,.avi,.mkv,.webm"
                  className="hidden"
                  onChange={(e) => pick(e.target.files)}
                />
              </div>
            </div>

            {error ? (
              <div className="w-full px-[50px] pb-[25px]">
                <p className="text-[14px] leading-[20px] text-[#be0000]">
                  {ERROR_TITLE[error.code] ?? 'Could not analyse this video'}
                </p>
                <p className="mt-[4px] text-[12px] leading-[18px] text-[#8a8a8a]">
                  {error.message}
                </p>
              </div>
            ) : (
              <div className="h-[35px] w-full" />
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
