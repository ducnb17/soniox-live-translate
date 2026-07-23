export interface TtsChunkSchedule {
  startAt: number;
}

/** Start immediately when idle, otherwise append gaplessly to scheduled PCM. */
export function resolveTtsChunkSchedule(
  currentTime: number,
  nextPlayTime: number,
): TtsChunkSchedule {
  return {
    startAt: Math.max(currentTime, nextPlayTime),
  };
}
