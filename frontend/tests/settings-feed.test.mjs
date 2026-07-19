import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/app.ts", import.meta.url), "utf8");

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
  assert.match(app, /data\.type === "line_ready"/);
  assert.match(app, /handleLineReady\(data\)/);
});
