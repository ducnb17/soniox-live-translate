import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import ts from "typescript";

const source = readFileSync(new URL("../src/sentence-lines.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const compiledModule = { exports: {} };
new Function("exports", "module", outputText)(compiledModule.exports, compiledModule);
const { formatDisplayLines, splitDisplayLines } = compiledModule.exports;

test("each completed sentence is displayed on its own line", () => {
  assert.deepEqual(
    splitDisplayLines("Xin chào. Bạn khỏe không? Tôi vẫn khỏe!"),
    ["Xin chào.", "Bạn khỏe không?", "Tôi vẫn khỏe!"],
  );
});

test("a long unfinished sentence wraps at whole-word boundaries", () => {
  const text = "một hai ba bốn năm sáu bảy tám chín mười";
  const lines = splitDisplayLines(text, 14);
  assert.equal(lines.join(" "), text);
  assert.ok(lines.every((line) => line.length <= 14));
});

test("partial updates reuse a newline-delimited display string", () => {
  assert.equal(formatDisplayLines("Câu một. Câu đang tới"), "Câu một.\nCâu đang tới");
});
