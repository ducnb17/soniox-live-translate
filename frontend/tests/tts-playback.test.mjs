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

test("a late chunk from the same line does not receive the line delay again", () => {
  const delaySeconds = 4;
  const first = resolveTtsChunkSchedule(0, 0, null, 17, delaySeconds);
  const firstChunkEnd = first.startAt + 0.25;

  // Simulate the second chunk arriving well after the first chunk finished.
  const lateCurrentTime = firstChunkEnd + 2;
  const second = resolveTtsChunkSchedule(
    lateCurrentTime,
    firstChunkEnd,
    first.currentLineId,
    17,
    delaySeconds,
  );

  assert.equal(first.isNewLine, true);
  assert.equal(first.startAt, delaySeconds);
  assert.equal(second.isNewLine, false);
  assert.equal(second.startAt, lateCurrentTime);
  assert.notEqual(second.startAt, lateCurrentTime + delaySeconds);
});

test("a real new line waits for queued audio and then applies line delay once", () => {
  const scheduled = resolveTtsChunkSchedule(2, 8, 17, 18, 3);

  assert.equal(scheduled.isNewLine, true);
  assert.equal(scheduled.currentLineId, 18);
  assert.equal(scheduled.startAt, 11);
});
