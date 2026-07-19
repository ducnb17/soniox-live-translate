import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import ts from "typescript";

const source = readFileSync(new URL("../src/device-selection.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const compiledModule = { exports: {} };
new Function("exports", "module", outputText)(compiledModule.exports, compiledModule);
const { resolveAudioDevices } = compiledModule.exports;

const device = (kind, deviceId, label) => ({ kind, deviceId, label });

test("empty enumerateDevices result falls back both saved selections without throwing", () => {
  const result = resolveAudioDevices([], "unplugged-mic", "unplugged-speaker");

  assert.equal(result.inputId, "default");
  assert.equal(result.outputId, "default");
  assert.equal(result.missingInput, true);
  assert.equal(result.missingOutput, true);
  assert.deepEqual(result.inputs, []);
  assert.deepEqual(result.outputs, []);
});

test("missing saved microphone falls back while an available speaker remains selected", () => {
  const devices = [
    device("audioinput", "replacement-mic", "USB Mic"),
    device("audiooutput", "saved-speaker", "CABLE Input (VB-Audio Virtual Cable)"),
  ];
  const result = resolveAudioDevices(devices, "unplugged-mic", "saved-speaker");

  assert.equal(result.inputId, "default");
  assert.equal(result.outputId, "saved-speaker");
  assert.equal(result.missingInput, true);
  assert.equal(result.missingOutput, false);
});

test("physical and virtual devices remain available and the pseudo default is not duplicated", () => {
  const devices = [
    device("audioinput", "default", "Default - Microphone"),
    device("audioinput", "usb-mic", "USB Mic"),
    device("audioinput", "vb-cable", "CABLE Output (VB-Audio Virtual Cable)"),
    device("audioinput", "vb-cable", "duplicate"),
    device("audiooutput", "speakers", "Realtek Speakers"),
  ];
  const result = resolveAudioDevices(devices, "vb-cable", "speakers");

  assert.deepEqual(result.inputs.map(({ deviceId }) => deviceId), ["usb-mic", "vb-cable"]);
  assert.deepEqual(result.outputs.map(({ deviceId }) => deviceId), ["speakers"]);
  assert.equal(result.inputId, "vb-cable");
  assert.equal(result.outputId, "speakers");
  assert.equal(result.missingInput, false);
  assert.equal(result.missingOutput, false);
});
