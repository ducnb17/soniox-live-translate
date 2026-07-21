export type SttRuntimeState =
  | "idle"
  | "starting"
  | "running"
  | "reconnecting"
  | "stopping"
  | "error";

export interface SttSessionCallbacks {
  onMessage: (event: MessageEvent) => void;
  onState?: (state: SttRuntimeState, event?: CloseEvent) => void;
}

type WebSocketFactory = (url: string) => WebSocket;

/** Owns only the STT WebSocket and browser-to-STT audio ingress. */
export class SttSessionController {
  private socket: WebSocket | null = null;
  private generation = 0;
  private manuallyClosed = false;
  private pendingConnectReject: ((reason?: unknown) => void) | null = null;
  state: SttRuntimeState = "idle";

  constructor(
    private readonly callbacks: SttSessionCallbacks,
    private readonly socketFactory: WebSocketFactory = (url) => new WebSocket(url),
  ) {}

  connect(url: string): Promise<void> {
    this.close();
    this.manuallyClosed = false;
    const generation = ++this.generation;
    const socket = this.socketFactory(url);
    this.socket = socket;
    socket.binaryType = "arraybuffer";
    this.setState("starting");
    socket.onmessage = (event) => {
      if (generation === this.generation) this.callbacks.onMessage(event);
    };
    socket.onclose = (event) => {
      if (generation !== this.generation) return;
      this.socket = null;
      this.setState(this.manuallyClosed ? "idle" : "error", event);
    };

    return new Promise<void>((resolve, reject) => {
      this.pendingConnectReject = reject;
      socket.onopen = () => {
        if (generation !== this.generation) return;
        this.pendingConnectReject = null;
        this.setState("running");
        resolve();
      };
      socket.onerror = () => {
        if (generation !== this.generation) return;
        this.pendingConnectReject = null;
        this.setState("error");
        reject(new Error("STT WebSocket error"));
      };
    });
  }

  markReconnecting(): void {
    if (this.socket) this.setState("reconnecting");
  }

  markConnected(): void {
    if (this.socket) this.setState("running");
  }

  sendAudio(data: Blob | ArrayBuffer | ArrayBufferView): boolean {
    if (!this.isOpen()) return false;
    this.socket!.send(data);
    return true;
  }

  sendJson(data: unknown): boolean {
    if (!this.isOpen()) return false;
    this.socket!.send(JSON.stringify(data));
    return true;
  }

  isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  close(): void {
    if (this.socket !== null) this.setState("stopping");
    this.manuallyClosed = true;
    this.generation += 1;
    const socket = this.socket;
    this.socket = null;
    this.pendingConnectReject?.(new Error("STT connection cancelled"));
    this.pendingConnectReject = null;
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      try { socket.close(); } catch { /* already closed */ }
    }
    this.setState("idle");
  }

  private setState(state: SttRuntimeState, event?: CloseEvent): void {
    this.state = state;
    this.callbacks.onState?.(state, event);
  }
}
