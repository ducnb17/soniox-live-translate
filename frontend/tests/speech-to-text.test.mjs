import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import ts from "typescript";

const source = readFileSync(new URL("../src/speech-to-text.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const compiledModule = { exports: {} };
new Function("exports", "module", outputText)(compiledModule.exports, compiledModule);
const { SpeechToText } = compiledModule.exports;

class FakeWebSocket {
  static OPEN = 1;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    this.closeCount = 0;
    FakeWebSocket.instances.push(this);
    queueMicrotask(() => {
      this.readyState = FakeWebSocket.OPEN;
      this.onopen?.();
    });
  }

  send(payload) {
    this.sent.push(payload);
  }

  close() {
    this.closeCount += 1;
    this.readyState = 3;
  }
}

function callbacks() {
  const noop = () => {};
  return {
    onTranscript: noop,
    onLineReady: noop,
    onAudioChunk: noop,
    onSessionId: noop,
    onReconnecting: noop,
    onReconnected: noop,
    onReconnectFailed: noop,
    onError: noop,
    onSessionDone: noop,
    onTranslationError: noop,
    onTtsFallback: noop,
    onTtsError: noop,
    onTtsUsage: noop,
    onStatusUpdate: noop,
  };
}

function config(isTtsEnabled) {
  return {
    mode: "one_way",
    targetLang: "vi",
    langId: true,
    diarize: true,
    voice: "Maya",
    voiceB: "Daniel",
    inputDevice: "default",
    outputDevice: "default",
    ttsProvider: "soniox",
    sttProvider: "soniox",
    translationProvider: "soniox",
    translationStyle: "natural",
    sttDelayMs: 1500,
    isTtsEnabled,
  };
}

test("TTS toggle sends a subscription command without restarting STT", async () => {
  const originalWebSocket = globalThis.WebSocket;
  const originalLocation = globalThis.location;
  globalThis.WebSocket = FakeWebSocket;
  globalThis.location = { protocol: "http:", host: "localhost:8000" };
  FakeWebSocket.instances.length = 0;

  try {
    const stt = new SpeechToText(callbacks());
    await stt.start(config(false));
    const socket = FakeWebSocket.instances[0];

    assert.equal(FakeWebSocket.instances.length, 1);
    assert.match(socket.url, /tts=true/);
    assert.match(socket.url, /tts_enabled=false/);
    assert.match(socket.url, /translation_style=natural/);
    assert.equal(stt.getState().isListening, true);

    stt.setTtsEnabled(true);
    assert.equal(FakeWebSocket.instances.length, 1);
    assert.equal(socket.closeCount, 0);
    assert.deepEqual(JSON.parse(socket.sent.at(-1)), {
      type: "tts_control",
      enabled: true,
    });
    assert.equal(stt.getState().isListening, true);

    stt.setTtsEnabled(false);
    assert.deepEqual(JSON.parse(socket.sent.at(-1)), {
      type: "tts_control",
      enabled: false,
    });
    assert.equal(socket.closeCount, 0);
    assert.equal(stt.getState().isListening, true);

    stt.stop();
    assert.equal(socket.closeCount, 1);
    assert.equal(stt.getState().isListening, false);
  } finally {
    globalThis.WebSocket = originalWebSocket;
    globalThis.location = originalLocation;
  }
});
