import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import ts from "typescript";

function loadTypeScriptModule(relativePath) {
  const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const compiledModule = { exports: {} };
  new Function("exports", "module", outputText)(compiledModule.exports, compiledModule);
  return compiledModule.exports;
}

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.sent = [];
    this.closed = false;
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.({});
  }

  message(data) {
    this.onmessage?.({ data });
  }

  send(data) {
    this.sent.push(data);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this.closed = true;
  }

  disconnect() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code: 1006, reason: "network" });
  }
}

globalThis.WebSocket = FakeWebSocket;
const { SttSessionController } = loadTypeScriptModule("../src/stt-session.ts");
const { TtsSessionController } = loadTypeScriptModule("../src/tts-session.ts");

test("STT controller owns audio socket independently", async () => {
  const sockets = [];
  const states = [];
  const controller = new SttSessionController(
    { onMessage() {}, onState: (state) => states.push(state) },
    (url) => {
      const socket = new FakeWebSocket(url);
      sockets.push(socket);
      return socket;
    },
  );
  const connected = controller.connect("ws://local/ws/stt");
  sockets[0].open();
  await connected;

  assert.equal(controller.sendAudio("audio"), true);
  assert.deepEqual(sockets[0].sent, ["audio"]);
  controller.close();
  assert.equal(sockets[0].closed, true);
  assert.deepEqual(states, ["idle", "starting", "running", "stopping", "idle"]);
});

test("TTS can wait for STT, deduplicates lines, and turns off alone", async () => {
  const sockets = [];
  const states = [];
  const controller = new TtsSessionController(
    { onAudio() {}, onState: (state) => states.push(state) },
    (url) => {
      const socket = new FakeWebSocket(url);
      sockets.push(socket);
      return socket;
    },
  );

  await controller.enable("ws://local/ws/tts", { provider: "soniox", voice: "Maya" }, false);
  assert.equal(controller.state, "waiting_for_stt");
  const connecting = controller.onSttStarted();
  sockets[0].open();
  await connecting;
  sockets[0].message(JSON.stringify({ type: "tts_state", state: "on", epoch: 0 }));

  const line = { requestId: "s:1", lineId: 1, text: "hello", direction: "en" };
  assert.equal(controller.speak(line), true);
  assert.equal(controller.speak(line), false);
  controller.disable();

  const commands = sockets[0].sent.map((item) => JSON.parse(item));
  assert.deepEqual(commands.map(({ type }) => type), ["configure", "speak", "cancel_all"]);
  assert.equal(controller.desiredEnabled, false);
  assert.equal(controller.state, "off");
  assert.equal(sockets[0].closed, true);
  assert.ok(states.includes("waiting_for_stt"));
  assert.ok(states.includes("on"));
});

test("stale TTS epochs and audio without metadata are discarded", async () => {
  const sockets = [];
  const received = [];
  const controller = new TtsSessionController(
    { onAudio: (audio, meta) => received.push([audio, meta]) },
    () => {
      const socket = new FakeWebSocket("ws://local/ws/tts");
      sockets.push(socket);
      return socket;
    },
  );
  const connecting = controller.enable(
    "ws://local/ws/tts", { provider: "soniox", voice: "Maya" }, true,
  );
  sockets[0].open();
  await connecting;
  sockets[0].message(JSON.stringify({ type: "tts_state", state: "on", epoch: 0 }));
  controller.cancelAll();
  sockets[0].message(JSON.stringify({
    type: "audio_chunk_meta", request_id: "old", line_id: 1,
    epoch: 0, byte_length: 2, line_audio_end: true,
  }));
  sockets[0].message(new ArrayBuffer(2));
  sockets[0].message(new ArrayBuffer(2));

  assert.deepEqual(received, []);
  controller.disable();
});

test("rapid TTS on/off/on settles old connects and ignores the old socket", async () => {
  const sockets = [];
  const controller = new TtsSessionController(
    { onAudio() {}, onError() {} },
    () => {
      const socket = new FakeWebSocket("ws://local/ws/tts");
      sockets.push(socket);
      return socket;
    },
  );
  const firstEnable = controller.enable(
    "ws://local/ws/tts", { provider: "soniox", voice: "Maya" }, true,
  );
  controller.disable();
  await assert.rejects(firstEnable, /cancelled/);

  const secondEnable = controller.enable(
    "ws://local/ws/tts", { provider: "soniox", voice: "Maya" }, true,
  );
  sockets[1].open();
  await secondEnable;
  sockets[0].message(JSON.stringify({ type: "tts_state", state: "on", epoch: 0 }));
  assert.equal(controller.state, "connecting");
  sockets[1].message(JSON.stringify({ type: "tts_state", state: "on", epoch: 1 }));
  assert.equal(controller.state, "on");
  controller.disable();
});

test("stopping STT preserves TTS preference but closes only the TTS runtime", async () => {
  const sockets = [];
  const controller = new TtsSessionController(
    { onAudio() {} },
    () => {
      const socket = new FakeWebSocket("ws://local/ws/tts");
      sockets.push(socket);
      return socket;
    },
  );
  const enabling = controller.enable(
    "ws://local/ws/tts", { provider: "soniox", voice: "Maya" }, true,
  );
  sockets[0].open();
  await enabling;
  controller.onSttStopped();

  assert.equal(controller.desiredEnabled, true);
  assert.equal(controller.state, "waiting_for_stt");
  assert.equal(sockets[0].closed, true);
});

test("TTS reconnect advances its own epoch and resets stale playback", async () => {
  const sockets = [];
  const resets = [];
  const controller = new TtsSessionController(
    { onAudio() {}, onReset: (epoch) => resets.push(epoch), onError() {} },
    () => {
      const socket = new FakeWebSocket("ws://local/ws/tts");
      sockets.push(socket);
      return socket;
    },
  );
  const enabling = controller.enable(
    "ws://local/ws/tts", { provider: "soniox", voice: "Maya" }, true,
  );
  sockets[0].open();
  await enabling;
  sockets[0].message(JSON.stringify({ type: "tts_state", state: "on", epoch: 0 }));
  sockets[0].disconnect();

  assert.equal(controller.state, "connecting");
  assert.equal(controller.epoch, 1);
  assert.deepEqual(resets, [1]);
  controller.disable();
});
