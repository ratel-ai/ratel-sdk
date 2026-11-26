import { describe, expect, it } from "vitest";
import * as platform from "../index.js";
import {
  attach,
  attachIntentGraphSync,
  CatalogSnapshotsPublisher,
  hashCatalogSnapshot,
  ratelPlatform,
} from "./index.js";

describe("the runtime module", () => {
  it("exports catalog snapshot publication and hashing", () => {
    expect(CatalogSnapshotsPublisher).toBeTypeOf("function");
    expect(hashCatalogSnapshot).toBeTypeOf("function");
  });

  it("exports one-line runtime attachment", () => {
    expect(attach).toBeTypeOf("function");
  });

  it("exports intent graph sync", () => {
    expect(attachIntentGraphSync).toBeTypeOf("function");
  });

  it("exports the Tool Picker client", () => {
    expect(ratelPlatform).toBeTypeOf("function");
  });

  it("is re-exported from the ./platform entry point", () => {
    expect(platform.attach).toBe(attach);
    expect(platform.attachIntentGraphSync).toBe(attachIntentGraphSync);
    expect(platform.ratelPlatform).toBe(ratelPlatform);
    expect(platform.CatalogSnapshotsPublisher).toBe(CatalogSnapshotsPublisher);
  });
});
