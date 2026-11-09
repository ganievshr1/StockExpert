// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DocumentCard } from "../src/features/session/DocumentCard";

const mocks = vi.hoisted(() => ({
  workspace: {} as any,
  upload: vi.fn(),
  replaceDocument: vi.fn(),
  errors: [] as string[],
}));
vi.mock("../src/state/Workspace", () => ({
  useWorkspace: () => mocks.workspace,
}));
vi.mock("../src/services/api", () => ({ api: mocks }));
vi.mock("../src/components/Icon", () => ({ Icon: () => null }));
const doc = (id = "existing", mode = "auto") => ({
  id,
  kind: "requirement",
  filename: `${id}.txt`,
  meta: { text: "Труба DN 50", requirement_mode: mode },
});
const input = () =>
  screen.getByLabelText<HTMLInputElement>("Опросный лист TXT");

beforeEach(() => {
  vi.resetAllMocks();
  mocks.errors = [];
  mocks.upload.mockResolvedValue(doc("uploaded"));
  mocks.replaceDocument.mockResolvedValue(doc("replaced"));
  mocks.workspace = {
    sid: "session-10",
    active: false,
    busy: false,
    canEdit: true,
    refresh: vi.fn().mockResolvedValue(undefined),
    act: async (fn: () => Promise<unknown>) => {
      try {
        await fn();
        return true;
      } catch (error) {
        mocks.errors.push((error as Error).message);
        return false;
      }
    },
    data: {
      session: {
        base_snapshot: { source_session_id: "ready-base" },
        schema_doc: { mappings: [] },
      },
      job: { status: "NOT_STARTED" },
      documents: [],
    },
  };
});
afterEach(cleanup);

