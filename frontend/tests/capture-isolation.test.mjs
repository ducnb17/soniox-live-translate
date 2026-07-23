import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import ts from "typescript";

const source = readFileSync(new URL("../src/capture-isolation.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const compiledModule = { exports: {} };
new Function("exports", "module", outputText)(compiledModule.exports, compiledModule);
const {
  displayAudioConstraints,
  FallbackCaptureGate,
  FALLBACK_CAPTURE_TAIL_MS,
  resolveDisplayCaptureIsolation,
} = compiledModule.exports;

test("display capture requests exclusion without muting local playback", () => {
  const constraints = displayAudioConstraints();
  assert.equal(constraints.restrictOwnAudio, true);
  assert.equal(constraints.suppressLocalAudioPlayback, false);
});

test("verified own-audio filtering keeps source capture open during TTS", () => {
  assert.equal(
    resolveDisplayCaptureIsolation(true, { restrictOwnAudio: true }),
    "own-audio-filter",
  );
});

test("unsupported own-audio filtering selects the explicit fallback", () => {
  assert.equal(resolveDisplayCaptureIsolation(false, {}), "fallback-gate");
  assert.equal(
    resolveDisplayCaptureIsolation(true, { restrictOwnAudio: false }),
    "fallback-gate",
  );
});

test("fallback gate covers the hardware-output tail and then reopens", () => {
  const gate = new FallbackCaptureGate();
  assert.equal(gate.shouldMute(true, 1000), true);
  assert.equal(gate.shouldMute(false, 1000 + FALLBACK_CAPTURE_TAIL_MS - 1), true);
  assert.equal(gate.shouldMute(false, 1000 + FALLBACK_CAPTURE_TAIL_MS), false);
});
