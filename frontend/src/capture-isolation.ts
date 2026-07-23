export type CaptureIsolationMode =
  | "microphone-aec"
  | "own-audio-filter"
  | "fallback-gate";

export interface OwnAudioTrackSettings {
  restrictOwnAudio?: boolean;
}

/** Chromium screen-capture constraints that keep local TTS audible but out of STT. */
export function displayAudioConstraints(): MediaTrackConstraints {
  return {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
    suppressLocalAudioPlayback: false,
    restrictOwnAudio: true,
  } as MediaTrackConstraints;
}

export function resolveDisplayCaptureIsolation(
  supportsRestrictOwnAudio: boolean,
  settings: OwnAudioTrackSettings,
): CaptureIsolationMode {
  return supportsRestrictOwnAudio && settings.restrictOwnAudio === true
    ? "own-audio-filter"
    : "fallback-gate";
}

export const FALLBACK_CAPTURE_TAIL_MS = 350;

/** Last-resort guard for platforms that cannot exclude the app's render stream. */
export class FallbackCaptureGate {
  private muteUntilMs = 0;

  shouldMute(ttsScheduled: boolean, nowMs: number): boolean {
    if (ttsScheduled) {
      this.muteUntilMs = nowMs + FALLBACK_CAPTURE_TAIL_MS;
      return true;
    }
    return nowMs < this.muteUntilMs;
  }

  reset(): void {
    this.muteUntilMs = 0;
  }
}
