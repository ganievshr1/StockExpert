// @vitest-environment jsdom
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RegexExperiments } from "../src/components/RegexExperiments";

const mocks = vi.hoisted(() => ({
  workspace: {} as any,
  list: vi.fn(),
  start: vi.fn(),
  upload: vi.fn(),
  passports: vi.fn(),
  save: vi.fn(),
}));
vi.mock("../src/state/Workspace", () => ({
  useWorkspace: () => mocks.workspace,
}));
vi.mock("../src/services/regexExperiments", () => ({ regexApi: mocks }));
vi.mock("../src/services/developer", () => ({ saveDownload: mocks.save }));
beforeEach(() => {
  vi.clearAllMocks();
  mocks.workspace = {
    busy: false,
    act: async (fn: any) => {
      await fn();
      return true;
    },
  };
  mocks.list.mockResolvedValue({ total: 0, items: [] });
  mocks.start.mockResolvedValue({ id: "run" });
  mocks.upload.mockResolvedValue({ id: "file-run" });
  mocks.passports.mockResolvedValue({ total: 0, items: [] });
});
afterEach(cleanup);
describe("isolated regex experiment", () => {
  it("runs uploaded Excel without a reference or an existing session", async () => {
    render(<RegexExperiments referenceId="" selectedSourceIds={[]} />);
    const file = new File(["xlsx fixture"], "stock.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await userEvent.upload(
      screen.getByLabelText("Складской Excel для регулярок"),
      file,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Обработать Excel регулярками" }),
    );
    await waitFor(() =>
      expect(mocks.upload).toHaveBeenCalledWith(
        file,
        expect.stringMatching(/^[0-9a-f-]{36}$/),
        undefined,
      ),
    );
    expect(mocks.start).not.toHaveBeenCalled();
    expect(mocks.list).toHaveBeenCalledWith("", 0);
  });
  it("uses the selected reference only when the checkbox is enabled", async () => {
    mocks.workspace.act = async (fn: any) => {
      await fn();
      return false;
    };
    render(<RegexExperiments referenceId="expert" selectedSourceIds={[]} />);
    const file = new File(["fixture"], "stock.xlsx");
    await userEvent.upload(
      screen.getByLabelText("Складской Excel для регулярок"),
      file,
    );
    await userEvent.click(
      screen.getByLabelText("Сравнить с выбранным выше эталоном эксперта"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Обработать Excel регулярками" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Обработать Excel регулярками" }),
    );
    expect(mocks.upload.mock.calls[0][2]).toBe("expert");
    expect(mocks.upload.mock.calls[0][1]).toBe(mocks.upload.mock.calls[1][1]);
  });
  it("requires exactly one existing source session", async () => {
    const view = render(
      <RegexExperiments referenceId="reference" selectedSourceIds={[]} />,
    );
    expect(
      (
        screen.getByRole("button", {
          name: "Рассчитать только регулярки",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    view.rerender(
      <RegexExperiments
        referenceId="reference"
        selectedSourceIds={["s1", "s2"]}
      />,
    );
    expect(
      (
        screen.getByRole("button", {
          name: "Рассчитать только регулярки",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(mocks.start).not.toHaveBeenCalled();
  });
  it("creates only a regex run and carries the expert reference and idempotency key", async () => {
    render(
      <RegexExperiments referenceId="reference" selectedSourceIds={["s1"]} />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Рассчитать только регулярки" }),
    );
    await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(1));
    expect(mocks.start).toHaveBeenCalledWith(
      "s1",
      "reference",
      expect.stringMatching(/^[0-9a-f-]{36}$/),
    );
  });
  it("keeps the request key after an uncertain response", async () => {
    mocks.workspace.act = async (fn: any) => {
      await fn();
      return false;
    };
    render(
      <RegexExperiments referenceId="reference" selectedSourceIds={["s1"]} />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Рассчитать только регулярки" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Рассчитать только регулярки" }),
    );
    expect(mocks.start.mock.calls[0][2]).toBe(mocks.start.mock.calls[1][2]);
  });
  it("distinguishes zero F1 from unavailable metrics and opens stored passports", async () => {
    mocks.list.mockResolvedValue({
      total: 1,
      items: [
        {
          id: "run",
          source_title: "Моя база",
          status: "COMPLETED",
          row_count: 99,
          processed_rows: 99,
          rules_version: "regex-mtr-v1.0.0",
          created_at: "2026-10-17T10:00:00Z",
          baseline: {
            metrics: { h1_f1: 0.279 },
            models: [{ model: "Saved Qwen" }],
          },
          result: {
            metrics: { h1_f1: 0, h1_precision: null, full_row_recall: 0.2 },
            timing: { normalization_seconds: 0.123, p95_row_ms: 0.01 },
          },
        },
      ],
    });
    render(
      <RegexExperiments referenceId="reference" selectedSourceIds={["s1"]} />,
    );
    expect(await screen.findByText("Saved Qwen")).toBeTruthy();
    expect(screen.getByText("0%")).toBeTruthy();
    expect(screen.getByText("20%")).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: "Паспорта и ошибки" }),
    );
    await waitFor(() => expect(mocks.passports).toHaveBeenCalledWith("run", 0));
    await userEvent.click(screen.getByRole("button", { name: "CSV" }));
    expect(mocks.save).toHaveBeenCalledWith(
      "/developer/regex-experiments/run/export?format=csv",
      "regex-run.csv",
    );
  });
});
