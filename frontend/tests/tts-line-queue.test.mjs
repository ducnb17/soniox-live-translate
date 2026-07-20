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

test("a complete later line waits until the preceding line is complete and taken", () => {
  const queue = new StrictLineAudioQueue();
  queue.registerLine(1);
  queue.registerLine(2);
  queue.addChunk(2, "line-2-audio", true);

  assert.equal(queue.takeReady(1), null);
  assert.equal(queue.lineCount, 2);

  queue.addChunk(1, "line-1-part-1", false);
  queue.addChunk(1, "line-1-part-2", true);
  assert.deepEqual(queue.takeReady(1), {
    lineId: 1,
    chunks: ["line-1-part-1", "line-1-part-2"],
    done: true,
  });
  assert.equal(queue.takeReady(2), null);
  assert.equal(queue.activeLineId, 1);
  assert.equal(queue.finishLine(1), true);
  assert.deepEqual(queue.takeReady(2), {
    lineId: 2,
    chunks: ["line-2-audio"],
    done: true,
  });
});

test("the queue is unbounded and never drops old lines", () => {
  const queue = new StrictLineAudioQueue();
  for (let lineId = 1; lineId <= 1000; lineId += 1) {
    queue.addChunk(lineId, lineId, true);
  }

  assert.equal(queue.lineCount, 1000);
  let playedLineCount = 0;
  for (let lineId = 1; lineId <= 1000; lineId += 1) {
    assert.equal(queue.takeReady(lineId).lineId, lineId);
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
});

test("backlog estimate scales with every queued line", () => {
  const queue = new StrictLineAudioQueue();
  queue.addChunk(1, 5, true);
  queue.registerLine(2);
  queue.addChunk(3, 2, false);

  assert.equal(queue.estimatedAudioSeconds((seconds) => seconds, 3), 11);
});
