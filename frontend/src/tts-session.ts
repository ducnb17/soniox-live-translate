export type TtsRuntimeState =
  | "off"
  | "waiting_for_stt"
  | "connecting"
  | "on"
  | "stopping"
  | "error";

export interface TtsConfigure {
  provider: string;
  voice: string;
  voiceB?: string;
  mode?: "one_way" | "two_way";
  targetLang?: string;
  langA?: string;
  langB?: string;
  realtimeStreaming?: boolean;
}

export interface TtsSpeakRequest {
  requestId: string;
  lineId: number;
  text: string;
  direction: string;
  voice?: string;
}

export interface TtsStreamTextRequest extends TtsSpeakRequest {
  sequence: number;
}

export interface TtsAudioMeta {
  request_id: string;
  line_id: number;
  epoch: number;
  byte_length: number;
  line_audio_end: boolean;
}

export interface TtsSessionCallbacks {
  onState?: (state: TtsRuntimeState) => void;
  onAudio: (audio: ArrayBuffer, meta: TtsAudioMeta) => void;
  onMessage?: (message: Record<string, unknown>) => void;
  onError?: (message: string) => void;
  onReset?: (epoch: number) => void;
}

type WebSocketFactory = (url: string) => WebSocket;

/** Independent TTS lifecycle with epoch filtering and per-line deduplication. */
export class TtsSessionController {
  private socket: WebSocket | null = null;
  private generation = 0;
  private sttActive = false;
  private url = "";
  private config: TtsConfigure = { provider: "soniox", voice: "Maya" };
  private pendingMeta: TtsAudioMeta | null = null;
  private readonly sentLines = new Set<string>();
  private readonly sentChunks = new Set<string>();
  private readonly startedStreams = new Set<string>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pendingConnectReject: ((reason?: unknown) => void) | null = null;
  private manuallyClosed = false;
  desiredEnabled = false;
  epoch = 0;
  state: TtsRuntimeState = "off";

  constructor(
    private readonly callbacks: TtsSessionCallbacks,
    private readonly socketFactory: WebSocketFactory = (url) => new WebSocket(url),
  ) {}

  async enable(url: string, config: TtsConfigure, sttActive: boolean): Promise<void> {
    this.desiredEnabled = true;
    this.url = url;
    this.config = config;
    this.sttActive = sttActive;
    if (!sttActive) {
      this.setState("waiting_for_stt");
      return;
    }
    await this.connect();
  }

  async onSttStarted(): Promise<void> {
    this.sttActive = true;
    if (this.desiredEnabled && !this.isOpen()) await this.connect();
  }

  onSttStopped(): void {
    this.sttActive = false;
    this.cancelAndClose(true);
  }

  disable(): void {
    this.desiredEnabled = false;
    this.cancelAndClose(false);
  }

  speak(request: TtsSpeakRequest): boolean {
    if (this.state !== "on" || !this.isOpen()) return false;
    const key = `${this.epoch}:${request.requestId}`;
    if (this.sentLines.has(key)) return false;
    this.sentLines.add(key);
    this.send({
      type: "speak",
      request_id: request.requestId,
      line_id: request.lineId,
      text: request.text,
      direction: request.direction,
      voice: request.voice,
      epoch: this.epoch,
    });
    return true;
  }

  streamText(request: TtsStreamTextRequest): "started" | "continued" | false {
    if (this.state !== "on" || !this.isOpen()) return false;
    const streamKey = `${this.epoch}:${request.requestId}`;
    const chunkKey = `${streamKey}:${request.sequence}`;
    if (this.sentChunks.has(chunkKey)) return false;
    this.sentChunks.add(chunkKey);
    const started = !this.startedStreams.has(streamKey);
    this.startedStreams.add(streamKey);
    this.send({
      type: "stream_text",
      request_id: request.requestId,
      line_id: request.lineId,
      text: request.text,
      direction: request.direction,
      voice: request.voice,
      sequence: request.sequence,
      epoch: this.epoch,
    });
    return started ? "started" : "continued";
  }

  endStream(requestId: string): boolean {
    if (this.state !== "on" || !this.isOpen()) return false;
    const streamKey = `${this.epoch}:${requestId}`;
    if (!this.startedStreams.has(streamKey)) return false;
    this.send({ type: "stream_end", request_id: requestId, epoch: this.epoch });
    return true;
  }

