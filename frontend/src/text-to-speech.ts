/** Owns TTS enablement, queueing and Web Audio playback. */

import { TTS_SAMPLE_RATE } from "./types";
import { StrictLineAudioQueue } from "./tts-line-queue";
import { resolveTtsChunkSchedule } from "./tts-playback";

export interface TextToSpeechConfig {
  outputDevice: string;
  ttsDelaySeconds: number;
  playbackRate: number;
}

export interface TextToSpeechCallbacks {
  onLineStarted: (lineId: number) => void;
  onLineFinished: (lineId: number, audioSeconds: number) => void;
  onQueueChanged: () => void;
  onError: (message: string) => void;
}

export interface TextToSpeechState {
  isTtsEnabled: boolean;
  isSpeaking: boolean;
  activeLineId: number | null;
  queuedLineCount: number;
}

const FADE_MS = 8;

export class TextToSpeech {
  private audioCtx: AudioContext | null = null;
  private state: TextToSpeechState = {
    isTtsEnabled: false,
    isSpeaking: false,
    activeLineId: null,
    queuedLineCount: 0,
  };
  private readonly callbacks: TextToSpeechCallbacks;
  private readonly lineAudioQueue = new StrictLineAudioQueue<Uint8Array>();
  private config: TextToSpeechConfig;
  private nextPlayTime = 0;
  private currentPlayingLineId: number | null = null;
  private activeLineSources: AudioBufferSourceNode[] = [];
  private playbackEpoch = 0;
  private lastRegisteredLineId = 0;
  private minimumAcceptedLineId = 1;
  private nextLineIdToPlay: number | null = null;
  private lastScheduledLineId: number | null = null;
  private activeLinePendingChunks = 0;
  private activeLineDoneScheduling = false;
  private activeLineAudioSeconds = 0;
  private averageLineAudioSeconds = 3;
  private audioLineReadyCount = 0;
  private audioLinePlayedCount = 0;
  private interruptedAudioLineCount = 0;

  constructor(callbacks: TextToSpeechCallbacks, config: TextToSpeechConfig) {
    this.callbacks = callbacks;
    this.config = config;
  }

  getState(): Readonly<TextToSpeechState> {
    return { ...this.state, queuedLineCount: this.lineAudioQueue.lineCount };
  }

  isAudible(): boolean {
    return this.activeLineSources.length > 0;
  }

  hasPendingAudio(): boolean {
    return this.currentPlayingLineId !== null || this.lineAudioQueue.lineCount > 0;
  }

  async enable(config?: Partial<TextToSpeechConfig>): Promise<void> {
    if (config) this.config = { ...this.config, ...config };
    if (this.state.isTtsEnabled) return;

    try {
      await this.initAudioContext();
      this.state.isTtsEnabled = true;
      this.callbacks.onQueueChanged();
    } catch (error) {
      const message = `Failed to enable TTS: ${(error as Error).message}`;
      this.callbacks.onError(message);
      throw error;
    }
  }

  disable(): void {
    if (!this.state.isTtsEnabled) return;
    this.state.isTtsEnabled = false;
    this.cancelAllAudio();
  }

  updateConfig(config: Partial<TextToSpeechConfig>): void {
    this.config = { ...this.config, ...config };
    if (config.outputDevice !== undefined && this.audioCtx) {
      void this.setAudioOutputDevice(this.audioCtx, config.outputDevice).catch((error) => {
        this.callbacks.onError(`Failed to change output device: ${(error as Error).message}`);
      });
    }
  }

  registerLine(lineId: number): void {
    if (!this.state.isTtsEnabled) return;
    this.lineAudioQueue.registerLine(lineId);
    this.lastRegisteredLineId = Math.max(this.lastRegisteredLineId, lineId);
    this.audioLineReadyCount += 1;
    if (this.nextLineIdToPlay === null && this.currentPlayingLineId === null) {
      this.nextLineIdToPlay = this.lineAudioQueue.firstLineId;
    }
    this.logTtsLineProgress();
    this.callbacks.onQueueChanged();
  }

  addAudioChunk(chunk: Uint8Array, lineId: number, isLastChunk: boolean): void {
    if (!this.state.isTtsEnabled || lineId < this.minimumAcceptedLineId) return;
    this.lineAudioQueue.addChunk(lineId, chunk, isLastChunk);
    if (this.nextLineIdToPlay === null) {
      this.nextLineIdToPlay = this.lineAudioQueue.firstLineId;
    }
    this.streamActiveLine();
    this.callbacks.onQueueChanged();
  }

