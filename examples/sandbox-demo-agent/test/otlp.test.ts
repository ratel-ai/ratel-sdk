import assert from "node:assert/strict";
import { test } from "node:test";
import { otlpEndpoint } from "../src/otlp.js";

test("the traces go to the Ratel that started the run: RATEL_BASE_URL of the container", () => {
  assert.equal(
    otlpEndpoint({ RATEL_BASE_URL: "https://ratel.example/api/v1/" }),
    "https://ratel.example/api/v1/traces",
  );
});

test("RATEL_OTLP_ENDPOINT wins, and with neither there is no export", () => {
  const env = { RATEL_OTLP_ENDPOINT: "http://collector/v1/traces", RATEL_BASE_URL: "https://x/api/v1" };
  assert.equal(otlpEndpoint(env), "http://collector/v1/traces");
  assert.equal(otlpEndpoint({}), undefined);
  assert.equal(otlpEndpoint({ RATEL_BASE_URL: "" }), undefined);
});
