export interface TtsUsageEvent {
  characters: number;
  estimated_cost_usd: number;
  cache_hit: boolean;
}

export interface TtsUsageTotals {
  characters: number;
  estimatedCostUsd: number;
  cacheHits: number;
}

export function emptyTtsUsage(): TtsUsageTotals {
  return { characters: 0, estimatedCostUsd: 0, cacheHits: 0 };
}

export function addTtsUsage(
  totals: TtsUsageTotals,
  usage: TtsUsageEvent,
): TtsUsageTotals {
  return {
    characters: totals.characters + usage.characters,
    estimatedCostUsd: totals.estimatedCostUsd + usage.estimated_cost_usd,
    cacheHits: totals.cacheHits + (usage.cache_hit ? 1 : 0),
  };
}

export function formatTtsCostHint(
  ratePerMillionCharacters: number,
  totals: TtsUsageTotals,
): string {
  const cacheText = totals.cacheHits ? ` · ${totals.cacheHits} cache hit` : "";
  return (
    `Rate: ~$${ratePerMillionCharacters}/million chars · ` +
    `Session: ${totals.characters} chars · est. $${totals.estimatedCostUsd.toFixed(6)}${cacheText}`
  );
}
