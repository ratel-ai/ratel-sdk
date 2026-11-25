import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { defineSandbox, toManifest } from "../src/index.js";

const require = createRequire(import.meta.url);
const Ajv = require("ajv/dist/2020.js").default;
const addFormats = require("ajv-formats").default;

const CONTRACTS = fileURLToPath(new URL("../../../../protocol/", import.meta.url));
const ROOT = `${CONTRACTS}sandbox/v1/`;
const ajv = new Ajv({ allErrors: true, strict: true, strictTypes: false, strictRequired: false });
addFormats(ajv);
const read = (path: string) => JSON.parse(readFileSync(`${CONTRACTS}${path}`, "utf8"));
const schemasOf = (contract: string) =>
  readdirSync(`${CONTRACTS}${contract}/schema`).map((file) => file.replace(".schema.json", ""));
for (const contract of ["sandbox/v1", "evals/v1"]) {
  for (const name of schemasOf(contract))
    ajv.addSchema(read(`${contract}/schema/${name}.schema.json`));
}
const validator = (name: string, contract = "sandbox/v1") =>
  ajv.getSchema(`https://ratel.sh/protocol/${contract}/${name}.schema.json`);

describe.each(["sandbox/v1", "evals/v1"])("%s", (contract) => {
  const schemas = schemasOf(contract);
  describe.each(["valid", "invalid"] as const)("%s vectors", (kind) => {
    const files = readdirSync(`${CONTRACTS}${contract}/conformance/${kind}`);
    it.each(files)("%s", (file) => {
      const schema = schemas.find((name) => file.startsWith(`${name}.`));
      expect(schema).toBeDefined();
      const validate = validator(schema ?? "", contract);
      const value = read(`${contract}/conformance/${kind}/${file}`);
      expect(validate(value), JSON.stringify(validate.errors)).toBe(kind === "valid");
    });
    it("covers every schema", () => {
      for (const name of schemas) {
        expect(
          files.some((file) => file.startsWith(`${name}.`)),
          name,
        ).toBe(true);
      }
    });
  });
});

describe("the manifest of a definition", () => {
  it("passes the schema", () => {
    const manifest = toManifest(
      defineSandbox({
        name: "support-agent",
        entry: "src/agent.ts",
        secrets: ["OPENAI_API_KEY"],
        tools: {
          lookup: { sideEffect: false, execute: () => 1 },
          refund: { sideEffect: true, execute: () => 1, fake: () => 2 },
        },
      }),
    );
    const validate = validator("manifest");
    expect(validate(manifest), JSON.stringify(validate.errors)).toBe(true);
    expect(manifest.tools).toEqual([
      { name: "lookup", sideEffect: false, fake: false },
      { name: "refund", sideEffect: true, fake: true },
    ]);
  });
});

it("the contract does not know why a run starts", () => {
  const texts = [
    ...readdirSync(`${ROOT}schema`).map((file) => readFileSync(`${ROOT}schema/${file}`, "utf8")),
    // The CLI has the commands of both contracts, and `src/eval/` is the other one.
    ...readdirSync(new URL("../src/", import.meta.url))
      .filter((file) => file.endsWith(".ts") && file !== "cli.ts")
      .map((file) => readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8")),
  ];
  for (const text of texts) {
    expect(text).not.toMatch(/\beval(s|uation|uations)?\b|\bcases?\b|\bscorers?\b/i);
  }
});
