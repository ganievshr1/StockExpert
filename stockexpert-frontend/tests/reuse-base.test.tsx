// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sidebar } from "../src/components/Sidebar";
import { DataPage } from "../src/pages/DataPage";
import { NewSessionDialog } from "../src/features/session/NewSessionDialog";

const mocks = vi.hoisted(() => ({
  workspace: {} as any,
  bases: vi.fn(),
  run: vi.fn(),
  prepare: vi.fn(),
  normalize: vi.fn(),
  rebuildTaxonomy: vi.fn(),
  selectTaxonomy: vi.fn(),
}));
vi.mock("../src/state/Workspace", () => ({
  useWorkspace: () => mocks.workspace,
}));
vi.mock("../src/services/api", () => ({ api: mocks }));
vi.mock("../src/components/Icon", () => ({
  Icon: () => null,
  Brand: () => null,
  ThemeSwitch: () => null,
}));
vi.mock("../src/features/session/DocumentCard", () => ({
  DocumentCard: ({ kind }: any) => <div>{`upload:${kind}`}</div>,
}));
vi.mock("../src/features/session/MappingEditor", () => ({
  MappingEditor: () => <div>Редактор столбцов</div>,
}));
vi.mock("../src/features/session/SessionControls", () => ({
  SessionControls: () => null,
}));
vi.mock("../src/components/ModelPicker", () => ({
  ModelPicker: ({
    value,
    onChange,
    label = "Модель для обработки сессии",
  }: any) => (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="deepseek">DeepSeek</option>
      <option value="qwen3-8b">Qwen3</option>
      <option value="qwen25-7b-q4">Qwen 7B Q4</option>
      <option value="qwen25-7b-q5">Qwen 7B Q5</option>
      <option value="qwen25-14b">Qwen 14B</option>
    </select>
  ),
  SessionModelPanel: () => null,
}));
beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  mocks.workspace = {
    user: { role: "admin", username: "admin" },
    tab: "developer",
    sid: "reuse",
    busy: false,
    active: false,
    canEdit: true,
    setTab: vi.fn(),
    create: vi.fn().mockResolvedValue(undefined),
    logout: vi.fn(),
    act: async (fn: () => Promise<unknown>) => {
      await fn();
      return true;
    },
    data: {
      session: {
        approved: true,
        status: "BASE_READY",
        llm_config: { label: "Qwen3", provider: "ollama" },
        base_snapshot: {
          source_session_id: "original",
          source_title: "Готовый склад",
          normalized_rows: 98,
          reused_passports: true,
        },
        schema_doc: {
          taxonomy_mode: "passports",
          mapping_approved: true,
          mappings: [],
          taxonomy_status: "approved",
        },
      },
      documents: [{ kind: "residual" }, { kind: "requirement" }],
    },
  };
  mocks.bases.mockResolvedValue([
    { session_id: "original", title: "Готовый склад", normalized_rows: 98 },
  ]);
  mocks.run.mockResolvedValue({ task_id: "job" });
});
afterEach(cleanup);

it('lets the user choose no H2 directly on the data page before processing',async()=>{
 render(<DataPage/>);
 await userEvent.click(screen.getByRole('radio',{name:'Без H2 — сразу к поиску'}));
 expect(mocks.selectTaxonomy).toHaveBeenCalledWith('reuse',false);
});

it('locks H2 mode after processing and explains how to choose another mode',async()=>{
 mocks.workspace.data.session.normalization_locked=true;
 render(<DataPage/>);
 await userEvent.click(screen.getByRole('radio',{name:'Без H2 — сразу к поиску'}));
 expect(mocks.selectTaxonomy).not.toHaveBeenCalled();
 expect(screen.getByText(/Режим зафиксирован после начала обработки/)).toBeTruthy();
});

it('creates a new session without H2 when selected',async()=>{
 render(<NewSessionDialog onClose={()=>{}}/>);
 await userEvent.type(screen.getByRole('textbox',{name:'Название сессии'}),'Без категорий');
 await userEvent.click(screen.getByRole('radio',{name:'Без H2 — сразу к поиску'}));
 await userEvent.click(screen.getByRole('button',{name:'Создать и загрузить файлы'}));
 expect(mocks.workspace.create).toHaveBeenCalledWith('Без категорий',undefined,'deepseek','regex',false);
});

