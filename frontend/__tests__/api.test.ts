import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, setFetch } from "@/services/api";

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("api client", () => {
  afterEach(() => setFetch((...args) => fetch(...args)));

  it("uses same-origin /api paths (no secrets or backend URL in the browser)", async () => {
    const f = vi.fn().mockResolvedValue(respond(200, { id: "j1" }));
    setFetch(f);
    await api.getJob("j1");
    expect(f).toHaveBeenCalledWith("/api/jobs/j1", undefined);
    expect(api.downloadUrl("j1")).toBe("/api/jobs/j1/download");
  });

  it("sends the chosen platform when starting", async () => {
    const f = vi.fn().mockResolvedValue(respond(200, {}));
    setFetch(f);
    await api.start("j1", "twitch");
    const [, init] = f.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ source_platform: "twitch" });
  });

  it("builds row queries", async () => {
    const f = vi.fn().mockResolvedValue(respond(200, { items: [] }));
    setFetch(f);
    await api.rows("j1", { status: "ERROR", offset: 100, limit: 50 });
    expect(f.mock.calls[0][0]).toBe("/api/jobs/j1/rows?status=ERROR&offset=100&limit=50");
  });

  it("surfaces backend error details", async () => {
    setFetch(vi.fn().mockResolvedValue(respond(422, { detail: "Required headers not found." })));
    await expect(api.upload(new File(["x"], "a.xlsx"))).rejects.toEqual(
      expect.objectContaining({ status: 422, message: "Required headers not found." }),
    );
    await expect(api.getJob("x")).rejects.toBeInstanceOf(ApiError);
  });
});
