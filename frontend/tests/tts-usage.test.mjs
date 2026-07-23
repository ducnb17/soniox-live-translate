import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import ts from "typescript";

const source = readFileSync(new URL("../src/tts-usage.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const compiledModule = { exports: {} };
new Function("exports", "module", outputText)(compiledModule.exports, compiledModule);
const { addTtsUsage, emptyTtsUsage, formatTtsCostHint } = compiledModule.exports;

test("session cost is accumulated from every synthesized phrase", () => {
  let totals = emptyTtsUsage();
  totals = addTtsUsage(totals, {
    characters: 100,
    estimated_cost_usd: 0.0015,
  });
  totals = addTtsUsage(totals, {
    characters: 100,
    estimated_cost_usd: 0.0015,
  });

  assert.deepEqual(totals, {
    characters: 200,
    estimatedCostUsd: 0.003,
  });
  assert.equal(
    formatTtsCostHint(15, totals),
    "Rate: ~$15/million chars · Session: 200 chars · est. $0.003000",
  );
});
