import { describe, expect, it } from "vitest";
import * as cloud from "../index.js";
import {
  attach,
  attachIntentGraphSync,
  CatalogSnapshotsPublisher,
  hashCatalogSnapshot,
  ratelCloud,
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
    expect(ratelCloud).toBeTypeOf("function");
  });

  it("is re-exported from the ./cloud entry point", () => {
    expect(cloud.attach).toBe(attach);
    expect(cloud.attachIntentGraphSync).toBe(attachIntentGraphSync);
    expect(cloud.ratelCloud).toBe(ratelCloud);
    expect(cloud.CatalogSnapshotsPublisher).toBe(CatalogSnapshotsPublisher);
  });
});
