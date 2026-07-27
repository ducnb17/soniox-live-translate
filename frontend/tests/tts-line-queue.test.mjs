import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import ts from "typescript";

const source = readFileSync(new URL("../src/tts-line-queue.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const compiledModule = { exports: {} };
new Function("exports", "module", outputText)(compiledModule.exports, compiledModule);
const { StrictLineAudioQueue } = compiledModule.exports;

test("a line can start streaming before all of its chunks have arrived", () => {
  const queue = new StrictLineAudioQueue();
  queue.registerLine(1);

  // Registration alone must not activate the line.
  assert.equal(queue.takeReady(1), null);

  queue.addChunk(1, "line-1-part-1", false);

  // Line 1 is next in sequence and has at least one chunk — it should
  // activate immediately, without waiting for `done`.
  const line = queue.takeReady(1);
  assert.equal(line.lineId, 1);
  assert.equal(queue.activeLineId, 1);

  // First chunk is available right away.
  assert.deepEqual(queue.takeNextChunk(), { chunk: "line-1-part-1", isLast: false });
  // No more chunks yet.
  assert.equal(queue.takeNextChunk(), null);

  // More audio arrives for the active line while it's already playing.
  queue.addChunk(1, "line-1-part-2", true);
  assert.deepEqual(queue.takeNextChunk(), { chunk: "line-1-part-2", isLast: true });
  assert.equal(queue.takeNextChunk(), null);

  assert.equal(queue.finishLine(1), true);
});

test("a later line waits until the preceding line has audio and is finished", () => {
  const queue = new StrictLineAudioQueue();
  queue.registerLine(1);
  queue.registerLine(2);
  queue.addChunk(2, "line-2-audio", true);

  // Line 1 is next in sequence but has no audio yet, so line 2 must wait.
  assert.equal(queue.takeReady(1), null);
  assert.equal(queue.takeReady(2), null);

  queue.addChunk(1, "line-1-audio", true);
  const line1 = queue.takeReady(1);
  assert.equal(line1.lineId, 1);
  assert.equal(queue.takeReady(2), null);

  assert.deepEqual(queue.takeNextChunk(), { chunk: "line-1-audio", isLast: true });
  assert.equal(queue.finishLine(1), true);

  const line2 = queue.takeReady(2);
  assert.equal(line2.lineId, 2);
  assert.deepEqual(queue.takeNextChunk(), { chunk: "line-2-audio", isLast: true });
  assert.equal(queue.finishLine(2), true);
});

test("the queue is unbounded and never drops old lines", () => {
  const queue = new StrictLineAudioQueue();
  for (let lineId = 1; lineId <= 1000; lineId += 1) {
    queue.addChunk(lineId, lineId, true);
  }

  assert.equal(queue.lineCount, 1000);
  let playedLineCount = 0;
  for (let lineId = 1; lineId <= 1000; lineId += 1) {
    const line = queue.takeReady(lineId);
    assert.equal(line.lineId, lineId);
    assert.deepEqual(queue.takeNextChunk(), { chunk: lineId, isLast: true });
    assert.equal(queue.finishLine(lineId), true);
    playedLineCount += 1;
  }
  assert.equal(playedLineCount, 1000);
  assert.equal(queue.lineCount, 0);
});

test("clear removes every waiting line for explicit barge-in", () => {
  const queue = new StrictLineAudioQueue();
  queue.addChunk(4, "four", true);
  queue.addChunk(5, "five", true);

  queue.clear();

  assert.equal(queue.lineCount, 0);
  assert.equal(queue.firstLineId, null);
  assert.equal(queue.activeLineId, null);
});

test("backlog estimate scales with every queued line behind the active one", () => {
  const queue = new StrictLineAudioQueue();
  queue.addChunk(1, 5, true);
  queue.registerLine(2);
  queue.addChunk(3, 2, false);

  assert.equal(queue.estimatedAudioSeconds((seconds) => seconds, 3), 11);
});

test("a queued later line becomes playable once the head line receives its first chunk", () => {
  const queue = new StrictLineAudioQueue();
  queue.registerLine(10);
  queue.addChunk(11, "line-11-audio", true);

  // The head line blocks strict ordering, but zero-chunk registration alone
  // must not consume the active slot.
  assert.equal(queue.takeReady(10), null);
  assert.equal(queue.activeLineId, null);

  queue.addChunk(10, "line-10-audio", true);
  const line10 = queue.takeReady(10);
  assert.equal(line10.lineId, 10);
  assert.deepEqual(queue.takeNextChunk(), { chunk: "line-10-audio", isLast: true });
  assert.equal(queue.finishLine(10), true);

  const line11 = queue.takeReady(11);
  assert.equal(line11.lineId, 11);
  assert.deepEqual(queue.takeNextChunk(), { chunk: "line-11-audio", isLast: true });
  assert.equal(queue.finishLine(11), true);
});
