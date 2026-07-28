"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { isLocalAppUrl, safeExternalHttpUrl } = require("../url-policy");

const BASE_URL = "http://127.0.0.1:8765";

test("local navigation requires an exact origin match", () => {
  assert.equal(isLocalAppUrl("http://127.0.0.1:8765/setup", BASE_URL), true);
  assert.equal(isLocalAppUrl("http://127.0.0.1:8765@evil.example/", BASE_URL), false);
  assert.equal(isLocalAppUrl("http://127.0.0.1:8766/", BASE_URL), false);
  assert.equal(isLocalAppUrl("not a url", BASE_URL), false);
});

test("external navigation only permits HTTP and HTTPS", () => {
  assert.equal(safeExternalHttpUrl("https://soniox.com/"), "https://soniox.com/");
  assert.equal(safeExternalHttpUrl("http://example.com/path"), "http://example.com/path");
  assert.equal(safeExternalHttpUrl("file:///C:/Windows/System32/calc.exe"), null);
  assert.equal(safeExternalHttpUrl("javascript:alert(1)"), null);
  assert.equal(safeExternalHttpUrl("not a url"), null);
});
