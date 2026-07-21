import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/app.ts", import.meta.url), "utf8");

test("STT and TTS controls are distinct and the legacy TTS checkbox is gone", () => {
  assert.match(html, /id="action"/);
  assert.match(html, /id="action-tts"/);
  assert.doesNotMatch(html, /id="tts"/);
  assert.match(app, /\/ws\/stt/);
  assert.match(app, /\/ws\/tts/);
  assert.doesNotMatch(app, /\/ws\/translate/);
});

test("TTS control never invokes the common STT stop function", () => {
  const listener = app.match(/\$actionTtsBtn\.addEventListener\("click",[\s\S]*?\n\}\);/u)?.[0] || "";
  assert.match(listener, /toggleTts/);
  assert.doesNotMatch(listener, /\bstop\(/);
  assert.doesNotMatch(listener, /playFile|start\(/);
});

test("file source lifecycle is absent from the TTS toggle", () => {
  const toggle = app.match(/async function toggleTts\(\)[\s\S]*?\n\}/u)?.[0] || "";
  assert.doesNotMatch(toggle, /fileAudio|pause\(|playFile|openWebSocket/);
  assert.match(toggle, /ttsSession\.disable/);
  assert.match(toggle, /interruptTtsAudio/);
});