  cancelAllAudio(): void {
    this.playbackEpoch += 1;
    const interruptedIds =
      this.lineAudioQueue.lineCount + (this.currentPlayingLineId === null ? 0 : 1);
    this.interruptedAudioLineCount += interruptedIds;
    this.lineAudioQueue.clear();
    for (const source of this.activeLineSources) {
      try {
        source.stop();
      } catch {
        // Already stopped.
      }
    }
    this.activeLineSources = [];
    if (this.audioCtx) this.nextPlayTime = this.audioCtx.currentTime;
    this.currentPlayingLineId = null;
    this.state.activeLineId = null;
    this.state.isSpeaking = false;
    this.activeLinePendingChunks = 0;
    this.activeLineDoneScheduling = false;
    this.activeLineAudioSeconds = 0;
    this.lastScheduledLineId = null;
    this.minimumAcceptedLineId = this.lastRegisteredLineId + 1;
    this.nextLineIdToPlay = null;
    this.callbacks.onQueueChanged();
  }

  resetSession(): void {
    this.cancelAllAudio();
    this.lastRegisteredLineId = 0;
    this.minimumAcceptedLineId = 1;
    this.audioLineReadyCount = 0;
    this.audioLinePlayedCount = 0;
    this.interruptedAudioLineCount = 0;
    this.averageLineAudioSeconds = 3;
    this.nextLineIdToPlay = null;
  }

  async cleanup(): Promise<void> {
    this.cancelAllAudio();
    if (this.audioCtx && this.audioCtx.state !== "closed") {
      await this.audioCtx.close().catch(() => undefined);
    }
    this.audioCtx = null;
    this.state.isTtsEnabled = false;
  }

  getScheduledPlaybackSeconds(): number {
    if (!this.audioCtx) return 0;
    return Math.max(0, this.nextPlayTime - this.audioCtx.currentTime);
  }

  getQueuedAudioSeconds(): number {
    return this.lineAudioQueue.estimatedAudioSeconds(
      (chunk) => this.pcmChunkDurationSeconds(chunk),
      this.averageLineAudioSeconds,
    );
  }

  getQueuedLineDelaySeconds(): number {
    return this.lineAudioQueue.lineCount * this.config.ttsDelaySeconds;
  }

  private async initAudioContext(): Promise<void> {
    if (!this.audioCtx || this.audioCtx.state === "closed") {
      this.audioCtx = new AudioContext({ sampleRate: TTS_SAMPLE_RATE });
    }
    await this.setAudioOutputDevice(this.audioCtx, this.config.outputDevice);
    if (this.audioCtx.state === "suspended") await this.audioCtx.resume();
    this.nextPlayTime = this.audioCtx.currentTime;
  }

  private async setAudioOutputDevice(ctx: AudioContext, deviceId: string): Promise<void> {
    type SinkRoutableAudioContext = AudioContext & {
      setSinkId?: (sinkId: string) => Promise<void>;
    };
    const setSinkId = (ctx as SinkRoutableAudioContext).setSinkId;
    if (deviceId === "default") {
      if (typeof setSinkId === "function") await setSinkId.call(ctx, "");
      return;
    }
    if (typeof setSinkId !== "function") {
      throw new Error("This browser cannot route audio to a selected output device");
    }
    await setSinkId.call(ctx, deviceId);
  }

  private streamActiveLine(): void {
    if (!this.audioCtx || !this.state.isTtsEnabled) return;
    if (this.currentPlayingLineId === null) {
      if (this.nextLineIdToPlay === null) {
        this.nextLineIdToPlay = this.lineAudioQueue.firstLineId;
      }
      const line = this.lineAudioQueue.takeReady(this.nextLineIdToPlay);
      if (!line) return;
      this.currentPlayingLineId = line.lineId;
      this.state.activeLineId = line.lineId;
      this.state.isSpeaking = true;
      this.activeLinePendingChunks = 0;
      this.activeLineDoneScheduling = false;
      this.activeLineAudioSeconds = 0;
      this.callbacks.onLineStarted(line.lineId);
    }

    const lineId = this.currentPlayingLineId;
    const epoch = this.playbackEpoch;
    let next = this.lineAudioQueue.takeNextChunk();
    while (next !== null) {
      const { chunk, isLast } = next;
      this.activeLinePendingChunks += 1;
      this.activeLineAudioSeconds += this.pcmChunkDurationSeconds(chunk);
      this.playPcmChunk(chunk, lineId, () => {
        if (epoch !== this.playbackEpoch) return;
        this.activeLinePendingChunks -= 1;
        this.maybeFinishActiveLine();
      });
      if (isLast) this.activeLineDoneScheduling = true;
      next = this.lineAudioQueue.takeNextChunk();
    }
    this.maybeFinishActiveLine();
  }

