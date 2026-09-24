/**
 * Credential redaction for anything the UI displays or lets a viewer copy.
 * Keys that look like credentials have their non-numeric values replaced; the
 * answer file's numeric `tokens` count is a usage metric, not a secret, and is
 * left intact. String values that look like bearer tokens or API keys are
 * redacted whatever their key.
 */
export const REDACTED = "[REDACTED]";

const CREDENTIAL_KEY = /(token|secret|password|passwd|pwd|api[_-]?key|apikey|authorization|auth[_-]?header|bearer|credential|private[_-]?key|cookie|session[_-]?id)/i;
const CREDENTIAL_VALUE = /^(bearer\s+\S+|basic\s+[A-Za-z0-9+/=]{8,}|sk-[A-Za-z0-9_-]{8,}|gh[opsu]_[A-Za-z0-9]{16,}|gsk_[A-Za-z0-9]{16,}|xox[abp]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})$/i;

export function isCredentialKey(key: string): boolean {
  return CREDENTIAL_KEY.test(key);
}

export function redact<T>(value: T): T {
  return walk(value, undefined) as T;
}

function walk(value: unknown, key: string | undefined): unknown {
  if (key !== undefined && isCredentialKey(key) && value !== null && typeof value !== "number" && typeof value !== "boolean") {
    return REDACTED;
  }
  if (typeof value === "string") return CREDENTIAL_VALUE.test(value.trim()) ? REDACTED : value;
  if (Array.isArray(value)) return value.map((item) => walk(item, undefined));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) out[k] = walk(v, k);
    return out;
  }
  return value;
}

/** Redact a free-text diagnostic line (key=value / "key": "value" shapes). */
export function redactText(text: string): string {
  return text
    .replace(/\b(bearer)\s+[A-Za-z0-9._~+/=-]+/gi, `$1 ${REDACTED}`)
    .replace(/((?:token|secret|password|api[_-]?key|authorization)["']?\s*[:=]\s*["']?)[^"',\s}]+/gi, `$1${REDACTED}`);
}

export function countRedactions(value: unknown): number {
  return (JSON.stringify(redact(value)).match(/\[REDACTED\]/g) ?? []).length - (JSON.stringify(value).match(/\[REDACTED\]/g) ?? []).length;
}
