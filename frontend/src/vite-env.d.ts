/// <reference types="vite/client" />

// requestVideoFrameCallback is the only reliable way to know which frame is
// actually on screen; HTML5 `currentTime` seeking is not frame-accurate.
interface VideoFrameCallbackMetadata {
  presentationTime: DOMHighResTimeStamp
  expectedDisplayTime: DOMHighResTimeStamp
  width: number
  height: number
  mediaTime: number
  presentedFrames: number
  processingDuration?: number
}

interface HTMLVideoElement {
  requestVideoFrameCallback(
    callback: (now: DOMHighResTimeStamp, metadata: VideoFrameCallbackMetadata) => void,
  ): number
  cancelVideoFrameCallback(handle: number): void
}