  private maybeFinishActiveLine(): void {
    if (this.currentPlayingLineId === null) return;
    if (!this.activeLineDoneScheduling || this.activeLinePendingChunks > 0) return;
    const lineId = this.currentPlayingLineId;
    const audioSeconds = this.activeLineAudioSeconds;
    if (!this.lineAudioQueue.finishLine(lineId)) return;

    this.audioLinePlayedCount += 1;
    this.averageLineAudioSeconds = this.averageLineAudioSeconds * 0.8 + audioSeconds * 0.2;
    this.currentPlayingLineId = null;
    this.state.activeLineId = null;
    this.state.isSpeaking = false;
    this.nextLineIdToPlay = this.lineAudioQueue.firstLineId;
    this.callbacks.onLineFinished(lineId, audioSeconds);
    this.logTtsLineProgress();
    this.callbacks.onQueueChanged();
    this.streamActiveLine();
  }

  private playPcmChunk(
    chunk: Uint8Array,
    lineId: number,
    onEnded: () => void,
  ): void {
    if (!this.audioCtx) return;
    const evenLength = chunk.byteLength - (chunk.byteLength % 2);
    const samples = new Int16Array(
      chunk.buffer as ArrayBuffer,
      chunk.byteOffset,
      evenLength / 2,
    );
    const pcm = new Float32Array(samples.length);
    for (let index = 0; index < samples.length; index += 1) {
      pcm[index] = samples[index] / 32768;
    }

    const buffer = this.audioCtx.createBuffer(1, pcm.length, TTS_SAMPLE_RATE);
    buffer.getChannelData(0).set(pcm);
    const source = this.audioCtx.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = this.config.playbackRate;
    // Compensate pitch shift caused by playbackRate so the voice stays natural
    // even at higher speeds. detune = 1200 * log2(1 / rate) restores the
    // original pitch: a rate of 2.0 shifts pitch +1 octave (+1200 cents),
    // and detune -1200 cancels it exactly.
    source.detune.value = 1200 * Math.log2(1 / this.config.playbackRate);
    const gain = this.audioCtx.createGain();
    source.connect(gain);
    gain.connect(this.audioCtx.destination);

    const schedule = resolveTtsChunkSchedule(
      this.audioCtx.currentTime,
      this.nextPlayTime,
      this.lastScheduledLineId,
      lineId,
      this.config.ttsDelaySeconds,
    );
    if (schedule.isNewLine) this.lastScheduledLineId = schedule.currentLineId;
    const startAt = schedule.startAt;
    const duration = buffer.duration / this.config.playbackRate;
    const endAt = startAt + duration;
    const fadeDuration = Math.min(FADE_MS / 1000, duration / 2);
    if (fadeDuration > 0) {
      gain.gain.setValueAtTime(0, startAt);
      gain.gain.linearRampToValueAtTime(1, startAt + fadeDuration);
      gain.gain.setValueAtTime(1, endAt - fadeDuration);
      gain.gain.linearRampToValueAtTime(0, endAt);
    }
    source.start(startAt);
    this.nextPlayTime = endAt;
    this.activeLineSources.push(source);
    source.onended = () => {
      const index = this.activeLineSources.indexOf(source);
      if (index !== -1) this.activeLineSources.splice(index, 1);
      onEnded();
    };
  }

  private pcmChunkDurationSeconds(chunk: Uint8Array): number {
    return chunk.byteLength / 2 / TTS_SAMPLE_RATE / this.config.playbackRate;
  }

  private logTtsLineProgress(force = false): void {
    const accounted = this.audioLinePlayedCount + this.interruptedAudioLineCount;
    const reachedMilestone =
      (this.audioLineReadyCount > 0 && this.audioLineReadyCount % 10 === 0) ||
      (accounted > 0 && accounted % 10 === 0);
    if (!force && !reachedMilestone) return;
    const summary =
      `line_ready(audio)=${this.audioLineReadyCount}, played=${this.audioLinePlayedCount}, ` +
      `interrupted=${this.interruptedAudioLineCount}, queued=${this.lineAudioQueue.lineCount}`;
    if (force && this.audioLineReadyCount !== accounted) {
      console.error(`[TTS line audit] MISMATCH: ${summary}`);
    } else {
      console.log(`[TTS line audit] ${summary}`);
    }
  }
}
