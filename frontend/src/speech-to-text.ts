/**
 * Owns the live STT transport and its lifecycle.
 *
 * TTS playback state deliberately does not live here. The only TTS-related
 * operation is forwarding a subscription command over the existing session.
 */

import type { SonioxSttResponse, TranslationMode, Utterance } from "./types";

export interface SpeechToTextConfig {
  mode: TranslationMode;
  targetLang?: string;
  langA?: string;
  langB?: string;
  langId: boolean;
  diarize: boolean;
  voice: string;
  voiceB: string;
  contextB64?: string;
  inputDevice: string;
  outputDevice: string;
  ttsProvider: string;
  sttProvider: string;
  translationProvider: string;
  translationStyle: string;
  sttDelayMs: number;
  isTtsEnabled: boolean;
}

export interface SpeechToTextCallbacks {
  onTranscript: (data: SonioxSttResponse) => void;
  onLineReady: (data: SonioxSttResponse) => void;
  onAudioChunk: (chunk: Uint8Array, lineId: number, isLastChunk: boolean) => void;
  onSessionId: (sessionId: string, isResume: boolean) => void;
  onReconnecting: (attempt: number, maxAttempts: number) => void;
  onReconnected: (downtimeMs: number, downtimeText?: string) => void;
  onReconnectFailed: (message: string) => void;
  onError: (message: string) => void;
  onSessionDone: () => void;
  onTranslationError: (message: string) => void;
  onTtsFallback: (fromProvider: string, toProvider: string, reason: string) => void;
  onTtsError: (message: string) => void;
  onTtsUsage: (usage: NonNullable<SonioxSttResponse["tts_usage"]>) => void;
  onStatusUpdate: (message: string) => void;
}

export interface SpeechToTextState {
  isListening: boolean;
  connectionStatus: "idle" | "connected" | "reconnecting" | "failed";
  sessionId: string | null;
}

export class SpeechToText {
  private ws: WebSocket | null = null;
  private state: SpeechToTextState = {
    isListening: false,
    connectionStatus: "idle",
    sessionId: null,
  };
  private readonly callbacks: SpeechToTextCallbacks;
  private pendingAudioBlobs: Blob[] = [];
  private pendingAudioOverflowed = false;
  private manualStopRequested = false;
  private lastConfig: SpeechToTextConfig | null = null;
  private lastExtraParams: Record<string, string> = {};
  private manualRetryInProgress = false;
  private resumeTranscriptOnNextSession = false;
  private pendingChunkLineId: number | null = null;
  private pendingChunkEndsLine = false;

  constructor(callbacks: SpeechToTextCallbacks) {
    this.callbacks = callbacks;
  }

  getState(): Readonly<SpeechToTextState> {
    return { ...this.state };
  }

  async start(
    config: SpeechToTextConfig,
    extraParams: Record<string, string> = {},
  ): Promise<void> {
    if (this.state.isListening) throw new Error("Already listening");

    this.manualStopRequested = false;
    this.lastConfig = { ...config };
    this.lastExtraParams = { ...extraParams };
    this.state.isListening = true;

    try {
      await this.openWebSocket(config, extraParams);
      this.state.connectionStatus = "connected";
    } catch (error) {
      this.state.isListening = false;
      this.state.connectionStatus = "idle";
      this.closeSocket();
      throw error;
    }
  }

  stop(): void {
    this.manualStopRequested = true;
    this.state.isListening = false;
    this.state.connectionStatus = "idle";
    this.pendingAudioBlobs = [];
    this.pendingAudioOverflowed = false;
    this.pendingChunkLineId = null;
    this.pendingChunkEndsLine = false;
    this.closeSocket();
  }

