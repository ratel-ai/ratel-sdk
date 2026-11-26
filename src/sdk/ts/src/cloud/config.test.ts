import { describe, expect, it } from "vitest";
import {
  API_KEY_ENV,
  BASE_URL_ENV,
  DEFAULT_BASE_URL,
  DEFAULT_TIMEOUT_MS,
  resolveCloudConfig,
} from "./config.js";
import * as cloud from "./index.js";

describe("resolveCloudConfig", () => {
  it("exports the resolver and the env names from @ratel-ai/sdk/cloud", () => {
    expect(cloud.resolveCloudConfig).toBe(resolveCloudConfig);
    expect(API_KEY_ENV).toBe("RATEL_API_KEY");
    expect(BASE_URL_ENV).toBe("RATEL_BASE_URL");
  });

  describe("apiKey", () => {
    it("uses the explicit option over the env", () => {
      const resolved = resolveCloudConfig({ apiKey: "rtl_opt" }, { RATEL_API_KEY: "rtl_env" });
      expect(resolved.apiKey).toBe("rtl_opt");
    });

    it("falls back to RATEL_API_KEY", () => {
      expect(resolveCloudConfig({}, { RATEL_API_KEY: "rtl_env" }).apiKey).toBe("rtl_env");
    });

    it("is undefined with no option and no env", () => {
      expect(resolveCloudConfig({}, {}).apiKey).toBeUndefined();
    });

    it("treats an empty env value as unset", () => {
      expect(resolveCloudConfig({}, { RATEL_API_KEY: "" }).apiKey).toBeUndefined();
    });
  });

  describe("baseUrl", () => {
    it("uses the explicit option over the env", () => {
      const resolved = resolveCloudConfig(
        { baseUrl: "http://opt.test/api/v1" },
        { RATEL_BASE_URL: "http://env.test/api/v1" },
      );
      expect(resolved.baseUrl).toBe("http://opt.test/api/v1");
    });

    it("falls back to RATEL_BASE_URL", () => {
      const resolved = resolveCloudConfig({}, { RATEL_BASE_URL: "http://env.test/api/v1" });
      expect(resolved.baseUrl).toBe("http://env.test/api/v1");
    });

    it("falls back to DEFAULT_BASE_URL", () => {
      expect(resolveCloudConfig({}, {}).baseUrl).toBe(DEFAULT_BASE_URL);
      expect(resolveCloudConfig({}, { RATEL_BASE_URL: "" }).baseUrl).toBe(DEFAULT_BASE_URL);
    });

    it("removes trailing slashes", () => {
      expect(resolveCloudConfig({ baseUrl: "http://opt.test/api/v1//" }, {}).baseUrl).toBe(
        "http://opt.test/api/v1",
      );
      expect(resolveCloudConfig({}, { RATEL_BASE_URL: "http://env.test/" }).baseUrl).toBe(
        "http://env.test",
      );
    });
  });

  it("passes fetch and timeoutMs through, with defaults", () => {
    const fetchImpl = (async () => new Response()) as typeof fetch;
    const resolved = resolveCloudConfig({ fetch: fetchImpl, timeoutMs: 5 }, {});
    expect(resolved.fetch).toBe(fetchImpl);
    expect(resolved.timeoutMs).toBe(5);

    const defaults = resolveCloudConfig({}, {});
    expect(defaults.fetch).toBe(globalThis.fetch);
    expect(defaults.timeoutMs).toBe(DEFAULT_TIMEOUT_MS);
  });

  it("reads process.env by default", () => {
    const previous = process.env.RATEL_BASE_URL;
    process.env.RATEL_BASE_URL = "http://process.test/api/v1";
    try {
      expect(resolveCloudConfig().baseUrl).toBe("http://process.test/api/v1");
    } finally {
      if (previous === undefined) delete process.env.RATEL_BASE_URL;
      else process.env.RATEL_BASE_URL = previous;
    }
  });
});