  cancelAll(restartRealtime = true): void {
    this.epoch += 1;
    this.sentLines.clear();
    this.sentChunks.clear();
    this.startedStreams.clear();
    this.pendingMeta = null;
    if (this.isOpen()) {
      this.send({ type: "cancel_all", epoch: this.epoch, restart: restartRealtime });
    }
  }

  isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  private connect(): Promise<void> {
    this.closeSocket();
    this.manuallyClosed = false;
    const generation = ++this.generation;
    const socket = this.socketFactory(this.url);
    this.socket = socket;
    socket.binaryType = "arraybuffer";
    this.setState("connecting");

    socket.onmessage = (event) => {
      if (generation !== this.generation) return;
      this.handleMessage(event);
    };
    socket.onclose = () => {
      if (generation !== this.generation) return;
      this.socket = null;
      if (!this.manuallyClosed && this.desiredEnabled && this.sttActive) {
        this.epoch += 1;
        this.sentLines.clear();
        this.sentChunks.clear();
        this.startedStreams.clear();
        this.pendingMeta = null;
        this.callbacks.onReset?.(this.epoch);
        this.setState("connecting");
        this.reconnectTimer = setTimeout(() => {
          void this.connect().catch((error: unknown) => {
            this.callbacks.onError?.((error as Error).message);
          });
        }, 500);
      } else {
        this.setState(this.desiredEnabled ? "waiting_for_stt" : "off");
      }
    };

    return new Promise<void>((resolve, reject) => {
      this.pendingConnectReject = reject;
      socket.onopen = () => {
        if (generation !== this.generation) return;
        this.pendingConnectReject = null;
        this.send({
          type: "configure",
          enabled: true,
          provider: this.config.provider,
          voice: this.config.voice,
          voice_b: this.config.voiceB,
          mode: this.config.mode,
          target_lang: this.config.targetLang,
          lang_a: this.config.langA,
          lang_b: this.config.langB,
          realtime_streaming: this.config.realtimeStreaming,
          epoch: this.epoch,
        });
        resolve();
      };
      socket.onerror = () => {
        if (generation !== this.generation) return;
        this.pendingConnectReject = null;
        this.setState("error");
        reject(new Error("TTS WebSocket error"));
      };
    });
  }

  private handleMessage(event: MessageEvent): void {
    if (typeof event.data === "string") {
      const message = JSON.parse(event.data) as Record<string, unknown>;
      const messageEpoch = Number(message.epoch);
      if (Number.isFinite(messageEpoch) && messageEpoch !== this.epoch) return;
      if (message.type === "tts_state") {
        const serverState = String(message.state);
        if (serverState === "on") this.setState("on");
        else if (serverState === "error") this.setState("error");
        else if (!this.desiredEnabled) this.setState("off");
      } else if (message.type === "audio_chunk_meta") {
        this.pendingMeta = message as unknown as TtsAudioMeta;
      } else if (message.type === "tts_error") {
        this.setState("error");
        this.callbacks.onError?.(String(message.message || "TTS failed"));
      }
      this.callbacks.onMessage?.(message);
      return;
    }

    const meta = this.pendingMeta;
    this.pendingMeta = null;
    if (!meta || meta.epoch !== this.epoch || !this.desiredEnabled) return;
    this.callbacks.onAudio(event.data as ArrayBuffer, meta);
  }

  private cancelAndClose(preserveDesired: boolean): void {
    this.setState("stopping");
    this.cancelAll(false);
    this.manuallyClosed = true;
    this.closeSocket();
    this.setState(preserveDesired && this.desiredEnabled ? "waiting_for_stt" : "off");
  }

  private closeSocket(): void {
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.generation += 1;
    const socket = this.socket;
    this.socket = null;
    this.pendingConnectReject?.(new Error("TTS connection cancelled"));
    this.pendingConnectReject = null;
    if (!socket) return;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
    try { socket.close(); } catch { /* already closed */ }
  }

  private send(message: unknown): void {
    if (this.isOpen()) this.socket!.send(JSON.stringify(message));
  }

  private setState(state: TtsRuntimeState): void {
    this.state = state;
    this.callbacks.onState?.(state);
  }
}
