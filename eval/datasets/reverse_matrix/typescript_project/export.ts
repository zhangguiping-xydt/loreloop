export const EXPORT_COLUMNS = ["account_id", "display_name"] as const;
export const MAX_EXPORT_ROWS = 10_000;
export const DEFAULT_LOCALE = "en-US";

export function canExport(rowCount: number): boolean {
  return rowCount >= 0 && rowCount <= MAX_EXPORT_ROWS;
}

export function exportHeader(): string {
  return EXPORT_COLUMNS.join(",");
}

export function normalizeLocale(locale?: string): string {
  return locale || DEFAULT_LOCALE;
}
