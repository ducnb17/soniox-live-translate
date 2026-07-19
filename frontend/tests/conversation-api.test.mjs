import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import ts from "typescript";

const source = readFileSync(new URL("../src/conversation-api.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const compiledModule = { exports: {} };
new Function("exports", "module", outputText)(compiledModule.exports, compiledModule);
const {
  cleanupConversations,
  conversationPageUrl,
  fetchConversationExport,
  fetchConversationPage,
} = compiledModule.exports;

const summary = (id) => ({
  id,
  started_at: 1000,
  ended_at: 2000,
  mode: "one_way",
  source_lang: "en",
  target_lang: "vi",
  title: null,
  segment_count: 1,
  preview: `preview ${id}`,
});

test("history list URL requests one extra row and carries the offset", () => {
  assert.equal(conversationPageUrl("", 20, 10), "/api/conversations?limit=11&offset=20");
});

test("search URL uses the FTS endpoint and encodes the keyword", () => {
  assert.equal(
    conversationPageUrl("  xin chào database  ", 10, 10),
    "/api/conversations/search?limit=11&offset=10&q=xin+ch%C3%A0o+database",
  );
});

test("page integration trims the extra row into a hasMore flag", async () => {
  let requestedUrl = "";
  const fakeFetch = async (url) => {
    requestedUrl = String(url);
    return new Response(JSON.stringify([summary("3"), summary("2"), summary("1")]), {
      headers: { "content-type": "application/json" },
    });
  };

  const page = await fetchConversationPage("keyword", 4, 2, fakeFetch);

  assert.equal(requestedUrl, "/api/conversations/search?limit=3&offset=4&q=keyword");
  assert.deepEqual(page.items.map(({ id }) => id), ["3", "2"]);
  assert.equal(page.hasMore, true);
});

for (const format of ["txt", "srt", "json"]) {
  test(`export ${format} downloads the response body and server filename`, async () => {
    const body = `${format} exported conversation content`;
    const fakeFetch = async (url) => {
      assert.equal(url, `/api/conversations/conversation-1/export?format=${format}`);
      return new Response(body, {
        headers: { "content-disposition": `attachment; filename="saved.${format}"` },
      });
    };

    const exported = await fetchConversationExport("conversation-1", format, fakeFetch);

    assert.equal(exported.filename, `saved.${format}`);
    assert.equal(await exported.blob.text(), body);
  });
}

test("manual retention cleanup posts the selected number of days", async () => {
  let request;
  const fakeFetch = async (url, init) => {
    request = { url, init };
    return new Response(JSON.stringify({ deleted: 7 }), {
      headers: { "content-type": "application/json" },
    });
  };

  const deleted = await cleanupConversations(45, fakeFetch);

  assert.equal(deleted, 7);
  assert.deepEqual(request, {
    url: "/api/retention/cleanup?max_age_days=45",
    init: { method: "POST" },
  });
});