describe("ready base and developer navigation", () => {
  it("shows the developer section directly in main navigation", async () => {
    render(<Sidebar organization="МТО" />);
    const link = screen.getByRole("button", { name: "Для разработчика" });
    expect(link.closest("nav")?.getAttribute("aria-label")).toBe(
      "Основная навигация",
    );
    expect(link.closest("details")).toBeNull();
    expect(link.getAttribute("aria-current")).toBe("page");
    await userEvent.click(link);
    expect(mocks.workspace.setTab).toHaveBeenCalledWith("developer");
  });
  it("keeps existing role restrictions", () => {
    mocks.workspace.user.role = "buyer";
    render(<Sidebar organization="МТО" />);
    expect(
      screen.queryByRole("button", { name: "Для разработчика" }),
    ).toBeNull();
  });
  it("runs matching from the data page without mapping or normalizing the base", async () => {
    render(<DataPage />);
    expect(screen.queryByText("upload:residuals")).toBeNull();
    expect(screen.getByText("upload:requirement")).toBeTruthy();
    expect(screen.queryByText("Редактор столбцов")).toBeNull();
    expect(screen.queryByText(/Запустите нормализацию/)).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Сопоставить с готовой базой" }),
    );
    expect(mocks.run).toHaveBeenCalledWith("reuse", 150);
    expect(mocks.prepare).not.toHaveBeenCalled();
    expect(mocks.normalize).not.toHaveBeenCalled();
    expect(mocks.workspace.setTab).toHaveBeenCalledWith("processing");
  });
  it("allows another model for the new requirement while reusing the chosen base", async () => {
    render(<NewSessionDialog onClose={vi.fn()} />);
    await userEvent.type(
      screen.getByRole("textbox", { name: "Название сессии" }),
      "Новый опросный лист",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Использовать готовую базу" }),
    );
    await screen.findByRole("option", { name: "Готовый склад · 98 позиций" });
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Готовая база" }),
      "original",
    );
    await userEvent.click(screen.getByRole('radio', {name:'С H2 — построить категории'}));
    await userEvent.selectOptions(
      screen.getByRole("combobox", {
        name: "Модель H2 — классификация паспортов",
      }),
      "qwen3-8b",
    );
    expect(screen.getByText(/даже при выборе другой модели/)).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: "Создать и загрузить файлы" }),
    );
    expect(mocks.workspace.create).toHaveBeenCalledWith(
      "Новый опросный лист",
      "original",
      "qwen3-8b",
      "regex",
      true,
    );
  });
  it("defaults a new Excel session to regex in the main engine", async () => {
    render(<NewSessionDialog onClose={vi.fn()} />);
    const method = screen.getByRole("combobox", {
      name: "Метод H1 — извлечение характеристик",
    }) as HTMLSelectElement;
    expect(method.value).toBe("regex");
    await userEvent.type(
      screen.getByRole("textbox", { name: "Название сессии" }),
      "Regex H1",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Создать и загрузить файлы" }),
    );
    expect(mocks.workspace.create).toHaveBeenCalledWith(
      "Regex H1",
      undefined,
      "deepseek",
      "regex",
      false,
    );
  });
  it("retains an explicit LLM option for comparisons", async () => {
    render(<NewSessionDialog onClose={vi.fn()} />);
    await userEvent.selectOptions(
      screen.getByRole("combobox", {
        name: "Метод H1 — извлечение характеристик",
      }),
      "llm",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Название сессии" }),
      "LLM H1",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Создать и загрузить файлы" }),
    );
    expect(mocks.workspace.create).toHaveBeenCalledWith(
      "LLM H1",
      undefined,
      "deepseek",
      "llm",
      false,
    );
  });
  it("starts only H1 from the normal data page", async () => {
    Object.assign(mocks.workspace.data.session, {
      approved: false,
      status: "MAPPING_APPROVED",
      normalization_method: "regex",
      base_snapshot: {},
      schema_doc: {
        taxonomy_mode: "passports",
        mapping_approved: true,
        mappings: [],
        taxonomy_status: "pending",
      },
    });
    render(<DataPage />);
    await userEvent.click(
      screen.getByRole("button", { name: "H1: нормализовать регулярками" }),
    );
    expect(mocks.normalize).toHaveBeenCalledWith("reuse");
    expect(mocks.run).not.toHaveBeenCalled();
    expect(mocks.rebuildTaxonomy).not.toHaveBeenCalled();
  });
  it("continues to H2 without rerunning H1", async () => {
    Object.assign(mocks.workspace.data.session, {
      approved: false,
      status: "H1_READY",
      normalization_method: "regex",
      base_snapshot: {},
      schema_doc: {
        taxonomy_mode: "passports",
        mapping_approved: true,
        mappings: [],
        taxonomy_status: "pending",
      },
    });
    render(<DataPage />);
    await userEvent.click(
      screen.getByRole("button", { name: "H2: построить категории" }),
    );
    expect(mocks.rebuildTaxonomy).toHaveBeenCalledWith("reuse");
    expect(mocks.normalize).not.toHaveBeenCalled();
  });
});


