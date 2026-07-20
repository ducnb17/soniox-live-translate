export interface BufferedAudioLine<T> {
  lineId: number;
  chunks: T[];
  done: boolean;
}

/** Unbounded FIFO grouped by line ID. No capacity/drop policy by design. */
export class StrictLineAudioQueue<T> {
  private readonly lines: BufferedAudioLine<T>[] = [];
  private activeLineIdValue: number | null = null;

  registerLine(lineId: number): BufferedAudioLine<T> {
    const existing = this.lines.find((line) => line.lineId === lineId);
    if (existing) return existing;
    const line = { lineId, chunks: [], done: false };
    this.lines.push(line);
    this.lines.sort((left, right) => left.lineId - right.lineId);
    return line;
  }

  addChunk(lineId: number, chunk: T, lineAudioEnd: boolean): void {
    const line = this.registerLine(lineId);
    line.chunks.push(chunk);
    if (lineAudioEnd) line.done = true;
  }

  takeReady(nextLineId: number | null): BufferedAudioLine<T> | null {
    const first = this.lines[0];
    if (
      this.activeLineIdValue !== null ||
      !first ||
      nextLineId === null ||
      first.lineId !== nextLineId ||
      !first.done
    ) {
      return null;
    }
    const ready = this.lines.shift() ?? null;
    this.activeLineIdValue = ready?.lineId ?? null;
    return ready;
  }

  finishLine(lineId: number): boolean {
    if (this.activeLineIdValue !== lineId) return false;
    this.activeLineIdValue = null;
    return true;
  }

  clear(): void {
    this.lines.length = 0;
    this.activeLineIdValue = null;
  }

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

  get lineCount(): number {
    return this.lines.length;
  }

  get firstLineId(): number | null {
    return this.lines[0]?.lineId ?? null;
  }

  get activeLineId(): number | null {
    return this.activeLineIdValue;
  }
}
