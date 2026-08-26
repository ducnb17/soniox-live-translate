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

test("live feed commits a line only after comma or full stop", () => {
  assert.match(app, /function endsDisplayLine\(text: string\): boolean/);
  assert.match(app, /\/\[,.\]\\s\*\$\//);
  assert.match(app, /pendingDisplayLine\.translationFinal \+= translated/);
  assert.match(app, /if \(endsDisplayLine\(visibleText\)\) \{/);
  assert.match(app, /utterances\.push\(pendingDisplayLine\)/);
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

test("provider save and test controls referenced at startup exist in the HTML", () => {
  for (const id of [
    "btn-save-stt-key",
    "btn-test-stt-key",
    "btn-save-translation-key",
    "btn-test-translation-key",
    "btn-save-tts-key",
    "btn-test-tts-key",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
});

test("STT start is blocked until required provider keys are saved", () => {
  assert.match(app, /function ensureRequiredProviderKeys\(\): boolean/);
  assert.match(app, /if \(!ensureRequiredProviderKeys\(\)\) return;/);
  assert.match(app, /activateSettingsTab\(check\.tab\)/);
});

test("keyless local STT hides the credential field", () => {
  assert.match(app, /keyRow\.classList\.toggle\("hidden", !provider\?\.requires_api_key\)/);
  assert.match(app, /Không cần key \(chạy local\)/);
});

test("every statically referenced startup element exists in the HTML", () => {
  const typedRefs = [...app.matchAll(/\$<[^>]+>\("([^"]+)"\)/g)].map((match) => match[1]);
  const plainRefs = [...app.matchAll(/\$\("([^"]+)"\)/g)].map((match) => match[1]);
  const missing = [...new Set([...typedRefs, ...plainRefs])]
    .filter((id) => !html.includes(`id="${id}"`));
  assert.deepEqual(missing, []);
});

test("changing provider auto-detects saved key and keeps input editable", () => {
  assert.match(app, /updateKeyInputState/);
  assert.match(app, /input\.disabled\s*=\s*false/);
  assert.match(app, /nhập key mới để thay đổi/);
  // Both STT and translation provider changes call updateKeyInputState.
  assert.match(app, /updateKeyInputState\(\$sttProvider,\s*sttProviders/);
  assert.match(app, /updateKeyInputState\(\$translationProvider,\s*translationProviders/);
  // TTS provider change also checks has_api_key.
  assert.match(app, /ttsHasKey/);
  assert.match(app, /\$ttsApiKey\.disabled\s*=\s*false/);
});
