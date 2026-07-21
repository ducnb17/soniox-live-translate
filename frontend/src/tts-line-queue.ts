export interface BufferedAudioLine<T> {
  lineId: number;
  chunks: T[];
  /** Index of the next chunk to hand to the caller via takeNextChunk(). */
  nextChunkIndex: number;
  done: boolean;
}

/**
 * Unbounded FIFO grouped by line ID.
 *
 * Streaming mode: a line becomes "active" (via takeReady) as soon as it is
 * next in sequence — the caller does NOT have to wait for all of its audio
 * to have arrived first. Once active, `takeNextChunk` hands out chunks one
 * at a time as they arrive, so playback of a line can start (and continue)
 * while Soniox is still generating the rest of that line's audio. This
 * removes the "wait for the whole line, then wait for it to fully play"
 * bottleneck that caused delay to snowball on continuous speech.
 *
 * No capacity/drop policy by design.
 */
export class StrictLineAudioQueue<T> {
  private readonly lines: BufferedAudioLine<T>[] = [];
  private activeLine: BufferedAudioLine<T> | null = null;

  registerLine(lineId: number): BufferedAudioLine<T> {
    if (this.activeLine?.lineId === lineId) return this.activeLine;
    const existing = this.lines.find((line) => line.lineId === lineId);
    if (existing) return existing;
    const line: BufferedAudioLine<T> = { lineId, chunks: [], nextChunkIndex: 0, done: false };
    this.lines.push(line);
    this.lines.sort((left, right) => left.lineId - right.lineId);
    return line;
  }

  addChunk(lineId: number, chunk: T, lineAudioEnd: boolean): void {
    const line = this.registerLine(lineId);
    line.chunks.push(chunk);
    if (lineAudioEnd) line.done = true;
  }

  /**
   * Activate the next line if it is next in sequence. Unlike the old
   * implementation, this does NOT require `done` — a line may be activated
   * (and streamed via takeNextChunk) as soon as it exists, even with zero
   * chunks received yet.
   */
  takeReady(nextLineId: number | null): BufferedAudioLine<T> | null {
    if (this.activeLine !== null) return null;
    const first = this.lines[0];
    if (!first || nextLineId === null || first.lineId !== nextLineId) {
      return null;
    }
    this.lines.shift();
    this.activeLine = first;
    return first;
  }

  /**
   * Return the next un-played chunk of the currently active line, or null if
   * there is no new chunk available yet. `isLast` is true when this chunk is
   * the final chunk of the line (i.e. the line is marked done and no further
   * chunks remain).
   */
  takeNextChunk(): { chunk: T; isLast: boolean } | null {
    const active = this.activeLine;
    if (!active) return null;
    if (active.nextChunkIndex >= active.chunks.length) return null;
    const chunk = active.chunks[active.nextChunkIndex];
    active.nextChunkIndex += 1;
    const isLast = active.done && active.nextChunkIndex >= active.chunks.length;
    return { chunk, isLast };
  }

  finishLine(lineId: number): boolean {
    if (this.activeLine?.lineId !== lineId) return false;
    this.activeLine = null;
    return true;
  }

  clear(): void {
    this.lines.length = 0;
    this.activeLine = null;
  }

  /** Estimated seconds of audio still queued behind the active line. */
  estimatedAudioSeconds(
    chunkDuration: (chunk: T) => number,
    fallbackLineSeconds: number,
  ): number {
    return this.lines.reduce((total, line) => {
      const receivedSeconds = line.chunks.reduce(
        (lineTotal, chunk) => lineTotal + chunkDuration(chunk),
        0,
      );
      return total + Math.max(receivedSeconds, fallbackLineSeconds);
    }, 0);
  }

  /** Number of lines queued behind the active line (excludes the active line itself). */
  get lineCount(): number {
    return this.lines.length;
  }

  get firstLineId(): number | null {
    return this.lines[0]?.lineId ?? null;
  }

  get activeLineId(): number | null {
    return this.activeLine?.lineId ?? null;
  }
}
