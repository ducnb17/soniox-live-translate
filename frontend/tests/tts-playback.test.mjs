import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import ts from "typescript";

const source = readFileSync(new URL("../src/tts-playback.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const compiledModule = { exports: {} };
new Function("exports", "module", outputText)(compiledModule.exports, compiledModule);
const { resolveTtsChunkSchedule } = compiledModule.exports;

// Load and run the audio-stretch module
const stretchSource = readFileSync(new URL("../src/audio-stretch.ts", import.meta.url), "utf8");
const stretchTranspiled = ts.transpileModule(stretchSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const stretchMod = { exports: {} };
new Function("exports", "module", stretchTranspiled.outputText)(stretchMod.exports, stretchMod);
const { stretchSamples } = stretchMod.exports;

// Read text-to-speech source for structural assertions
const ttsSource = readFileSync(new URL("../src/text-to-speech.ts", import.meta.url), "utf8");

test("the first translated audio chunk starts immediately", () => {
  const first = resolveTtsChunkSchedule(2.5, 0);
  assert.equal(first.startAt, 2.5);
});

test("the next line stays gapless behind already scheduled audio", () => {
  const scheduled = resolveTtsChunkSchedule(2, 8);
  assert.equal(scheduled.startAt, 8);
});

test("playback has no configurable line delay or persistent audio cache", () => {
  assert.doesNotMatch(ttsSource, /ttsDelaySeconds|localStorage|IndexedDB/);
  assert.match(source, /Math\.max\(currentTime, nextPlayTime\)/);
});

// ── stretchSamples unit tests ──

test("stretchSamples at rate=1 returns same-length copy", () => {
  const input = new Float32Array(2048);
  for (let i = 0; i < input.length; i++) input[i] = Math.sin(i * 0.01);
  const result = stretchSamples(input, 1);
  assert.equal(result.length, input.length);
});

test("stretchSamples at rate=2 returns roughly half the samples", () => {
  const input = new Float32Array(4096);
  for (let i = 0; i < input.length; i++) input[i] = Math.sin(i * 0.01);
  const result = stretchSamples(input, 2);
  const ratio = result.length / input.length;
  assert.ok(ratio > 0.45 && ratio < 0.55, `expected ~0.5, got ${ratio}`);
});

test("stretchSamples at rate=1.5 preserves approximate amplitude", () => {
  const input = new Float32Array(4096);
  for (let i = 0; i < input.length; i++) input[i] = 0.7 * Math.sin(i * 0.02);
  const result = stretchSamples(input, 1.5);
  const maxIn = Math.max(...input.map(Math.abs));
  const maxOut = Math.max(...result.map(Math.abs));
  // Amplitude should not clip or distort significantly
  assert.ok(maxOut > 0.3 && maxOut < 1.0, `max amplitude = ${maxOut}`);
});

test("stretchSamples at rate=0.5 returns roughly double the samples", () => {
  const input = new Float32Array(2048);
  for (let i = 0; i < input.length; i++) input[i] = Math.sin(i * 0.01);
  const result = stretchSamples(input, 0.5);
  assert.ok(result.length > input.length * 1.8);
});

// ── source-code shape tests for text-to-speech.ts ──

test("playPcmChunk imports and uses stretchSamples", () => {
  assert.match(ttsSource, /import.*stretchSamples.*from.*audio-stretch/);
  assert.match(ttsSource, /stretchSamples\(/);
});

test("playPcmChunk sets detune=0 when using time-stretch", () => {
  // The stretch path should NOT use the old detune pitch-correction.
  assert.match(ttsSource, /source\.detune\.value\s*=\s*0/);
});

test("playPcmChunk uses buffer.duration directly when stretched", () => {
  // Duration from stretched buffer, not divided by playbackRate.
  assert.match(ttsSource, /const duration\s*=\s*buffer\.duration[^/]/);
});