  sendAudio(blob: Blob): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(blob);
      } catch {
        // The socket closed between the readyState check and send.
      }
      return;
    }

    if (
      this.state.connectionStatus === "reconnecting" ||
      this.state.connectionStatus === "failed"
    ) {
      if (this.pendingAudioBlobs.length >= 100) {
        this.pendingAudioBlobs.shift();
        this.pendingAudioOverflowed = true;
      }
      this.pendingAudioBlobs.push(blob);
    }
  }

  sendTranscriptSnapshot(utterances: Utterance[]): void {
    const snapshot = utterances.filter(
      (utterance) =>
        utterance.originalFinal ||
        utterance.translationFinal ||
        utterance.originalPartial ||
        utterance.translationPartial,
    );
    if (snapshot.length) this.sendJson({ type: "utterances", utterances: snapshot });
  }

  sendBargeIn(): void {
    this.sendJson({ type: "barge" });
  }

  setTtsEnabled(enabled: boolean): void {
    if (this.lastConfig) this.lastConfig.isTtsEnabled = enabled;
    this.sendJson({ type: "tts_control", enabled });
  }

  async retryConnection(): Promise<void> {
    if (
      this.state.connectionStatus !== "failed" ||
      this.manualRetryInProgress ||
      !this.lastConfig
    ) {
      return;
    }

    this.manualRetryInProgress = true;
    this.manualStopRequested = false;
    this.resumeTranscriptOnNextSession = true;
    this.state.connectionStatus = "reconnecting";
    this.callbacks.onStatusUpdate("Đang thử kết nối lại…");

    try {
      await this.openWebSocket(this.lastConfig, this.lastExtraParams);
      this.state.connectionStatus = "connected";
      this.callbacks.onStatusUpdate("Đã kết nối lại thủ công");
      if (this.pendingAudioOverflowed) {
        this.callbacks.onStatusUpdate(
          "[mất âm thanh trong lúc chờ thử lại; buffer trình duyệt đầy]",
        );
        this.pendingAudioOverflowed = false;
      }
      this.flushPendingAudio();
    } catch (error) {
      console.error("Manual reconnect failed", error);
      this.resumeTranscriptOnNextSession = false;
      this.state.connectionStatus = "failed";
      this.callbacks.onStatusUpdate(
        "Thử lại chưa thành công. Vui lòng kiểm tra mạng và thử lại.",
      );
    } finally {
      this.manualRetryInProgress = false;
    }
  }

  private async openWebSocket(
    config: SpeechToTextConfig,
    extraParams: Record<string, string>,
  ): Promise<void> {
    this.closeSocket();
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const params = new URLSearchParams({
      mode: config.mode,
      lang_id: String(config.langId),
      diarize: String(config.diarize),
      voice: config.voice,
      voice_b: config.voiceB,
      // Keep the transport available without coupling it to subscriber state.
      // The backend only synthesizes while tts_enabled is true.
      tts: "true",
      tts_enabled: String(config.isTtsEnabled),
      stt_delay_ms: String(config.sttDelayMs),
      input_device: config.inputDevice,
      output_device: config.outputDevice,
      tts_provider: config.ttsProvider,
      stt_provider: config.sttProvider,
      translation_provider: config.translationProvider,
      translation_style: config.translationStyle,
      ...extraParams,
    });

    if (config.mode === "one_way") {
      if (config.targetLang) params.set("target_lang", config.targetLang);
    } else {
      if (config.langA) params.set("lang_a", config.langA);
      if (config.langB) {
        params.set("lang_b", config.langB);
        params.set("target_lang", config.langB);
      }
    }
    if (config.contextB64) params.set("context_b64", config.contextB64);

    const socket = new WebSocket(`${proto}//${location.host}/ws/translate?${params}`);
    this.ws = socket;
    socket.binaryType = "arraybuffer";
    socket.onmessage = (event: MessageEvent) => this.handleMessage(event);
    socket.onclose = (event: CloseEvent) => {
      if (socket !== this.ws) return;
      if (this.manualStopRequested) {
        this.state.connectionStatus = "idle";
        return;
      }
      console.log("WebSocket closed", event.code, event.reason);
      if (event.code === 4000) {
        this.state.connectionStatus = "failed";
        this.callbacks.onStatusUpdate(
          "Không thể kết nối lại. Nhấn “Thử lại” để tiếp tục phiên.",
        );
      }
    };

    await new Promise<void>((resolve, reject) => {
      socket.onopen = () => resolve();
      socket.onerror = () => reject(new Error("WebSocket error"));
    });
  }

  private handleMessage(event: MessageEvent): void {
    if (typeof event.data !== "string") {
      const lineId = this.pendingChunkLineId;
      const isLastChunk = this.pendingChunkEndsLine;
      this.pendingChunkLineId = null;
      this.pendingChunkEndsLine = false;
      if (lineId === null) {
        console.warn("Received TTS audio without audio_chunk_meta");
        return;
      }
      this.callbacks.onAudioChunk(
        new Uint8Array(event.data as ArrayBuffer),
        lineId,
        isLastChunk,
      );
      return;
    }

    let data: SonioxSttResponse;
    try {
      data = JSON.parse(event.data) as SonioxSttResponse;
    } catch {
      this.callbacks.onError("Server returned an invalid message");
      return;
    }
    this.handleJsonMessage(data);
  }

  private handleJsonMessage(data: SonioxSttResponse): void {
    if (data.session_id) {
      this.state.sessionId = data.session_id;
      const isResume = this.resumeTranscriptOnNextSession;
      this.resumeTranscriptOnNextSession = false;
      this.callbacks.onSessionId(data.session_id, isResume);
      return;
    }
    if (data.reconnecting) {
      this.state.connectionStatus = "reconnecting";
      this.callbacks.onReconnecting(data.attempt || 0, data.max_attempts || 0);
      return;
    }
    if (data.reconnected) {
      this.state.connectionStatus = "connected";
      this.callbacks.onReconnected(data.downtime_ms || 0, data.downtime_text);
      this.flushPendingAudio();
      return;
    }
    if (data.reconnect_failed) {
      this.state.connectionStatus = "failed";
      this.callbacks.onReconnectFailed(data.error_message || "Không thể kết nối lại.");
      return;
    }
    if (data.tts_fallback) {
      this.callbacks.onTtsFallback(
        data.tts_fallback.from_provider,
        data.tts_fallback.to_provider,
        data.tts_fallback.reason,
      );
      return;
    }
    if (data.tts_usage) {
      this.callbacks.onTtsUsage(data.tts_usage);
      return;
    }
    if (data.translation_error) {
      this.callbacks.onTranslationError(data.translation_error.message);
      return;
    }
    if (data.type === "audio_chunk_meta") {
      this.pendingChunkLineId = typeof data.line_id === "number" ? data.line_id : null;
      this.pendingChunkEndsLine = data.line_audio_end === true;
      return;
    }
    if (data.type === "line_ready") {
      this.callbacks.onLineReady(data);
      return;
    }
    if (data.type === "tts_error" || data.tts_error) {
      this.callbacks.onTtsError(
        data.error_message || data.tts_error?.message || "TTS không phát được",
      );
      return;
    }
    if (data.error_code || data.error_message) {
      if (this.isRetryableSttError(data)) {
        this.state.connectionStatus = "reconnecting";
        this.callbacks.onStatusUpdate("Kết nối STT bị gián đoạn, đang chuẩn bị thử lại…");
        return;
      }
      const message = data.error_message
        ? data.error_message.replace(/code \d+/g, "").replace(/\(.*\)/g, "").trim()
        : "Lỗi kết nối. Vui lòng thử lại.";
      this.callbacks.onError(message);
      return;
    }
    if (data.barge_ack || data.type === "tts_control_ack") return;
    if (data.session_done) {
      this.callbacks.onSessionDone();
      return;
    }
    this.callbacks.onTranscript(data);
  }

  private isRetryableSttError(data: SonioxSttResponse): boolean {
    if (
      data.error_type === "service_unavailable" ||
      data.error_type === "max_duration_reached"
    ) {
      return true;
    }
    const numericCode = Number(data.error_code);
    return Number.isFinite(numericCode) && numericCode >= 500;
  }

  private sendJson(payload: object): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    try {
      this.ws.send(JSON.stringify(payload));
    } catch {
      // The socket closed between the readyState check and send.
    }
  }

  private flushPendingAudio(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      for (const blob of this.pendingAudioBlobs) {
        try {
          this.ws.send(blob);
        } catch {
          break;
        }
      }
    }
    this.pendingAudioBlobs = [];
  }

  private closeSocket(): void {
    const socket = this.ws;
    this.ws = null;
    if (!socket) return;
    socket.onopen = null;
    socket.onerror = null;
    socket.onmessage = null;
    socket.onclose = null;
    try {
      socket.close();
    } catch {
      // Already closed.
    }
  }
}
