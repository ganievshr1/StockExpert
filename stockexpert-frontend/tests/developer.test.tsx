// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DeveloperPage } from "../src/pages/DeveloperPage";

const mocks = vi.hoisted(() => ({
  workspace: {} as any,
  references: vi.fn(),
  sessions: vi.fn(),
  results: vi.fn(),
  upload: vi.fn(),
  evaluate: vi.fn(),
  detail: vi.fn(),
}));
vi.mock("../src/state/Workspace", () => ({
  useWorkspace: () => mocks.workspace,
}));
vi.mock("../src/services/developer", () => ({
  developerApi: mocks,
  saveDownload: vi.fn(),
}));
vi.mock("../src/services/regexExperiments", () => ({
  regexApi: { list: vi.fn().mockResolvedValue({ total: 0, items: [] }) },
}));
vi.mock("../src/components/HypothesisComparison", () => ({HypothesisComparison:({source}:any)=><div>История: {source}</div>}));
beforeEach(() => {
  vi.clearAllMocks();
  mocks.workspace = {
    user: { role: "admin" },
    busy: false,
    act: async (fn: any) => {
      await fn();
      return true;
    },
    selectSession: vi.fn(),
    setTab: vi.fn(),
  };
  mocks.references.mockResolvedValue([
    {
      id: "reference",
      title: "Эталон 98",
      version: "1",
      passport_count: 99,
      pair_count: 98,
      notes: [],
    },
  ]);
  mocks.sessions.mockResolvedValue({
    total: 2,
    items: [
      {
        id: "s1",
        title: "Первая",
        status: "MATCHED",
        llm_config: { model: "Qwen Q4" },
      },
      {
        id: "s2",
        title: "Вторая",
        status: "RUNNING",
        llm_config: { model: "Qwen Q5" },
      },
    ],
  });
  mocks.results.mockResolvedValue([]);
  mocks.evaluate.mockResolvedValue({});
  mocks.upload.mockResolvedValue({ id: "reference" });
});
afterEach(cleanup);
describe("developer expert comparison", () => {
  it("separates manual and automatic tabs and their histories", async () => {
    render(<DeveloperPage />);
    await screen.findByLabelText("Файл экспертных данных");
    await userEvent.click(screen.getByRole('tab',{name:'Ручная проверка метрик'}));
    expect(screen.queryByLabelText('Файл экспертных данных')).toBeNull();
    expect(screen.getByText('История: manual')).toBeTruthy();
    await userEvent.click(screen.getByRole('tab',{name:'Автоматическая проверка метрик'}));
    expect(await screen.findByLabelText('Файл экспертных данных')).toBeTruthy();
    expect(screen.getByText('История: automatic')).toBeTruthy();
  });
  it("is inaccessible for a buyer and makes no reference requests", async () => {
    mocks.workspace.user.role = "buyer";
    render(<DeveloperPage />);
    expect(
      screen.getByText("Раздел доступен только администратору"),
    ).toBeTruthy();
    expect(mocks.references).not.toHaveBeenCalled();
  });
  it("uploads once, selects saved sessions and evaluates exactly the selected IDs", async () => {
    render(<DeveloperPage />);
    await screen.findByText("Первая");
    await userEvent.upload(
      screen.getByLabelText("Файл экспертных данных"),
      new File(["{}"], "expert.json", { type: "application/json" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Загрузить эталон" }),
    );
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledTimes(1));
    await userEvent.click(
      screen.getByRole("button", { name: "Выбрать эту страницу" }),
    );
    await userEvent.click(screen.getByLabelText("Выбрать Вторая"));
    await userEvent.click(
      screen.getByRole("button", {
        name: "Рассчитать метрики выбранных сессий",
      }),
    );
    await waitFor(() =>
      expect(mocks.evaluate).toHaveBeenCalledWith("reference", ["s1"]),
    );
  });
  it("distinguishes unavailable metrics from zero and opens session manual review", async () => {
    mocks.results.mockResolvedValue([
      {
        id: "eval",
        session_id: "s1",
        session_title: "Старая сессия",
        status: "PARTIAL",
        result: {
          models: [{ model: "Saved-model", passports: 98 }],
          hypotheses: [
            {
              id: "H1",
              label: "F1",
              target: 0.94,
              value: 0,
              formula: "2 × TP / (2 × TP + FP + FN)",
              numerator: 0,
              denominator: 4,
              substitution: "2 × 0 / (2 × 0 + 1 + 3)",
              scope: "98 строк",
              reason: "",
            },
            {
              id: "H2",
              label: "Accuracy L3",
              value: null,
              target: 0.85,
              formula: "M / N",
              denominator: 98,
              scope: "98 строк",
              reason: "В результатах нет L3.",
            },
          ],
          metrics: { h1_f1: 0, h2_l3_accuracy: null },
          coverage: { expected_stock: 98, normalized_stock: 98 },
          notes: [],
        },
      },
    ]);
    render(<DeveloperPage />);
    await screen.findByText("Старая сессия");
    expect(screen.getByText("Saved-model (98)")).toBeTruthy();
    expect(screen.getByText("0%")).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: "Ручная проверка" }),
    );
    expect(mocks.workspace.selectSession).toHaveBeenCalledWith("s1");
    expect(mocks.workspace.setTab).toHaveBeenCalledWith("quality");
  });
});
