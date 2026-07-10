export const DOCUMENT_ROUTE = "/v1/documents";
export const POLL_INTERVAL_MS = 2_000;
export const MAX_DOCUMENT_MIB = 24;

export function progressUrl(id: string): string {
  return `${DOCUMENT_ROUTE}/${id}/progress`;
}
