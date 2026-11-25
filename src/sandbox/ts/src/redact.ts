export const REDACTED = "[redacted]";

/** Shorter values are ordinary words and numbers: replacing them would destroy the output. */
const MIN_SECRET_LENGTH = 4;
/** A line of a secret with many lines. A shorter line can be an ordinary word or a bracket. */
const MIN_LINE_LENGTH = 8;

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
  let rest: string[] = [];
  return {
    push(chunk: string) {
      const lines = chunk.split("\n");
      for (const line of lines.slice(0, -1)) {
        write(`${redactor.text(rest.join("") + line)}\n`);
        rest = [];
      }
      rest.push(lines.at(-1) ?? "");
    },
    end() {
      const line = rest.join("");
      if (line) write(redactor.text(line));
      rest = [];
    },
  };
}

/** A secret as it is, trimmed, inside JSON text, in a URL, in base64, and each of its lines. */
function variants(secret: string): string[] {
  const trimmed = secret.trim();
  return [
    secret,
    trimmed,
    JSON.stringify(secret).slice(1, -1),
    encodeURIComponent(secret),
    Buffer.from(secret).toString("base64"),
    // Process output is redacted by line, so a secret with many lines never shows whole there.
    ...secret.split(/\r?\n/).filter((line) => line.length >= MIN_LINE_LENGTH),
  ];
}
