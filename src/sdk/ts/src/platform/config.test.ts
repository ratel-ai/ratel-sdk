import { describe, expect, it } from "vitest";
import {
  API_KEY_ENV,
  BASE_URL_ENV,
  DEFAULT_BASE_URL,
  DEFAULT_TIMEOUT_MS,
  platformSwitchOn,
  resolvePlatformConfig,
} from "./config.js";
import * as platform from "./index.js";

describe("resolvePlatformConfig", () => {
  it("exports the resolver and the env names from @ratel-ai/sdk/platform", () => {
    expect(platform.resolvePlatformConfig).toBe(resolvePlatformConfig);
    expect(API_KEY_ENV).toBe("RATEL_API_KEY");
    expect(BASE_URL_ENV).toBe("RATEL_BASE_URL");
  });

  describe("apiKey", () => {
    it("uses the explicit option over the env", () => {
      const resolved = resolvePlatformConfig({ apiKey: "rtl_opt" }, { RATEL_API_KEY: "rtl_env" });
      expect(resolved.apiKey).toBe("rtl_opt");
    });

    it("falls back to RATEL_API_KEY", () => {
      expect(resolvePlatformConfig({}, { RATEL_API_KEY: "rtl_env" }).apiKey).toBe("rtl_env");
    });

    it("is undefined with no option and no env", () => {
      expect(resolvePlatformConfig({}, {}).apiKey).toBeUndefined();
    });

    it("treats an empty env value as unset", () => {
      expect(resolvePlatformConfig({}, { RATEL_API_KEY: "" }).apiKey).toBeUndefined();
    });
  });

  describe("baseUrl", () => {
    it("uses the explicit option over the env", () => {
      const resolved = resolvePlatformConfig(
        { baseUrl: "http://opt.test/api/v1" },
        { RATEL_BASE_URL: "http://env.test/api/v1" },
      );
      expect(resolved.baseUrl).toBe("http://opt.test/api/v1");
    });

    it("falls back to RATEL_BASE_URL", () => {
      const resolved = resolvePlatformConfig({}, { RATEL_BASE_URL: "http://env.test/api/v1" });
      expect(resolved.baseUrl).toBe("http://env.test/api/v1");
    });

    it("falls back to DEFAULT_BASE_URL", () => {
      expect(resolvePlatformConfig({}, {}).baseUrl).toBe(DEFAULT_BASE_URL);
      expect(resolvePlatformConfig({}, { RATEL_BASE_URL: "" }).baseUrl).toBe(DEFAULT_BASE_URL);
    });

    it("removes trailing slashes", () => {
      expect(resolvePlatformConfig({ baseUrl: "http://opt.test/api/v1//" }, {}).baseUrl).toBe(
        "http://opt.test/api/v1",
      );
      expect(resolvePlatformConfig({}, { RATEL_BASE_URL: "http://env.test/" }).baseUrl).toBe(
        "http://env.test",
      );
    });
  });

  it("passes fetch and timeoutMs through, with defaults", () => {
    const fetchImpl = (async () => new Response()) as typeof fetch;
    const resolved = resolvePlatformConfig({ fetch: fetchImpl, timeoutMs: 5 }, {});
    expect(resolved.fetch).toBe(fetchImpl);
    expect(resolved.timeoutMs).toBe(5);

    const defaults = resolvePlatformConfig({}, {});
    expect(defaults.fetch).toBe(globalThis.fetch);
    expect(defaults.timeoutMs).toBe(DEFAULT_TIMEOUT_MS);
  });

  it("reads process.env by default", () => {
    const previous = process.env.RATEL_BASE_URL;
    process.env.RATEL_BASE_URL = "http://process.test/api/v1";
    try {
      expect(resolvePlatformConfig().baseUrl).toBe("http://process.test/api/v1");
    } finally {
      if (previous === undefined) delete process.env.RATEL_BASE_URL;
      else process.env.RATEL_BASE_URL = previous;
    }
  });
});

describe("DEFAULT_BASE_URL", () => {
  it("is the Ratel Platform at app.ratel.sh", () => {
    expect(DEFAULT_BASE_URL).toBe("https://app.ratel.sh/api/v1");
  });
});

describe("platformSwitchOn", () => {
  it.each([
    ["events", "RATEL_PLATFORM_EVENTS", "RATEL_CLOUD_EVENTS"],
    ["intentGraph", "RATEL_PLATFORM_INTENT_GRAPH", "RATEL_CLOUD_INTENT_GRAPH"],
  ] as const)("reads %s from %s, then the deprecated %s", (feature, current, deprecated) => {
    expect(platformSwitchOn(feature, {})).toBe(true);
    expect(platformSwitchOn(feature, { [current]: "off" })).toBe(false);
    expect(platformSwitchOn(feature, { [current]: " OFF " })).toBe(false);
    expect(platformSwitchOn(feature, { [deprecated]: "off" })).toBe(false);
    expect(platformSwitchOn(feature, { [current]: "on", [deprecated]: "off" })).toBe(true);
    expect(platformSwitchOn(feature, { [current]: "off", [deprecated]: "on" })).toBe(false);
    expect(platformSwitchOn(feature, { [current]: "", [deprecated]: "off" })).toBe(false);
  });
});
