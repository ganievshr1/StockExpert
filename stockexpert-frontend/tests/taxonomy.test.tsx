// @vitest-environment jsdom
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TaxonomyPage } from "../src/pages/TaxonomyPage";
import type { SchemaDraft } from "../src/types";
const mocks = vi.hoisted(() => ({
  workspace: {} as any,
  save: vi.fn(),
  mapping: vi.fn(),
  approve: vi.fn(),
  passports: vi.fn(),
  normalize: vi.fn(),
  rebuild: vi.fn(),
}));
vi.mock("../src/state/Workspace", () => ({
  useWorkspace: () => mocks.workspace,
}));
vi.mock("../src/services/api", () => ({
  api: {
    saveSchema: mocks.save,
    approveMapping: mocks.mapping,
    approve: mocks.approve,
    passports: mocks.passports,
    normalize: mocks.normalize,
    rebuildTaxonomy: mocks.rebuild,
  },
}));
const pending: SchemaDraft = {
  taxonomy_mode: "passports",
  taxonomy_status: "pending",
  mapping_approved: false,
  analyzed_items: 0,
  mappings: [
    {
      document_id: "doc",
      sheet: "Лист1",
      header_row: 1,
      name_columns: ["0"],
      category_column: null,
    },
  ],
  categories: [],
  features: [],
};
const built: SchemaDraft = {
  ...pending,
  mapping_approved: true,
  taxonomy_status: "review",
  analyzed_items: 20,
  categories: [
    {
      id: "pump",
      name: "Насос",
      parent_id: null,
      feature_keys: ["name", "voltage"],
      aliases: ["Насос", "Помпа"],
      item_count: 15,
      requirement_count: 1,
    },
    {
      id: "bolt",
      name: "Болт",
      parent_id: null,
      feature_keys: ["name", "diameter"],
      item_count: 4,
      requirement_count: 0,
    },
  ],
  features: [
    {
      key: "name",
      label: "Наименование",
      kind: "string",
      unit: "none",
      comparison: "informational",
      tolerance: 0,
    },
    {
      key: "voltage",
      label: "Напряжение",
      kind: "number",
      unit: "V",
      comparison: "informational",
      tolerance: 0,
    },
    {
      key: "diameter",
      label: "Диаметр",
      kind: "number",
      unit: "mm",
      comparison: "informational",
      tolerance: 0,
    },
  ],
};
function setSchema(schema: SchemaDraft) {
  mocks.workspace.data = {
    session: {
      schema_doc: structuredClone(schema),
      approved: false,
      status: "TAXONOMY_REVIEW",
      taxonomy_version: null,
    },
    documents: [{ id: "doc", filename: "Склад.xlsx" }],
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.passports.mockResolvedValue({ total: 0, items: [] });
  mocks.save.mockImplementation(async (_sid, schema) =>
    structuredClone(schema),
  );
  mocks.mapping.mockResolvedValue({});
  mocks.approve.mockResolvedValue({});
  mocks.workspace = {
    sid: "demo",
    active: false,
    busy: false,
    canEdit: true,
    canExpert: true,
    setTab: vi.fn(),
    act: async (fn: any) => {
      await fn();
      return true;
    },
  };
  setSchema(pending);
});
afterEach(cleanup);
describe("passport taxonomy workflow", () => {
  it("approves column mapping separately from taxonomy", async () => {
    render(<TaxonomyPage />);
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Утвердить столбцы",
        exact: true,
      }),
    );
    await waitFor(() => expect(mocks.mapping).toHaveBeenCalledWith("demo"));
    expect(mocks.approve).not.toHaveBeenCalled();
    expect(mocks.save.mock.calls[0][1].categories).toEqual([]);
  });
  it("receives a completed taxonomy after asynchronous normalization", async () => {
    setSchema({ ...pending, mapping_approved: true });
    const view = render(<TaxonomyPage />);
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Нормализовать все позиции и построить категории",
        exact: true,
      }),
    );
    expect(mocks.normalize).toHaveBeenCalledWith("demo");
    setSchema(built);
    view.rerender(<TaxonomyPage />);
    expect(await screen.findByRole("button", { name: /Насос/ })).toBeTruthy();
    expect(
      screen.queryByRole("button", {
        name: "Нормализовать все позиции и построить категории",
        exact: true,
      }),
    ).toBeNull();
  });
  it("shows only the selected material properties and saves expert rules", async () => {
    setSchema(built);
    render(<TaxonomyPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Насос/ }));
    expect(await screen.findByDisplayValue("Напряжение")).toBeTruthy();
    expect(screen.queryByDisplayValue("Диаметр")).toBeNull();
    await userEvent.selectOptions(
      screen.getAllByLabelText("Сравнение")[1],
      "eq",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Утвердить таксономию", exact: true }),
    );
    await waitFor(() =>
      expect(mocks.approve).toHaveBeenCalledWith("demo", "v1"),
    );
    expect(
      mocks.save.mock.calls[0][1].features.find((f: any) => f.key === "voltage")
        .comparison,
    ).toBe("eq");
    expect(
      mocks.save.mock.calls[0][1].features.find(
        (f: any) => f.key === "diameter",
      ).comparison,
    ).toBe("informational");
  });
  it("keeps unsaved category edits when polling refreshes the session", async () => {
    setSchema(built);
    const view = render(<TaxonomyPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Насос/ }));
    const input = screen.getByLabelText("Название категории");
    await userEvent.clear(input);
    await userEvent.type(input, "Насосы");
    setSchema(built);
    view.rerender(<TaxonomyPage />);
    expect(
      (screen.getByLabelText("Название категории") as HTMLInputElement).value,
    ).toBe("Насосы");
  });
  it("locks editing after taxonomy approval", async () => {
    setSchema(built);
    mocks.workspace.data.session.approved = true;
    render(<TaxonomyPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Болт/ }));
    expect(
      (screen.getByLabelText("Название категории") as HTMLInputElement)
        .disabled,
    ).toBe(true);
    expect(
      (
        screen.getByRole("button", {
          name: "Утвердить таксономию",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });
});
