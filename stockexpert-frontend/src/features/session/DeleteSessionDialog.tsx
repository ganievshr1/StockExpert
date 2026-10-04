import { useState } from "react";
import { ErrorBox, Modal, Notice } from "../../components/ui";
import { useWorkspace } from "../../state/Workspace";
import type { ProcurementSession } from "../../types";
import type { SessionDeletionResult } from "../../services/api";

export function DeleteSessionDialog({
  session,
  total,
  onClose,
  onDeleted,
}: {
  session?: Pick<ProcurementSession, "id" | "title">;
  total?: number;
  onClose: () => void;
  onDeleted?: (result: SessionDeletionResult) => void;
}) {
  const w = useWorkspace();
  const [error, setError] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [failed, setFailed] = useState<SessionDeletionResult["failed"]>([]);
  const all = !session;
  const submit = async () => {
    setError("");
    setFailed([]);
    try {
      const result = await w.deleteSessions(session?.id);
      onDeleted?.(result);
      if (result.failed.length) setFailed(result.failed);
      else onClose();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <Modal
      title={all ? "Удалить все сессии?" : "Удалить сессию?"}
      onClose={() => {
        if (!w.busy) onClose();
      }}
    >
      <div className="stack">
        <Notice title="Удаление без возможности восстановления" tone="warning">
          {all
            ? `Будут удалены все сессии текущей организации${total == null ? "" : ` (${total})`}, включая сессии на других страницах и за пределами фильтра.`
            : `Будет удалена сессия «${session.title}».`}{" "}
          Исходные файлы, результаты, отчёты, метрики и аудит этих сессий будут
          удалены. Сначала скачайте нужные отчёты.
        </Notice>
        <p className="small muted">
          Общие запасы в оставшихся сессиях сохранятся. Использованные позиции
          не станут доступными повторно. Активную обработку необходимо
          остановить и дождаться её завершения.
        </p>
        {all && (
          <label className="stack">
            Для подтверждения введите УДАЛИТЬ
            <input
              aria-label="Подтверждение удаления всех сессий"
              autoComplete="off"
              value={confirmation}
              disabled={w.busy}
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </label>
        )}
        {error && <ErrorBox message={error} />}
        {!!failed.length && (
          <div role="alert" className="stack">
            <Notice
              title={`Не удалось удалить сессий: ${failed.length}`}
              tone="danger"
            >
              Успешно удалённые сессии уже убраны из списка. Исправьте причину и
              повторите удаление оставшихся.
            </Notice>
            <ul>
              {failed.map((item) => (
                <li key={item.session_id}>
                  {item.title}: {item.detail}
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="modal-actions">
          <button className="btn" disabled={w.busy} onClick={onClose}>
            Отмена
          </button>
          <button
            className="btn-danger"
            disabled={
              w.busy || !w.canEdit || (all && confirmation.trim() !== "УДАЛИТЬ")
            }
            onClick={() => void submit()}
          >
            {w.busy
              ? "Удаление…"
              : all
                ? "Удалить все сессии"
                : "Удалить сессию"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
