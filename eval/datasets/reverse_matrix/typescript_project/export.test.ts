import { canExport, exportHeader, normalizeLocale } from "./export";

if (!canExport(10_000) || canExport(10_001)) throw new Error("row boundary");
if (exportHeader() !== "account_id,display_name") throw new Error("header");
if (normalizeLocale() !== "en-US") throw new Error("locale fallback");
