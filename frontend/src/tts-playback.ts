export interface TtsChunkSchedule {
  startAt: number;
  isNewLine: boolean;
  currentLineId: number;
}

/** Resolve one PCM chunk's Web Audio start time from its real line ID. */
export function resolveTtsChunkSchedule(
  currentTime: number,
  nextPlayTime: number,
  currentLineId: number | null,
  lineId: number,
  lineDelaySeconds: number,
): TtsChunkSchedule {
  const isNewLine = lineId !== currentLineId;
  const earliestGaplessStart = Math.max(currentTime, nextPlayTime);
  return {
    startAt: isNewLine
      ? earliestGaplessStart + lineDelaySeconds
      : earliestGaplessStart,
    isNewLine,
    currentLineId: lineId,
  };
}
