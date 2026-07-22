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

test("save config button is present and wired to /api/config/save", () => {
  assert.match(html, /id="save-config-btn"/);
  assert.match(app, /\/api\/config\/save/);
  assert.match(app, /saveConfigBtn\.addEventListener/);
});

test("translation style offers six modes and is sent to the backend", () => {
  for (const style of ["natural", "literal", "professional", "casual", "subtitle_game", "technical"]) {
    assert.match(html, new RegExp(`<option value="${style}">`));
  }
  assert.match(app, /translation_style:\s*\$translationStyle\.value/);
  assert.match(app, /translationStyle:\s*\$translationStyle\.value/);
  assert.match(speechToText, /translation_style:\s*config\.translationStyle/);
  assert.match(app, /supported_styles/);
});

test("changing provider auto-detects saved key and disables input", () => {
  assert.match(app, /updateKeyInputState/);
  assert.match(app, /\.disabled\s*=\s*true/);
  assert.match(app, /Đã lưu key cho/);
  // Both STT and translation provider changes call updateKeyInputState.
  assert.match(app, /updateKeyInputState\(\$sttProvider,\s*sttProviders/);
  assert.match(app, /updateKeyInputState\(\$translationProvider,\s*translationProviders/);
  // TTS provider change also checks has_api_key.
  assert.match(app, /ttsHasKey/);
  assert.match(app, /\$ttsApiKey\.disabled\s*=\s*true/);
});
