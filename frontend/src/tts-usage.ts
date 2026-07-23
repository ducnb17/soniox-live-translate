export interface TtsUsageEvent {
  characters: number;
  estimated_cost_usd: number;
}

export interface TtsUsageTotals {
  characters: number;
  estimatedCostUsd: number;
}

export function emptyTtsUsage(): TtsUsageTotals {
  return { characters: 0, estimatedCostUsd: 0 };
}

export function addTtsUsage(
  totals: TtsUsageTotals,
  usage: TtsUsageEvent,
): TtsUsageTotals {
  return {
    characters: totals.characters + usage.characters,
    estimatedCostUsd: totals.estimatedCostUsd + usage.estimated_cost_usd,
  };
}

export function formatTtsCostHint(
  ratePerMillionCharacters: number,
  totals: TtsUsageTotals,
): string {
  return (
    `Rate: ~$${ratePerMillionCharacters}/million chars · ` +
    `Session: ${totals.characters} chars · est. $${totals.estimatedCostUsd.toFixed(6)}`
  );
}