it.each(['qwen25-7b-q4','qwen25-7b-q5','qwen3-8b','qwen25-14b','deepseek'])('keeps H2 disabled when selecting LLM %s',async(profile)=>{
 render(<NewSessionDialog onClose={()=>{}}/>);
 expect((screen.getByRole('radio',{name:'Без H2 — сразу к поиску'}) as HTMLInputElement).checked).toBe(true);
 await userEvent.selectOptions(screen.getByRole('combobox',{name:'Метод H1 — извлечение характеристик'}),'llm');
 await userEvent.selectOptions(screen.getByRole('combobox',{name:'Модель для обработки сессии'}),profile);
 expect((screen.getByRole('radio',{name:'Без H2 — сразу к поиску'}) as HTMLInputElement).checked).toBe(true);
 await userEvent.type(screen.getByRole('textbox',{name:'Название сессии'}),'H1 без H2');
 await userEvent.click(screen.getByRole('button',{name:'Создать и загрузить файлы'}));
 expect(mocks.workspace.create).toHaveBeenCalledWith('H1 без H2',undefined,profile,'llm',false);
});

it('retains an explicit H2 choice across method and model changes',async()=>{
 render(<NewSessionDialog onClose={()=>{}}/>);
 await userEvent.click(screen.getByRole('radio',{name:'С H2 — построить категории'}));
 await userEvent.selectOptions(screen.getByRole('combobox',{name:'Метод H1 — извлечение характеристик'}),'llm');
 await userEvent.selectOptions(screen.getByRole('combobox',{name:'Модель для обработки сессии'}),'qwen25-7b-q5');
 expect((screen.getByRole('radio',{name:'С H2 — построить категории'}) as HTMLInputElement).checked).toBe(true);
 await userEvent.selectOptions(screen.getByRole('combobox',{name:'Метод H1 — извлечение характеристик'}),'regex');
 expect((screen.getByRole('radio',{name:'С H2 — построить категории'}) as HTMLInputElement).checked).toBe(true);
 await userEvent.click(screen.getByRole('radio',{name:'Без H2 — сразу к поиску'}));
 await userEvent.selectOptions(screen.getByRole('combobox',{name:'Метод H1 — извлечение характеристик'}),'llm');
 expect((screen.getByRole('radio',{name:'Без H2 — сразу к поиску'}) as HTMLInputElement).checked).toBe(true);
});

it('offers the explicit H2 step after LLM H1 when H2 was selected',async()=>{
 Object.assign(mocks.workspace.data.session,{approved:false,status:'H1_READY',normalization_method:'llm',taxonomy_enabled:true,base_snapshot:{},schema_doc:{mapping_approved:true,taxonomy_mode:'passports',taxonomy_status:'pending',mappings:[]}});
 render(<DataPage/>);
 await userEvent.click(screen.getByRole('button',{name:'H2: построить категории'}));
 expect(mocks.rebuildTaxonomy).toHaveBeenCalledWith('reuse');
 expect(mocks.normalize).not.toHaveBeenCalled();
});
