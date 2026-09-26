import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("U-3: the client never invents a capability", () => {
  it("does not treat capabilities as authorisation", () => {
    const source = readFileSync(resolve("src/api.ts"), "utf8");
    expect(source).not.toMatch(/if \(.*capabilities/);
  });
});
