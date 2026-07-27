export type ConversationExportFormat = "txt" | "srt" | "json";

export interface ConversationSummary {
  id: string;
  started_at: number;
  ended_at: number | null;
  mode: string;
  source_lang: string | null;
  target_lang: string;
  title: string | null;
  segment_count: number;
  preview: string;
}

export interface ConversationSegment {
  id: number;
  speaker_label: string | null;
  source_lang: string | null;
  original_text: string;
  translated_text: string | null;
  started_at_ms: number | null;
  ended_at_ms: number | null;
  is_final: number;
}

export interface ConversationDetail {
  id: string;
  started_at: number;
  ended_at: number | null;
  mode: string;
  source_lang: string | null;
  target_lang: string;
  title: string | null;
  segments: ConversationSegment[];
  connection_events: unknown[];
}

export interface ConversationPage {
  items: ConversationSummary[];
  hasMore: boolean;
}

export interface RetentionStats {
  conversations: number;
  segments: number;
  db_size_mb: number;
}

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

async function checkedFetch(fetcher: Fetcher, input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = await fetcher(input, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`.trim();
    try {
      const body = await response.json() as { error?: string };
      if (body.error) detail = body.error;
    } catch { /* response was not JSON */ }
    throw new Error(detail);
  }
  return response;
}

export function conversationPageUrl(query: string, offset: number, pageSize: number): string {
  const trimmed = query.trim();
  const path = trimmed ? "/api/conversations/search" : "/api/conversations";
  const params = new URLSearchParams({
    limit: String(pageSize + 1),
    offset: String(offset),
  });
  if (trimmed) params.set("q", trimmed);
  return `${path}?${params.toString()}`;
}

export async function fetchConversationPage(
  query: string,
  offset: number,
  pageSize: number,
  fetcher: Fetcher = fetch,
): Promise<ConversationPage> {
  const response = await checkedFetch(fetcher, conversationPageUrl(query, offset, pageSize));
  const rows = await response.json() as ConversationSummary[];
  return { items: rows.slice(0, pageSize), hasMore: rows.length > pageSize };
}

export async function fetchConversation(
  id: string,
  fetcher: Fetcher = fetch,
): Promise<ConversationDetail> {
  const response = await checkedFetch(fetcher, `/api/conversations/${encodeURIComponent(id)}`);
  return response.json() as Promise<ConversationDetail>;
}

export async function deleteConversation(id: string, fetcher: Fetcher = fetch): Promise<void> {
  await checkedFetch(fetcher, `/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function fetchConversationExport(
  id: string,
  format: ConversationExportFormat,
  fetcher: Fetcher = fetch,
): Promise<{ blob: Blob; filename: string }> {
  const response = await checkedFetch(
    fetcher,
    `/api/conversations/${encodeURIComponent(id)}/export?format=${format}`,
  );
  const disposition = response.headers.get("content-disposition") || "";
  const matchedName = /filename="?([^";]+)"?/i.exec(disposition)?.[1];
  return {
    blob: await response.blob(),
    filename: matchedName || `conversation-${id}.${format}`,
  };
}

export async function cleanupConversations(days: number, fetcher: Fetcher = fetch): Promise<number> {
  const response = await checkedFetch(
    fetcher,
    `/api/retention/cleanup?max_age_days=${encodeURIComponent(String(days))}`,
    { method: "POST" },
  );
  const result = await response.json() as { deleted: number };
  return result.deleted;
}

export async function fetchRetentionStats(fetcher: Fetcher = fetch): Promise<RetentionStats> {
  const response = await checkedFetch(fetcher, "/api/retention/stats");
  return response.json() as Promise<RetentionStats>;
}
