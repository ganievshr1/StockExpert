import { useState } from "react";
import { useWorkspace } from "../../state/Workspace";
import { api } from "../../services/api";
import { Modal, Notice } from "../../components/ui";
import { DeleteSessionDialog } from "./DeleteSessionDialog";
export function SessionControls() {
  const w = useWorkspace();
  const [deleting, setDeleting] = useState(false);
  const [size, setSize] = useState(150),
    [confirm, setConfirm] = useState<"files" | "stock" | null>(null);
  if (!w.data) return null;
  const { session, documents, job } = w.data;
  const mappingReady =
    session.schema_doc.taxonomy_mode === "passports"
      ? session.schema_doc.mapping_approved
      : session.approved;
  const disabled = w.busy || w.active || !w.canEdit;
  const valid = Number.isInteger(size) && size >= 1 && size <= 1000;
  const requirement = documents.some(
    (document) => document.kind === "requirement",
  );
  async function launch(action: () => Promise<unknown>, message: string) {
    if (await w.act(action, message)) w.setTab("processing");
  }
  return (
    <div className="stack">
      <div className="row wrap">
        <label>
          Размер shortlist
          <input
            aria-label="Размер shortlist"
            style={{ width: 130 }}
            type="number"
            min={1}
            max={1000}
            value={size}
            onChange={(event) => setSize(Number(event.target.value))}
          />
        </label>
        <button
          className="btn-primary"
          disabled={disabled || !mappingReady || !requirement || !valid}
          onClick={() =>
            void launch(
              () => api.run(w.sid, size),
              "Обработка поставлена в очередь",
            )
          }
        >
          {session.approved
            ? "Запустить подбор"
            : session.taxonomy_enabled === false ? "H1 и подбор без H2" : session.normalization_method === "regex"
              ? "H1 регулярками + H2 моделью"
              : "Нормализация и категории"}
        </button>
        {!session.base_snapshot?.reused_passports && (
          <button
            className="btn"
            disabled={disabled || !mappingReady || !requirement}
            onClick={() =>
              void launch(
                () => api.normalize(w.sid),
                "Нормализация поставлена в очередь",
              )
            }
          >
            {session.normalization_method === "regex"
              ? "Только H1: регулярки"
              : "Только H1: LLM"}
          </button>
        )}
        <button
          className="btn"
          disabled={
            disabled ||
            !valid ||
            !session.approved ||
            ![
              "NORMALIZED",
              "MATCHED",
              "FAILED",
              "APPROVED",
              "BASE_READY",
            ].includes(session.status)
          }
          onClick={() =>
            void launch(
              () => api.match(w.sid, size),
              "Повторный подбор запущен",
            )
          }
        >
          Повторить подбор
        </button>
      </div>
      {job.error && (
        <Notice title="Обработка остановлена с ошибкой" tone="danger">
          <span className="preserve-line">{job.error}</span>
          <button
            className="btn"
            style={{ marginTop: 12 }}
            disabled={disabled || !job.id || job.status !== "FAILED"}
            onClick={() =>
              void launch(
                () => api.retry(w.sid, job.id!),
                "Задача поставлена в очередь",
              )
            }
          >
            Повторить задачу
          </button>
        </Notice>
      )}
      <div className="danger-zone stack">
        <div className="row wrap">
          <button
            className="btn-danger"
            disabled={disabled}
            onClick={() => setDeleting(true)}
          >
            Удалить сессию
          </button>
          <button
            className="btn-danger"
            disabled={disabled}
            onClick={() => setConfirm("files")}
          >
            Очистить файлы сессии
          </button>
          <button
            className="btn-danger"
            disabled={disabled || !session.approved}
            onClick={() => setConfirm("stock")}
          >
            Очистить общую базу
          </button>
        </div>
        <small>
          Очистка файлов сбрасывает текущую сессию. Очистка общей базы исключает
          её запасы из всех связанных сессий, сохраняя историю решений.
        </small>
      </div>
      {confirm && (
        <Modal
          title={
            confirm === "files"
              ? "Очистить текущую сессию?"
              : "Очистить общую базу?"
          }
          onClose={() => !w.busy && setConfirm(null)}
        >
          <Notice title="Подтвердите удаление" tone="warning">
            {confirm === "files"
              ? "Будут удалены исходные файлы, паспорта, схема, результаты, задания, метрики и аудит текущей сессии. Сначала скачайте нужные отчёты."
              : "Все доступные запасы этой базы будут исключены из подбора во всех связанных сессиях. История расчётов сохранится."}
          </Notice>
          <p className="small muted" style={{ marginTop: 16 }}>
            {session.title}
          </p>
          <div className="modal-actions">
            <button
              className="btn"
              disabled={w.busy}
              onClick={() => setConfirm(null)}
            >
              Отмена
            </button>
            <button
              className="btn-danger"
              disabled={w.busy}
              onClick={async () => {
                if (
                  await w.act(
                    () =>
                      confirm === "files"
                        ? api.clearFiles(w.sid)
                        : api.clearStock(w.sid),
                    "Очистка завершена",
                  )
                )
                  setConfirm(null);
              }}
            >
              Подтвердить очистку
            </button>
          </div>
        </Modal>
      )}
      {deleting && (
        <DeleteSessionDialog
          session={session}
          onClose={() => setDeleting(false)}
        />
      )}
    </div>
  );
}
