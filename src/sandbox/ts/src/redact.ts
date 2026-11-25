export const REDACTED = "[redacted]";

/** Shorter values are ordinary words and numbers: replacing them would destroy the output. */
const MIN_SECRET_LENGTH = 4;

export type Redactor = {
  text(value: string): string;
  /** Every string of a JSON value, object keys too. */
  json<T>(value: T): T;
};

export function createRedactor(secrets: readonly string[]): Redactor {
  const values = [...new Set(secrets.flatMap(variants))]
    .filter((value) => value.length >= MIN_SECRET_LENGTH)
    .sort((a, b) => b.length - a.length);
  const text = (value: string) =>
    values.reduce((rest, secret) => rest.replaceAll(secret, REDACTED), value);
  const json = (value: unknown): unknown => {
    if (typeof value === "string") return text(value);
    if (typeof value === "number" && values.includes(String(value))) return REDACTED;
    if (Array.isArray(value)) return value.map(json);
    if (value === null || typeof value !== "object") return value;
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [text(key), json(item)]));
  };
  return { text, json: json as Redactor["json"] };
}

/** Writes whole lines, so a secret that spans two chunks is replaced too. */
export function redactLines(redactor: Redactor, write: (line: string) => void) {
  let rest = "";
  return {
    push(chunk: string) {
      const lines = (rest + chunk).split("\n");
      rest = lines.pop() ?? "";
      for (const line of lines) write(`${redactor.text(line)}\n`);
    },
    end() {
      if (rest) write(redactor.text(rest));
      rest = "";
    },
  };
}

/** A secret as it is, trimmed, inside JSON text, in a URL and in base64. */
function variants(secret: string): string[] {
  const trimmed = secret.trim();
  return [
    secret,
    trimmed,
    JSON.stringify(secret).slice(1, -1),
    encodeURIComponent(secret),
    Buffer.from(secret).toString("base64"),
  ];
}
