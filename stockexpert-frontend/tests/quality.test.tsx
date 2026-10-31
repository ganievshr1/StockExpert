// @vitest-environment jsdom
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QualityPage } from "../src/pages/QualityPage";
const mocks = vi.hoisted(() => ({
  workspace: {} as any,
  summary: vi.fn(),
  targets: vi.fn(),
  save: vi.fn(),
  finalize: vi.fn(),
  remove: vi.fn(),
}));
vi.mock("../src/state/Workspace", () => ({
  useWorkspace: () => mocks.workspace,
}));
vi.mock("../src/services/quality", () => ({ qualityApi: mocks }));
const item = {
  id: "stock",
  kind: "residual",
  source_text: "Кабель 3300 V",
  sheet: "Склад",
  row_index: 2,
  passport: {
    name: "Кабель",
    category_id: "cable",
    attributes: { voltage: 3300 },
    evidence: {},
  },
};
beforeEach(() => {
  vi.clearAllMocks();
  mocks.save.mockResolvedValue({});
  mocks.finalize.mockResolvedValue({});
  mocks.summary.mockResolvedValue({
    job_id: "job",
    finalized: false,
    coverage: {
      passports_reviewed: 1,
      passports_total: 2,
      pairs_reviewed: 0,
      pairs_total: 1,
    },
    fields: [],
  });
  mocks.targets.mockImplementation(async (_sid, kind) => ({
    job_id: "job",
    total: 1,
    items: [
      {
        target:
          kind === "passport"
            ? item
            : {
                id: "match",
                residual: item,
                requirement: item,
                result: { rank: 1, is_shortlisted: true },
              },
        review: null,
      },
    ],
  }));
  mocks.workspace = {
    sid: "session",
    busy: false,
    active: false,
    canExpert: true,
    setTab: vi.fn(),
    act: async (fn: any) => {
      await fn();
      return true;
    },
    data: {
      job: { id: "job", status: "COMPLETED" },
      session: {
        schema_doc: {
          features: [
            {
              key: "voltage",
              label: "Напряжение",
              kind: "number",
              unit: "V",
              comparison: "eq",
            },
          ],
          categories: [
            {
              id: "cable",
              name: "Кабель",
              parent_id: null,
              feature_keys: ["voltage"],
            },
          ],
        },
      },
    },
  };
});
afterEach(cleanup);
describe("explicit expert assessment", () => {
  it("does not mark model predictions as reviewed without expert action", async () => {
    render(<QualityPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Сохранить и далее" }),
    );
    expect(
      await screen.findByText("Отметьте проверенные параметры или категорию"),
    ).toBeTruthy();
    expect(mocks.save).not.toHaveBeenCalled();
    await userEvent.selectOptions(screen.getByLabelText("Режим проверки паспорта"), "parameters");
    await userEvent.click(screen.getByLabelText("Проверен Напряжение"));
    await userEvent.click(
      screen.getByRole("button", { name: "Сохранить и далее" }),
    );
    await waitFor(() =>
      expect(mocks.save).toHaveBeenCalledWith(
        "session",
        "job",
        "passport",
        "stock",
        {
          attributes: { voltage: 3300 },
          category_reviewed: false,
          category_id: null,
        },
      ),
    );
  });
  it("stores explicit absence without a category in parameter mode", async () => {
    render(<QualityPage />);
    await userEvent.selectOptions(await screen.findByLabelText("Режим проверки паспорта"), "parameters");
    await userEvent.click(
      await screen.findByRole("button", { name: "Нет данных" }),
    );
    expect(screen.queryByLabelText("Категория проверена")).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Сохранить и далее" }),
    );
    await waitFor(() =>
      expect(mocks.save).toHaveBeenCalledWith(
        "session",
        "job",
        "passport",
        "stock",
        {
          attributes: { voltage: null },
          category_reviewed: false,
          category_id: null,
        },
      ),
    );
  });
  it("defaults to one text per stock row and hides H2 and technical attributes", async () => {
    render(<QualityPage />);
    const field=await screen.findByLabelText("Правильное значение Текст наименования");
    expect(screen.queryByLabelText("Категория проверена")).toBeNull();
    expect(screen.queryByLabelText("Проверен Напряжение")).toBeNull();
    expect(mocks.targets.mock.calls.at(-1)?.[4]).toMatchObject({passport_kind:"residual",review_mode:"text"});
    await userEvent.clear(field);
    await userEvent.type(field,"Кабель 3300 V");
    await userEvent.click(screen.getByRole("button",{name:"Сохранить и далее"}));
    await waitFor(()=>expect(mocks.save).toHaveBeenCalledWith("session","job","passport","stock",{attributes:{name:"Кабель 3300 V"},category_reviewed:false,category_id:null}));
  });
  it("checks H2 in its own mode without a text field", async () => {
    render(<QualityPage />);
    await userEvent.selectOptions(await screen.findByLabelText("Режим проверки паспорта"), "category");
    expect(screen.queryByLabelText("Правильное значение Текст наименования")).toBeNull();
    await userEvent.click(screen.getByLabelText("Категория проверена"));
    await userEvent.click(screen.getByRole("button",{name:"Сохранить и далее"}));
    await waitFor(()=>expect(mocks.save).toHaveBeenCalledWith("session","job","passport","stock",{attributes:{},category_reviewed:true,category_id:"cable"}));
  });
  it("skips unknown pairs and saves unsuitable pairs as false", async () => {
    render(<QualityPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Подходит / не подходит" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Далее без оценки" }),
    );
    expect(mocks.save).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Не подходит", exact: true }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Сохранить и далее" }),
    );
    await waitFor(() =>
      expect(mocks.save).toHaveBeenCalledWith(
        "session",
        "job",
        "candidate",
        "match",
        { suitable: false, comment: "" },
      ),
    );
  });
  it("finalizes the current completed job", async () => {
    render(<QualityPage />);
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Завершить экспертную проверку",
      }),
    );
    await waitFor(() =>
      expect(mocks.finalize).toHaveBeenCalledWith("session", "job"),
    );
  });
});
