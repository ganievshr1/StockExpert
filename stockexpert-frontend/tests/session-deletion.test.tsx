// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkspaceProvider, useWorkspace } from "../src/state/Workspace";
import { DashboardPage } from "../src/pages/DashboardPage";
import { api, ApiError, setToken } from "../src/services/api";
import { workspaceApi } from "../src/services/workspace";

let rows: any[];
function Harness() {
  const w = useWorkspace();
  if (w.initializing) return null;
  return (
    <>
      <output aria-label="Выбранная сессия">{w.sid || "none"}</output>
      <output aria-label="Открытые данные">
        {w.data?.session.id || "none"}
      </output>
      <output aria-label="Раздел">{w.tab}</output>
      <output aria-label="Глобальная ошибка">{w.error}</output>
      <button onClick={() => w.setTab("dashboard")}>К списку</button>
      <DashboardPage onCreate={() => {}} />
    </>
  );
}
const mount = () =>
  render(
    <WorkspaceProvider>
      <Harness />
    </WorkspaceProvider>,
  );
const dialog = () => within(screen.getByRole("dialog"));

beforeEach(() => {
  sessionStorage.clear();
  setToken("test-token");
  rows = Array.from({ length: 11 }, (_, i) => ({
    id: `s${i}`,
    title: `Сессия ${i}`,
    status: "CREATED",
    created_at: "2026-10-02",
    schema_doc: {},
  }));
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  vi.spyOn(api, "me").mockResolvedValue({ id: 1, role: "admin" } as any);
  vi.spyOn(api, "config").mockResolvedValue({} as any);
  vi.spyOn(api, "sessions").mockImplementation(async () => [...rows]);
  vi.spyOn(api, "session").mockImplementation(async (id) =>
    rows.find((s) => s.id === id),
  );
  vi.spyOn(api, "documents").mockResolvedValue([]);
  vi.spyOn(api, "metrics").mockResolvedValue({} as any);
  vi.spyOn(api, "status").mockResolvedValue({ status: "NOT_STARTED" } as any);
  vi.spyOn(api, "deleteSession").mockImplementation(async (id) => {
    rows = rows.filter((s) => s.id !== id);
    return { deleted: true, session_id: id };
  });
  vi.spyOn(api, "deleteAllSessions").mockImplementation(async () => {
    const ids = rows.map((s) => s.id);
    rows = [];
    return { deleted_session_ids: ids, failed: [] };
  });
  vi.spyOn(workspaceApi, "overview").mockImplementation(
    async () =>
      ({ total_sessions: rows.length, generated_at: "2026-10-02" }) as any,
  );
  vi.spyOn(workspaceApi, "sessions").mockImplementation(async (params) => {
    const matching = rows.filter((s) =>
      s.title.includes(params.get("q") || ""),
    );
    const offset = Number(params.get("offset"));
    return {
      total: matching.length,
      items: matching.slice(offset, offset + 10),
    };
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setToken("");
});

describe("session deletion", () => {
  it("requires confirmation and cancellation does not send DELETE", async () => {
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "Удалить сессию «Сессия 0»" }),
    );
    expect(dialog().getByText(/Будет удалена сессия «Сессия 0»/)).toBeTruthy();
    expect(api.deleteSession).not.toHaveBeenCalled();
    await userEvent.click(dialog().getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.deleteSession).not.toHaveBeenCalled();
  });

  it("returns to the previous page when the last row is deleted", async () => {
    mount();
    await screen.findByText("Сессия 0");
    await userEvent.click(screen.getByRole("button", { name: "Далее" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Удалить сессию «Сессия 10»" }),
    );
    await userEvent.click(
      dialog().getByRole("button", { name: "Удалить сессию", exact: true }),
    );
    await screen.findByText("Сессия 0");
    expect(screen.queryByText("Сессия 10")).toBeNull();
    expect(screen.getByText("1 / 1")).toBeTruthy();
  });

  it("clears the selected session and does not reload deleted details", async () => {
    sessionStorage.setItem("stockexpert.sid", "s0");
    mount();
    await waitFor(() =>
      expect(screen.getByLabelText("Открытые данные").textContent).toBe("s0"),
    );
    vi.mocked(api.session).mockClear();
    await userEvent.click(
      screen.getByRole("button", { name: "Удалить сессию «Сессия 0»" }),
    );
    await userEvent.click(
      dialog().getByRole("button", { name: "Удалить сессию", exact: true }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Выбранная сессия").textContent).toBe(
        "none",
      ),
    );
    expect(screen.getByLabelText("Открытые данные").textContent).toBe("none");
    expect(sessionStorage.getItem("stockexpert.sid")).toBeNull();
    expect(api.session).not.toHaveBeenCalled();
  });

  it("requires typed confirmation and bulk delete covers hidden pages and filters", async () => {
    mount();
    await screen.findByText("Сессия 0");
    await userEvent.type(
      screen.getByLabelText("Поиск по названию сессии"),
      "Сессия 0",
    );
    await waitFor(() => expect(screen.queryByText("Сессия 1")).toBeNull());
    await userEvent.click(
      screen.getByRole("button", { name: "Удалить все сессии" }),
    );
    expect(
      dialog()
        .getByRole("button", { name: "Удалить все сессии" })
        .hasAttribute("disabled"),
    ).toBe(true);
    await userEvent.type(
      dialog().getByLabelText("Подтверждение удаления всех сессий"),
      "УДАЛИТЬ",
    );
    await userEvent.click(
      dialog().getByRole("button", { name: "Удалить все сессии" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(api.deleteAllSessions).toHaveBeenCalledOnce();
    expect(rows).toHaveLength(0);
    await screen.findByText("Сессий по выбранным условиям нет.");
  });

  it("shows active-job errors in the dialog and leaves rows intact", async () => {
    vi.mocked(api.deleteSession).mockRejectedValue(
      new ApiError(409, "Остановите обработку"),
    );
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "Удалить сессию «Сессия 0»" }),
    );
    await userEvent.click(
      dialog().getByRole("button", { name: "Удалить сессию", exact: true }),
    );
    expect(await dialog().findByRole("alert")).toBeTruthy();
    expect(screen.getByText("Сессия 0")).toBeTruthy();
    expect(rows).toHaveLength(11);
  });

  it("keeps partial bulk failures visible after deleting the selected session", async () => {
    sessionStorage.setItem("stockexpert.sid", "s0");
    vi.mocked(api.deleteAllSessions).mockImplementation(async () => {
      rows = rows.filter((s) => s.id === "s1");
      return {
        deleted_session_ids: [
          "s0",
          ...Array.from({ length: 9 }, (_, i) => `s${i + 2}`),
        ],
        failed: [
          {
            session_id: "s1",
            title: "Сессия 1",
            status_code: 503,
            detail: "Повторите удаление",
          },
        ],
      };
    });
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "Удалить все сессии" }),
    );
    await userEvent.type(
      dialog().getByLabelText("Подтверждение удаления всех сессий"),
      "УДАЛИТЬ",
    );
    await userEvent.click(
      dialog().getByRole("button", { name: "Удалить все сессии" }),
    );
    expect(
      await dialog().findByText("Не удалось удалить сессий: 1"),
    ).toBeTruthy();
    expect(screen.getByLabelText("Выбранная сессия").textContent).toBe("none");
    expect(screen.getByText("Сессия 1")).toBeTruthy();
    expect(screen.queryByText("Сессия 0")).toBeNull();
  });

  it("does not offer deletion to a read-only user", async () => {
    vi.mocked(api.me).mockResolvedValue({ id: 1, role: "viewer" } as any);
    mount();
    await screen.findByText("Сессия 0");
    expect(screen.queryByRole("button", { name: /Удалить/ })).toBeNull();
  });

  it("opening the already selected session retains loaded data", async () => {
    sessionStorage.setItem("stockexpert.sid", "s0");
    mount();
    await waitFor(() =>
      expect(screen.getByLabelText("Открытые данные").textContent).toBe("s0"),
    );
    await userEvent.click(
      within(screen.getByText("Сессия 0").closest("tr")!).getByRole("button", {
        name: "Открыть →",
      }),
    );
    expect(screen.getByLabelText("Открытые данные").textContent).toBe("s0");
  });
});
