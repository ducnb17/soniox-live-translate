import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/app.ts", import.meta.url), "utf8");
const speechToText = readFileSync(new URL("../src/speech-to-text.ts", import.meta.url), "utf8");

test("settings exposes all six Transifyr-style tabs", () => {
  for (const tab of ["general", "stt", "tts", "translation", "display", "about"]) {
    assert.match(html, new RegExp(`data-settings-tab="${tab}"`));
    assert.match(html, new RegExp(`data-settings-panel="${tab}"`));
  }
});

test("transcript uses one feed instead of parallel original and translation columns", () => {
  assert.match(html, /id="transcript-feed"/);
  assert.doesNotMatch(html, /id="original"/);
  assert.doesNotMatch(html, /id="translation"/);
});

test("frontend consumes backend line_ready as the final display boundary", () => {
  assert.match(speechToText, /data\.type === "line_ready"/);
  assert.match(speechToText, /this\.callbacks\.onLineReady\(data\)/);
  assert.match(app, /onLineReady: handleLineReady/);
});

test("STT and TTS expose independent controls and state", () => {
  assert.match(html, /id="action"/);
  assert.match(html, /id="action-tts"[^>]*aria-pressed="false"/);
  assert.doesNotMatch(html, /id="tts"/);
  assert.match(app, /speechToText\.getState\(\)\.isListening/);
  assert.match(app, /textToSpeech\.getState\(\)\.isTtsEnabled/);
  assert.match(app, /textToSpeech\.disable\(\);\s*speechToText\.setTtsEnabled\(false\)/);
});