describe("requirement documents", () => {
  it("keeps adding available after the first document and sends multiple selected files", async () => {
    mocks.workspace.data.documents = [doc()];
    render(<DocumentCard kind="requirement" />);
    expect(input().multiple).toBe(true);
    const files = [
      new File(["Задвижка DN 50"], "Q02.txt"),
      new File(["Кран DN 80"], "Q03.txt"),
    ];
    await userEvent.upload(input(), files);
    await userEvent.click(
      screen.getByRole("button", { name: "Загрузить файлы (2)" }),
    );
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledTimes(2));
    expect(mocks.upload).toHaveBeenNthCalledWith(
      1,
      "session-10",
      "requirement",
      files[0],
      "auto",
    );
    expect(mocks.upload).toHaveBeenNthCalledWith(
      2,
      "session-10",
      "requirement",
      files[1],
      "auto",
    );
    expect(input()).toBeTruthy();
  });

  it("previews ten CRLF-separated thermocolumn positions and sends the explicit lines mode", async () => {
    render(<DocumentCard kind="requirement" />);
    await userEvent.selectOptions(
      screen.getByLabelText("Формат потребности"),
      "lines",
    );
    const lines = Array.from(
      { length: 10 },
      (_, index) => `Термоколонка ТКС.В.32-11/1,2-Ф(У)-И${index + 4}`,
    );
    const file = new File(
      ["\uFEFF\r\n" + lines.join("\r\n\r\n") + "\r\n"],
      "Термоколонка10.txt",
    );
    await userEvent.upload(input(), file);
    expect(
      await screen.findByText(
        "Позиций к загрузке: 10. Пустые строки пропускаются.",
      ),
    ).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: "Загрузить файл" }),
    );
    await waitFor(() =>
      expect(mocks.upload).toHaveBeenCalledWith(
        "session-10",
        "requirement",
        file,
        "lines",
      ),
    );
  });

  it("does not reinterpret a multiline questionnaire when ordinary mode is selected", async () => {
    render(<DocumentCard kind="requirement" />);
    const file = new File(
      ["Кабель\nНапряжение 6000 В\n3х70"],
      "questionnaire.txt",
    );
    await userEvent.upload(input(), file);
    expect(screen.queryByText(/Позиций к загрузке/)).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Загрузить файл" }),
    );
    await waitFor(() =>
      expect(mocks.upload).toHaveBeenCalledWith(
        "session-10",
        "requirement",
        file,
        "auto",
      ),
    );
  });

  it("refreshes committed documents after partial failure and retries only the remaining file", async () => {
    mocks.upload
      .mockResolvedValueOnce(doc("first"))
      .mockRejectedValueOnce(new Error("HTTP 503"));
    render(<DocumentCard kind="requirement" />);
    const files = [
      new File(["Труба DN 20"], "first.txt"),
      new File(["Труба DN 40"], "second.txt"),
    ];
    await userEvent.upload(input(), files);
    await userEvent.click(
      screen.getByRole("button", { name: "Загрузить файлы (2)" }),
    );
    await waitFor(() =>
      expect(mocks.errors[0]).toContain("Загружено файлов: 1 из 2"),
    );
    expect(mocks.errors[0]).toContain("second.txt");
    expect(mocks.workspace.refresh).toHaveBeenCalledTimes(1);
    await userEvent.click(
      screen.getByRole("button", { name: "Загрузить файл" }),
    );
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledTimes(3));
    expect(mocks.upload.mock.calls.map((call) => call[2].name)).toEqual([
      "first.txt",
      "second.txt",
      "second.txt",
    ]);
  });

  it("replaces only one document and restores its saved parsing mode", async () => {
    mocks.workspace.data.documents = [doc("existing", "lines")];
    render(<DocumentCard kind="requirement" />);
    await userEvent.click(
      screen.getByRole("button", { name: "Заменить файл" }),
    );
    expect(input().multiple).toBe(false);
    expect(
      (screen.getByLabelText("Формат потребности") as HTMLSelectElement).value,
    ).toBe("lines");
    const first = new File(["Труба DN 20\nТруба DN 30"], "replacement.txt");
    const extra = new File(["Труба DN 50"], "ignored.txt");
    fireEvent.change(input(), { target: { files: [first, extra] } });
    await screen.findByText(
      "Позиций к загрузке: 2. Пустые строки пропускаются.",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Сохранить замену" }),
    );
    await waitFor(() =>
      expect(mocks.replaceDocument).toHaveBeenCalledWith(
        "session-10",
        "existing",
        first,
        "lines",
      ),
    );
    expect(mocks.replaceDocument).toHaveBeenCalledTimes(1);
    expect(mocks.upload).not.toHaveBeenCalled();
  });

  it("rejects non-TXT dropped files in lines mode and keeps processed sessions locked", async () => {
    const view = render(<DocumentCard kind="requirement" />);
    await userEvent.selectOptions(
      screen.getByLabelText("Формат потребности"),
      "lines",
    );
    fireEvent.drop(input().closest("label")!, {
      dataTransfer: { files: [new File(["PDF"], "list.pdf")] },
    });
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Для списка позиций выберите только TXT-файлы.",
    );
    expect(
      (
        screen.getByRole("button", {
          name: "Загрузить файл",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    mocks.workspace.data.documents = [doc()];
    mocks.workspace.data.job.status = "COMPLETED";
    view.rerender(<DocumentCard kind="requirement" />);
    expect(input().disabled).toBe(true);
    expect(
      (screen.getByLabelText("Формат потребности") as HTMLSelectElement)
        .disabled,
    ).toBe(true);
    expect(
      (
        screen.getByRole("button", {
          name: "Заменить файл",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it("keeps the residual Excel upload singular", () => {
    render(<DocumentCard kind="residuals" />);
    const excel = screen.getByLabelText<HTMLInputElement>(
      "База остатков Excel",
    );
    expect(excel.multiple).toBe(false);
    expect(excel.accept).toBe(".xlsx");
    expect(screen.queryByLabelText("Формат потребности")).toBeNull();
  });
});
