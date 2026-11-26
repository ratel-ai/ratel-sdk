/** Text from Ratel or a container, safe for a terminal: no control characters but new lines and tabs. */
export function safe(text: string): string {
  // biome-ignore lint/suspicious/noControlCharactersInRegex: this removes them
  return text.replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g, " ");
}

/** One line of text, safe for a terminal. */
export function oneLine(text: string): string {
  return safe(text).replace(/[\n\t\u2028\u2029]/g, " ");
}

/** A value for a person: text as it is, else indented JSON. */
export function pretty(value: unknown): string {
  return typeof value === "string" ? safe(value) : safe(JSON.stringify(value, null, 2) ?? "null");
}

/** Short JSON on one line, cut at `max` characters. */
export function short(value: unknown, max = 200): string {
  const text = oneLine(JSON.stringify(value) ?? "null");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** The logs of a container, indented, with a title. Empty logs: a line that says so. */
export function logsBlock(logs: string | undefined): string {
  const lines = safe(logs ?? "").replace(/\n+$/, "");
  if (!lines) return "container logs: (empty)\n";
  return `container logs:\n${lines
    .split("\n")
    .map((line) => `  ${line}`)
    .join("\n")}\n`;
}

export function megabytes(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}
