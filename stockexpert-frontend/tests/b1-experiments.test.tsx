// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { B1ExperimentPanel } from "../src/components/B1ExperimentPanel";
import type { B1Config, B1Experiment } from "../src/services/b1Experiments";

const mocks = vi.hoisted(() => ({ config: vi.fn(), list: vi.fn(), create: vi.fn(), detail: vi.fn(), cancel: vi.fn(), continue: vi.fn(), requirements: vi.fn(), saveDownload: vi.fn() }));
vi.mock("../src/services/b1Experiments", () => ({ b1Api: { ...mocks, exportPath: (id: string, format: string) => `/developer/b1/experiments/${id}/export?format=${format}` } }));
vi.mock("../src/services/api", () => ({ api: { requirements: mocks.requirements } }));
vi.mock("../src/services/developer", () => ({ saveDownload: mocks.saveDownload }));

const config: B1Config = {
  routing_mode: "shared",
  ready_bases: [{ session_id: "base-12000", title: "База 12 тысяч", normalized_rows: 12000 }],
  requirement_sessions: [{ session_id: "needs-10", title: "Потребности для B1", normalized_requirements: 10 }],
  background_sessions: [{ session_id: "background", title: "Фоновый файл", residual_rows: 12000 }],
};
const experiment: B1Experiment = { id: "run-1", title: "Проверка очередей", scenario: "concurrent_10", status: "COMPLETED", scope: "diagnostic", progress: 100, summary: { p95_seconds: 0, error_rate: 0, batch_rows_per_minute: null, scenario_makespan_seconds: 5.3 } };
const items = Array.from({ length: 10 }, (_, index) => ({ id: `need-${index + 1}`, document_id: "doc", row_index: index + 1, source_text: `Потребность ${index + 1}`, issues: [], passport: { name: `Потребность ${index + 1}`, attributes: { diameter: 10 + index }, category_id: "cat" } }));
const startButton = () => screen.getByRole<HTMLButtonElement>("button", { name: "Запустить проверку B1" });
async function selectInputs() {
  await screen.findByText(/Текущая конфигурация:/);
  await userEvent.selectOptions(screen.getByLabelText("Готовая база (цель — 12 000 строк)"), "base-12000");
  await userEvent.selectOptions(screen.getByLabelText("Сессия с 10 потребностями"), "needs-10");
  await screen.findByText("Входные данные: 10 из 10 потребностей с параметрами");
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.config.mockResolvedValue(config);
  mocks.list.mockResolvedValue({ items: [] });
  mocks.requirements.mockResolvedValue({ items });
  mocks.create.mockResolvedValue(experiment);
  mocks.detail.mockResolvedValue(experiment);
  mocks.cancel.mockResolvedValue({ ...experiment, status: "CANCELLED" });
  mocks.continue.mockResolvedValue({ ...experiment, status: "RUNNING" });
  mocks.saveDownload.mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("B1 real backend experiment", () => {
  it("requires ten normalized requirements and sends the selected existing session IDs", async () => {
    render(<B1ExperimentPanel />);
    await screen.findByText(/Текущая конфигурация:/);
    expect(startButton().disabled).toBe(true);
    await selectInputs();
    expect(startButton().disabled).toBe(false);
    expect(screen.getByText('{"diameter":10}')).toBeTruthy();
    await userEvent.click(startButton());
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith({ scenario: "concurrent_10", base_session_id: "base-12000", requirement_session_id: "needs-10" }));
    expect(await screen.findByText("Диагностический результат")).toBeTruthy();
    expect(mocks.create).toHaveBeenCalledTimes(1);
  });

  it("does not permit a session with fewer than ten normalized passports", async () => {
    mocks.requirements.mockResolvedValue({ items: [...items.slice(0, 9), { ...items[9], passport: null }] });
    render(<B1ExperimentPanel />);
    await screen.findByText(/Текущая конфигурация:/);
    await userEvent.selectOptions(screen.getByLabelText("Готовая база (цель — 12 000 строк)"), "base-12000");
    await userEvent.selectOptions(screen.getByLabelText("Сессия с 10 потребностями"), "needs-10");
    expect(await screen.findByText(/Для запуска нужны 10 нормализованных потребностей/)).toBeTruthy();
    expect(startButton().disabled).toBe(true);
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it("distinguishes sequential diagnostics from a full campaign that requires background work", async () => {
    render(<B1ExperimentPanel />);
    await selectInputs();
    await userEvent.selectOptions(screen.getByLabelText("Сценарий проверки"), "sequential_10");
    expect(screen.getByText(/следующий запрос начинается после завершения предыдущего/)).toBeTruthy();
    await userEvent.selectOptions(screen.getByLabelText("Сценарий проверки"), "confirmation");
    expect(screen.getByText("Подтверждающий протокол")).toBeTruthy();
    expect(screen.getByText(/600 запланированных запросов за 30 минут/)).toBeTruthy();
    expect(startButton().disabled).toBe(true);
    await userEvent.selectOptions(screen.getByLabelText("Фоновая обработка (обязательно)"), "background");
    await userEvent.click(startButton());
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith({ scenario: "confirmation", base_session_id: "base-12000", requirement_session_id: "needs-10", background_session_id: "background" }));
  });

  it("keeps zero metrics distinct from unavailable values and exports server results", async () => {
    mocks.list.mockResolvedValue({ items: [experiment] });
    render(<B1ExperimentPanel />);
    expect(await screen.findByText("0 с")).toBeTruthy();
    expect(screen.getByText("0%")).toBeTruthy();
    expect(screen.getByText("5,3 с")).toBeTruthy();
    expect(screen.getByText("Не измерено")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Скачать CSV" }));
    expect(mocks.saveDownload).toHaveBeenCalledWith("/developer/b1/experiments/run-1/export?format=csv", "b1-run-1.csv");
  });

  it("resumes only when the configured routing mode matches the next run", async () => {
    const awaiting: B1Experiment = { ...experiment, status: "AWAITING_PROFILE", scope: "confirmation", expected_routing_mode: "split" };
    mocks.list.mockResolvedValue({ items: [awaiting] });
    mocks.detail.mockResolvedValue(awaiting);
    render(<B1ExperimentPanel />);
    const resume = await screen.findByRole<HTMLButtonElement>("button", { name: "Продолжить измерение" });
    expect(resume.disabled).toBe(true);
    mocks.config.mockResolvedValue({ ...config, routing_mode: "split" });
    await userEvent.click(screen.getByRole("button", { name: "Обновить конфигурацию" }));
    await waitFor(() => expect(resume.disabled).toBe(false));
    await userEvent.click(resume);
    await waitFor(() => expect(mocks.continue).toHaveBeenCalledWith("run-1"));
  });


  it("omits a previously selected background when returning to diagnostics", async () => {
    render(<B1ExperimentPanel />);
    await selectInputs();
    await userEvent.selectOptions(screen.getByLabelText("Сценарий проверки"), "confirmation");
    await userEvent.selectOptions(screen.getByLabelText("Фоновая обработка (обязательно)"), "background");
    await userEvent.selectOptions(screen.getByLabelText("Сценарий проверки"), "sequential_10");
    expect(screen.queryByLabelText("Фоновая обработка (обязательно)")).toBeNull();
    await userEvent.click(startButton());
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith({ scenario: "sequential_10", base_session_id: "base-12000", requirement_session_id: "needs-10" }));
  });

  it("resumes an interrupted campaign and restarts polling after terminal status", async () => {
    const interrupted: B1Experiment = { ...experiment, status: "INTERRUPTED", expected_routing_mode: "shared", plan: [{ index: 0, pair_index: 1, routing_mode: "shared", status: "PENDING" }] };
    mocks.list.mockResolvedValue({ items: [interrupted] });
    mocks.detail.mockResolvedValue(interrupted);
    render(<B1ExperimentPanel />);
    const resume = await screen.findByRole<HTMLButtonElement>("button", { name: "Продолжить измерение" });
    expect(resume.disabled).toBe(false);
    expect(screen.getByRole("button", { name: "Остановить эксперимент" })).toBeTruthy();
    const before = mocks.detail.mock.calls.length;
    mocks.detail.mockResolvedValue({ ...interrupted, status: "RUNNING", progress: 40 });
    await userEvent.click(resume);
    await waitFor(() => expect(mocks.detail.mock.calls.length).toBeGreaterThan(before));
    await waitFor(() => expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("40"));
  });

  it("polls real progress and stops polling when the panel is closed", async () => {
    vi.useFakeTimers();
    const running: B1Experiment = { ...experiment, status: "RUNNING", progress: 30 };
    mocks.list.mockResolvedValue({ items: [running] });
    mocks.detail.mockResolvedValue(running);
    const view = render(<B1ExperimentPanel />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(mocks.detail).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("30");
    mocks.detail.mockResolvedValue({ ...running, progress: 60 });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("60");
    fireEvent.click(screen.getByRole("button", { name: "Остановить эксперимент" }));
    await act(async () => { await Promise.resolve(); });
    expect(mocks.cancel).toHaveBeenCalledWith("run-1");
    view.unmount();
    const calls = mocks.detail.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(mocks.detail).toHaveBeenCalledTimes(calls);
  });
});
