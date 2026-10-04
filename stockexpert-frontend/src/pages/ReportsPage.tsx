import { ScrollArea } from "../components/ScrollArea";
import { useEffect, useState } from "react";
import { useWorkspace } from "../state/Workspace";
import { api } from "../services/api";
import {
  workspaceApi,
  saveBlob,
  type ReportSections,
} from "../services/workspace";
import { useResource } from "../hooks/useResource";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  MetricCard,
  Modal,
  Notice,
  PageTitle,
} from "../components/ui";
const sectionLabels: Record<keyof ReportSections, string> = {
  summary: "Сводные результаты",
  candidates: "Кандидаты и решения",
  evidence: "Обоснования и источники",
  history: "История действий",
};
export function ReportsPage() {
  const w = useWorkspace();
  const [sections, setSections] = useState<ReportSections>({
      summary: true,
      candidates: true,
      evidence: true,
      history: true,
    }),
    [preview, setPreview] = useState("");
  const summary = useResource(
    () => api.summary(w.sid),
    [w.sid, w.data],
    !!w.sid,
  );
  const rows = useResource(
    () =>
      api.candidates(
        w.sid,
        new URLSearchParams({ shortlisted: "false", limit: "5", offset: "0" }),
      ),
    [w.sid, w.data],
    !!w.sid,
  );
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );
  if (!w.sid) return <Empty />;
  if (!w.data) return <Loading />;
  const any = Object.values(sections).some(Boolean);
  async function download(format: "pdf" | "xlsx") {
    const blob = await workspaceApi.report(w.sid, format, sections);
    saveBlob(blob, `stockexpert-report-${w.sid.slice(0, 8)}.${format}`);
  }
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow="Документы"
        title="Отчёт для комиссии"
        description="Проверьте результаты и сформируйте протокол предзакупочной проверки."
        actions={
          <>
            <button
              className="btn"
              disabled={!any || w.busy}
              onClick={() => void w.act(() => download("xlsx"), "Excel скачан")}
            >
              Скачать Excel
            </button>
            <button
              className="btn-primary"
              disabled={!any || w.busy}
              onClick={() => void w.act(() => download("pdf"), "PDF скачан")}
            >
              Скачать PDF
            </button>
          </>
        }
      />
      {summary.error && <ErrorBox message={summary.error} />}
      <div className="metric-grid three">
        <MetricCard
          title="Проверено системой"
          value={`${summary.data?.pairs_evaluated ?? 0} пар`}
          detail="За последний завершённый расчёт"
        />
        <MetricCard
          title="В shortlist"
          value={`${summary.data?.shortlisted_pairs ?? 0} пар`}
          detail="Кандидаты для оценки экспертом"
        />
        <MetricCard
          title="Требуют проверки"
          value={`${summary.data?.needs_review_pairs ?? 0} пар`}
          detail="Документ требует согласования"
        />
      </div>
      <div className="split">
        <article className="panel panel-spacious report-paper stack">
          <div className="row between">
            <span className="eyebrow">
              STOCKEXPERT / ПРОТОКОЛ {w.sid.slice(0, 8)}
            </span>
            <span className="badge warning">Проект</span>
          </div>
          <div className="report-heading stack">
            <h2>Протокол предзакупочной проверки запасов</h2>
            <p className="muted">
              {w.data.session.title} ·{" "}
              {new Date().toLocaleDateString("ru-RU", {
                day: "numeric",
                month: "long",
                year: "numeric",
              })}
            </p>
          </div>
          {sections.summary && (
            <>
              <h3>Результаты сопоставления</h3>
              <p className="muted">
                Проверено {summary.data?.pairs_evaluated || 0} пар. В shortlist
                включено {summary.data?.shortlisted_pairs || 0}. Выявлено{" "}
                {summary.data?.contradicted_pairs || 0} противоречий; для{" "}
                {summary.data?.needs_review_pairs || 0} пар требуется
                дополнительная проверка.
              </p>
            </>
          )}
          {rows.error && <ErrorBox message={rows.error} />}{" "}
          {sections.candidates && (
            <ScrollArea label="Кандидаты и решения в отчёте">
              <table>
                <thead>
                  <tr>
                    <th>Складской запас</th>
                    <th>Основание</th>
                    <th>Решение</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.data?.items.map((row) => (
                    <tr key={row.id}>
                      <td>{row.residual.source_text}</td>
                      <td>{row.reason}</td>
                      <td>
                        <Badge value={row.decision} />
                        {row.expert_comment && (
                          <small style={{ display: "block", marginTop: 8 }}>
                            {row.expert_comment}
                          </small>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollArea>
          )}
          <small>
            Предпросмотр первых {rows.data?.items.length || 0} пар. Полные
            выбранные разделы доступны в выгрузке.
          </small>
          {!any && (
            <Notice title="Выберите разделы отчёта" tone="warning">
              Отметьте хотя бы один раздел справа.
            </Notice>
          )}
        </article>
        <aside className="panel stack">
          <h3>Состав отчёта</h3>
          {Object.entries(sectionLabels).map(([key, label]) => (
            <label className="checkbox-label" key={key}>
              <input
                type="checkbox"
                checked={sections[key as keyof ReportSections]}
                onChange={(event) =>
                  setSections({ ...sections, [key]: event.target.checked })
                }
              />
              {label}
            </label>
          ))}
          <div className="danger-zone">
            <small>
              Подготовлен на основе последнего завершённого расчёта сессии.
            </small>
          </div>
          <button
            className="btn"
            disabled={!any || w.busy}
            onClick={() =>
              void w.act(async () => {
                const blob = await workspaceApi.report(
                  w.sid,
                  "pdf",
                  sections,
                  true,
                );
                setPreview(URL.createObjectURL(blob));
              }, "Предпросмотр готов")
            }
          >
            Предпросмотр PDF
          </button>
        </aside>
      </div>
      <Notice title="Оценка экономии требует фактических данных" tone="warning">
        Денежный эффект и объём вовлечения не рассчитаны. Протокол требует
        экспертного согласования и не является подписанным документом.
      </Notice>
      {preview && (
        <Modal title="Предпросмотр PDF" onClose={() => setPreview("")}>
          <iframe
            className="report-preview"
            title="Протокол проверки запасов"
            src={preview}
          />
          <div className="modal-actions">
            <button className="btn" onClick={() => setPreview("")}>
              Закрыть
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
