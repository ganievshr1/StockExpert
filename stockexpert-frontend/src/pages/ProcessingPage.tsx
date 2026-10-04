import { useState } from "react";
import { useWorkspace } from "../state/Workspace";
import { api } from "../services/api";
import { Icon } from "../components/Icon";
import { Stepper } from "../components/Stepper";
import { SessionControls } from "../features/session/SessionControls";
import {
  Badge,
  Empty,
  Loading,
  Modal,
  Notice,
  PageTitle,
  Progress,
  stages,
} from "../components/ui";
const groups = [
  {
    title: "Проверка файлов",
    detail: "Чтение остатков и потребности",
    icon: "check",
    stages: ["QUEUED", "PARSING", "SCHEMA_DISCOVERY", "REQUIREMENT_PARSING"],
  },
  {
    title: "Извлечение характеристик",
    detail: "Приводим наименования, единицы и параметры к единому виду",
    icon: "refresh",
    stages: ["PASSPORT_NORMALIZATION", "EXTRACTION_CLASSIFICATION"],
  },
  {
    title: "Классификация",
    detail: "Формирование категорий по нормализованным паспортам",
    icon: "layers",
    stages: ["TAXONOMY_BUILD"],
  },
  {
    title: "Подбор кандидатов",
    detail: "Проверка правил и ранжирование складских запасов",
    icon: "search",
    stages: [
      "VECTORIZATION",
      "HYBRID_SEARCH",
      "HARD_RULES",
      "CROSS_ENCODER",
      "PERSIST_MATCHES",
      "EXPLANATIONS",
    ],
  },
];
export function ProcessingPage() {
  const w = useWorkspace();
  const [settings, setSettings] = useState(false);
  if (!w.sid) return <Empty />;
  if (!w.data) return <Loading />;
  const { session, job, metrics } = w.data;
  const index = groups.findIndex((group) =>
    group.stages.includes(job.stage || ""),
  );
  const next =
    session.status === "TAXONOMY_REVIEW"
      ? "taxonomy"
      : session.status === "MATCHED"
        ? "shortlist"
        : "data";
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow="Выполнение"
        title="Обработка данных"
        description="Можно перейти к другим разделам — обработка продолжится в фоне."
        actions={
          <button className="btn" onClick={() => setSettings(true)}>
            Настройки
          </button>
        }
      />
      <Stepper current={session.approved ? 2 : 1} />
      <section className="panel panel-spacious stack">
        <div className="row between">
          <h2 className="row">
            <Icon name="refresh" />
            {stages[job.stage || ""] || "Ожидание запуска"}
          </h2>
          <Badge value={job.status} />
        </div>
        <div className="row between" style={{ margin: "16px 0" }}>
          <strong className="metric-value">{session.progress}%</strong>
          <small>
            Нормализовано {metrics.normalized_items} из {metrics.total_rows}{" "}
            строк остатков
          </small>
        </div>
        <Progress value={session.progress} />
        <small>
          {session.llm_config.label} ·{" "}
          {w.streamFallback
            ? "Статус обновляется опросом сервера"
            : "Статус обновляется автоматически"}
        </small>
        <div>
          {groups.map((group, i) => {
            const completed =
              session.status === "MATCHED" ||
              (i < index && index >= 0) ||
              (i <= 2 && session.status === "TAXONOMY_REVIEW");
            const running = w.active && index === i;
            return (
              <div className="stage-row" key={group.title}>
                <Icon name={group.icon} />
                <div>
                  <p>{group.title}</p>
                  <small>{group.detail}</small>
                </div>
                <span
                  className={`badge ${completed ? "success" : running ? "info" : ""}`}
                >
                  {completed
                    ? "Завершено"
                    : running
                      ? "Выполняется"
                      : "Ожидание"}
                </span>
              </div>
            );
          })}
        </div>
        {job.error && (
          <Notice title="Обработка завершилась с ошибкой" tone="danger">
            {job.error}
            <button
              className="btn"
              style={{ marginTop: 12 }}
              disabled={
                w.busy ||
                w.active ||
                !w.canEdit ||
                !job.id ||
                job.status !== "FAILED"
              }
              onClick={() =>
                void w.act(
                  () => api.retry(w.sid, job.id!),
                  "Задача поставлена в очередь",
                )
              }
            >
              Повторить задачу
            </button>
          </Notice>
        )}
        <div className="row between wrap">
          <button className="btn" onClick={() => w.setTab("overview")}>
            Вернуться к сессии
          </button>
          {w.active ? (
            <button
              className="btn-danger"
              disabled={
                w.busy || !w.canEdit || job.status === "CANCEL_REQUESTED"
              }
              onClick={() =>
                void w.act(
                  () => api.cancel(w.sid),
                  "Запрошена остановка обработки",
                )
              }
            >
              {job.status === "CANCEL_REQUESTED"
                ? "Останавливаем…"
                : "Прервать обработку"}
            </button>
          ) : (
            <button className="btn-primary" onClick={() => w.setTab(next)}>
              {next === "taxonomy"
                ? "Проверить классификатор"
                : next === "shortlist"
                  ? "Открыть подбор"
                  : "Продолжить подготовку"}
            </button>
          )}
        </div>
      </section>
      <Notice title="Результаты сохраняются по мере обработки">
        При временной ошибке повторный запуск использует уже готовые паспорта с
        той же конфигурацией модели. История доступна в аудите.
      </Notice>
      {settings && (
        <Modal
          title="Управление обработкой"
          onClose={() => !w.busy && setSettings(false)}
        >
          <SessionControls />
        </Modal>
      )}
    </div>
  );
}
