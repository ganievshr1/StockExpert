// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../src/services/api";

afterEach(() => vi.unstubAllGlobals());
it("encodes explicit requirement parsing modes and preserves the legacy multipart contract", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => ({ id: "doc" }) });
  vi.stubGlobal("fetch", fetch);
  const file = new File(["Труба DN 20\nТруба DN 40"], "positions.txt");
  await api.upload("s", "requirement", file, "lines");
  await api.replaceDocument("s", "d", file, "auto");
  await api.upload("s", "requirement", file);
  await api.upload("s", "residuals", file);
  expect(
    (fetch.mock.calls[0][1].body as FormData).get("requirement_mode"),
  ).toBe("lines");
  expect(
    (fetch.mock.calls[1][1].body as FormData).get("requirement_mode"),
  ).toBe("auto");
  expect(
    (fetch.mock.calls[2][1].body as FormData).has("requirement_mode"),
  ).toBe(false);
  expect(
    (fetch.mock.calls[3][1].body as FormData).has("requirement_mode"),
  ).toBe(false);
  expect((fetch.mock.calls[0][1].body as FormData).get("file")).toBeInstanceOf(
    File,
  );
});
