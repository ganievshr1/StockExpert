import { useState } from "react";
import { useWorkspace } from "../state/Workspace";
import { useResource } from "../hooks/useResource";
import { workspaceApi } from "../services/workspace";
import { SessionModelPanel } from "../components/ModelPicker";
import { SessionControls } from "../features/session/SessionControls";
import { Stepper } from "../components/Stepper";
import { Icon } from "../components/Icon";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  MetricCard,
  Modal,
  Notice,
  PageTitle,
  Progress,
  percentage,
  seconds,
} from "../components/ui";
export function SessionOverviewPage() {
  const w = useWorkspace();
  const [settings, setSettings] = useState(false),
    [management, setManagement] = useState(false);
  const metrics = useResource(
    () => workspaceApi.measurement(w.sid),
    [w.sid, w.data],
    !!w.sid,
  );
  if (!w.sid) return <Empty />;
  if (!w.data) return <Loading />;
  const { session, documents } = w.data;
  const data = metrics.data;
  const e = data?.efficiency,
    c = data?.quality.coverage;
  const step =
    session.status === "MATCHED"
      ? 3
      : session.approved
        ? 2
        : session.schema_doc.mapping_approved
          ? 1
          : 0;
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow={`Сессия ${w.sid.slice(0, 8)}`}
        title={session.title}
        description={
          w.active
            ? "Обработка выполняется. Вы можете перейти к другим разделам."
            : session.status === "MATCHED"
              ? "Остатки и потребность загружены. Подбор завершён."
              : "Загрузите источники, проверьте классификатор и запустите подбор."
        }
        actions={
          <button
            className="btn-primary"
            onClick={() =>
              w.setTab(
                w.active
                  ? "processing"
                  : session.status === "MATCHED"
                    ? "shortlist"
                    : "data",
              )
            }
          >
            {w.active
              ? "Ход обработки"
              : session.status === "MATCHED"
                ? "Открыть подбор"
                : "Открыть данные"}
          </button>
        }
      />
      <Stepper current={step} />
      {metrics.error && <ErrorBox message={metrics.error} />}
      <div className="metric-grid">
        <MetricCard
          title="Потребностей с кандидатами"
          value={
            e ? `${e.requirements_with_candidates} / ${e.requirements}` : "—"
          }
          detail={percentage(e?.requirement_coverage)}
        />
        <MetricCard
          title="Пар для оценки"
          value={e?.pairs_before ?? "—"}
          detail={`${e?.shortlisted_pairs || 0} включено в shortlist`}
        />
        <MetricCard
          title="Время подбора"
          value={seconds(e?.latest_job_seconds)}
          detail="Последний завершённый расчёт"
        />
        <MetricCard
          title="Проверено экспертом"
          value={`${c?.pairs_reviewed || 0} / ${c?.pairs_total || 0}`}
          detail={
            data?.quality.finalized
              ? "Проверка завершена"
              : "Разметка ещё не завершена"
          }
        />
      </div>
      <div className="split">
        <section className="panel stack">
          <h3>
            {w.active
              ? "Данные обрабатываются"
              : session.status === "MATCHED"
                ? "Требуется экспертное решение"
                : "Следующее действие"}
          </h3>
          <Notice
            title={
              w.active
                ? "Обработка продолжается"
                : session.status === "MATCHED"
                  ? `${data?.matching.needs_review_pairs || 0} пар требуют проверки`
                  : "Подготовьте данные сессии"
            }
            tone={session.status === "MATCHED" ? "warning" : "info"}
          >
            {session.status === "MATCHED"
              ? "Проверьте отличия и пропуски параметров перед использованием запасов."
              : "Загрузите базу Excel и опросный лист TXT, затем проверьте столбцы и категории."}
          </Notice>
          <div className="row between small">
            <span className="muted">Проверено пар</span>
            <span>
              {c?.pairs_reviewed || 0} из {c?.pairs_total || 0} ·{" "}
              {percentage(
                c?.pairs_total ? (c.pairs_reviewed || 0) / c.pairs_total : 0,
              )}
            </span>
          </div>
          <Progress
            value={
              c?.pairs_total
                ? (100 * (c.pairs_reviewed || 0)) / c.pairs_total
                : 0
            }
          />
          <div>
            <button
              className="btn-primary"
              onClick={() =>
                w.setTab(
                  w.active
                    ? "processing"
                    : session.status === "MATCHED"
                      ? "quality"
                      : "data",
                )
              }
            >
              {w.active
                ? "Открыть обработку"
                : session.status === "MATCHED"
                  ? "Продолжить экспертную проверку"
                  : "Загрузить и проверить данные"}
            </button>
          </div>
        </section>
        <section className="panel stack">
          <h3>Параметры сессии</h3>
          <small>
            H1:{" "}
            {session.normalization_method === "regex"
              ? "Регулярные выражения · CPU"
              : "LLM"}
          </small>
          <small>H2: {session.taxonomy_enabled === false ? "отключён — поиск без категорий" : "включён — с категориями"}</small>
          {(session.taxonomy_enabled !== false || session.normalization_method === 'llm') && <p>Модель: {session.llm_config.label}</p>}
          <div>
            <Badge value={session.status} />
          </div>
          <small>
            {session.llm_locked
              ? "Модель зафиксирована после запуска для воспроизводимости результатов."
              : "Модель можно изменить до первого запуска."}
          </small>
          <button className="btn" onClick={() => setSettings(true)}>
            Настройки сессии
          </button>
          <button className="btn" onClick={() => w.setTab('metrics')}>
            Скачать результаты расчёта
          </button>
        </section>
      </div>
      <section className="panel stack">
        <div className="row between">
          <h3>Исходные документы</h3>
          <button className="btn-ghost" onClick={() => w.setTab("data")}>
            Открыть данные
          </button>
        </div>
        <div className="row wrap between">
          {documents.map((document) => (
            <div className="row" key={document.id}>
              <Icon name="file" />
              <span>{document.filename}</span>
              <small>
                {document.kind === "residual"
                  ? "База остатков"
                  : "Опросный лист"}
              </small>
            </div>
          ))}
          {!documents.length && (
            <p className="muted">Документы ещё не загружены.</p>
          )}
        </div>
      </section>
      <footer className="page-footer">
        <span>
          Создано: {new Date(session.created_at).toLocaleString("ru-RU")}
        </span>
        <button className="btn" onClick={() => setManagement(true)}>
          Управление данными
        </button>
      </footer>
      {settings && (
        <Modal title="Настройки сессии" onClose={() => setSettings(false)}>
          <SessionModelPanel />
        </Modal>
      )}
      {management && (
        <Modal
          title="Управление данными и запуском"
          onClose={() => !w.busy && setManagement(false)}
        >
          <SessionControls />
        </Modal>
      )}
    </div>
  );
}
