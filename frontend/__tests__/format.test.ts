import { describe, expect, it } from "vitest";

import {
  displayDecision,
  formatConfidence,
  formatScore,
  isActive,
  otherPlatform,
  percent,
  platformLabel,
} from "@/lib/format";

describe("format helpers", () => {
  it("computes bounded percentages", () => {
    expect(percent(380, 500)).toBe(76);
    expect(percent(0, 0)).toBe(0);
    expect(percent(600, 500)).toBe(100);
  });

  it("formats confidence and scores", () => {
    expect(formatConfidence(96.4)).toBe("96%");
    expect(formatConfidence(null)).toBe("–");
    expect(formatScore(0.82)).toBe("82%");
    expect(formatScore(true)).toBe("MATCH");
    expect(formatScore(["instagram:x"])).toBe("instagram:x");
    expect(formatScore(null)).toBe("n/a");
  });

  it("labels platforms", () => {
    expect(platformLabel("kick")).toBe("Kick");
    expect(otherPlatform("kick")).toBe("twitch");
    expect(otherPlatform(null)).toBeNull();
  });

  it("maps internal row states to display decisions", () => {
    expect(displayDecision({ status: "MATCH", decision: "MATCH", manual_verdict: null })).toBe("MATCH");
    expect(displayDecision({ status: "REVIEW", decision: "REVIEW", manual_verdict: null })).toBe("REVIEW");
    expect(displayDecision({ status: "NO_MATCH", decision: "REVIEW", manual_verdict: "REJECTED" })).toBe("NO_MATCH");
    expect(displayDecision({ status: "SOURCE_NOT_FOUND", decision: "NO_MATCH", manual_verdict: null })).toBe(
      "NOT_FOUND",
    );
    expect(displayDecision({ status: "SOURCE_NOT_FOUND", decision: "REVIEW", manual_verdict: null })).toBe("REVIEW");
    expect(displayDecision({ status: "TEMPORARY_ERROR", decision: null, manual_verdict: null })).toBe("ERROR");
    expect(displayDecision({ status: "RATE_LIMITED", decision: null, manual_verdict: null })).toBe("ERROR");
    expect(displayDecision({ status: "PENDING", decision: null, manual_verdict: null })).toBe("PENDING");
    expect(displayDecision({ status: "PRESERVED", decision: "MATCH", manual_verdict: null })).toBe("SKIPPED");
  });

  it("knows which jobs are active", () => {
    expect(isActive({ status: "PROCESSING" })).toBe(true);
    expect(isActive({ status: "COMPLETED" })).toBe(false);
  });
});
