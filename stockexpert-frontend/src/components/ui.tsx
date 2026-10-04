import { useEffect, useRef, type ReactNode } from "react";
import { Icon } from "./Icon";
export const statuses: Record<string, string> = {
  MAPPING_APPROVED: "Столбцы утверждены",
  TAXONOMY_REVIEW: "Проверка таксономии",
  USED: "Запас использован",
  CLEARED: "Удалено из общей базы",
  BASE_READY: "База готова",
  CANCELLED: "Отменено",
  CANCEL_REQUESTED: "Остановка запрошена",
  CLEANING: "Очистка",
  CLEANUP_FAILED: "Повторите очистку",
  CREATED: "Новая сессия",
  UPLOADED: "Файлы загружены",
  QUEUED: "В очереди",
  RUNNING: "Обработка",
  REVIEW_REQUIRED: "Проверка схемы",
  APPROVED: "Схема утверждена",
  NORMALIZED: "Нормализовано",
  MATCHED: "Подбор завершён",
  FAILED: "Ошибка",
  COMPLETED: "Завершено",
  NOT_STARTED: "Не запущено",
  NEEDS_REVIEW: "Нужна проверка",
  CONTRADICTED: "Есть противоречия",
  PASSES_KNOWN_RULES: "По правилам проверено",
  RECOMMENDED: "Рекомендовано экспертом",
  REJECTED_MANUAL: "Отклонено экспертом",
  PENDING: "Без решения",
};
export const stages: Record<string, string> = {
  PASSPORT_NORMALIZATION: "Нормализация всех паспортов",
  TAXONOMY_BUILD: "Категории по всем JSON-паспортам",
  CANCELLED: "Отменено",
  CANCELLING: "Ожидание остановки запросов",
  SCHEMA_DISCOVERY: "Определение столбцов Excel",
  REQUIREMENT_PARSING: "Разбор потребности",
  HARD_RULES: "Проверка ограничений",
  PERSIST_MATCHES: "Сохранение результатов",
  QUEUED: "Ожидание исполнителя",
  PARSING: "Разбор документов",
  EXTRACTION_CLASSIFICATION: "Извлечение характеристик",
  VECTORIZATION: "Индексирование запасов",
  HYBRID_SEARCH: "Гибридный поиск",
  CROSS_ENCODER: "Ранжирование кандидатов",
  EXPLANATIONS: "Подготовка результатов",
  COMPLETED: "Завершено",
};
export const format = (value: number) => value.toLocaleString("ru-RU");
export function Badge({ value }: { value: string }) {
  const tone = [
    "FAILED",
    "CONTRADICTED",
    "REJECTED_MANUAL",
    "CLEANUP_FAILED",
  ].includes(value)
    ? "danger"
    : [
          "MATCHED",
          "COMPLETED",
          "APPROVED",
          "RECOMMENDED",
          "PASSES_KNOWN_RULES",
          "USED",
          "VERIFIED",
        ].includes(value)
      ? "success"
      : [
            "NEEDS_REVIEW",
            "REVIEW_REQUIRED",
            "TAXONOMY_REVIEW",
            "PENDING",
          ].includes(value)
        ? "warning"
        : [
              "CREATED",
              "RUNNING",
              "QUEUED",
              "UPLOADED",
              "MAPPING_APPROVED",
            ].includes(value)
          ? "info"
          : "";
  return <span className={`badge ${tone}`}>{statuses[value] || value}</span>;
}
export function Loading() {
  return (
    <div className="loading-state" role="status">
      <span className="spinner" />
      Загружаем данные…
    </div>
  );
}
export function Empty({
  title = "Выберите закупочную сессию",
  description = "Создайте новую сессию или выберите существующую в верхней панели.",
  children,
}: {
  title?: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="panel empty-state">
      <Icon name="box" size={24} />
      <h2>{title}</h2>
      <p className="muted">{description}</p>
      {children}
    </div>
  );
}
export function PageTitle({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-title">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}
export function Notice({
  title,
  children,
  tone = "info",
}: {
  title: string;
  children?: ReactNode;
  tone?: "info" | "warning" | "success" | "danger";
}) {
  return (
    <div className={`notice ${tone}`}>
      <Icon
        name={
          tone === "warning" || tone === "danger"
            ? "alert"
            : tone === "success"
              ? "check"
              : "info"
        }
      />
      <div>
        <p>{title}</p>
        {children && <small>{children}</small>}
      </div>
    </div>
  );
}
export function ErrorBox({ message }: { message: string }) {
  return (
    <div role="alert">
      <Notice title={message} tone="danger" />
    </div>
  );
}
let openModalCount = 0;
let bodyOverflowBeforeModal = "";
let rootOverflowBeforeModal = "";

export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (openModalCount === 0) {
      bodyOverflowBeforeModal = document.body.style.overflow;
      rootOverflowBeforeModal = document.documentElement.style.overflow;
    }
    openModalCount++;
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";
    dialog?.showModal();
    return () => {
      dialog?.close();
      openModalCount--;
      if (openModalCount === 0) {
        document.body.style.overflow = bodyOverflowBeforeModal;
        document.documentElement.style.overflow = rootOverflowBeforeModal;
      }
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-label={title}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      className="modal"
    >
      <div className="modal-header">
        <h2>{title}</h2>
        <button
          type="button"
          className="icon-btn"
          onClick={onClose}
          aria-label="Закрыть"
        >
          <Icon name="x" />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function Pagination({
  offset,
  total,
  pageSize = 20,
  onChange,
  busy = false,
}: {
  offset: number;
  total: number;
  pageSize?: number;
  onChange: (value: number) => void;
  busy?: boolean;
}) {
  return (
    <div className="pagination">
      <span>
        Показано {total ? Math.min(offset + 1, total) : 0}–
        {Math.min(total, offset + pageSize)} из {format(total)}
      </span>
      <div className="row">
        <button
          className="btn"
          disabled={busy || offset === 0}
          onClick={() => onChange(Math.max(0, offset - pageSize))}
        >
          Назад
        </button>
        <span>
          {total ? Math.floor(offset / pageSize) + 1 : 0} /{" "}
          {Math.ceil(total / pageSize)}
        </span>
        <button
          className="btn"
          disabled={busy || offset + pageSize >= total}
          onClick={() => onChange(offset + pageSize)}
        >
          Далее
        </button>
      </div>
    </div>
  );
}
export function MetricCard({
  title,
  value,
  detail,
}: {
  title: string;
  value: ReactNode;
  detail?: ReactNode;
}) {
  return (
    <div className="panel metric-card">
      <p>{title}</p>
      <div className="metric-value">{value}</div>
      <small>{detail}</small>
    </div>
  );
}
export function Progress({ value }: { value: number }) {
  const safe = Math.min(100, Math.max(0, value || 0));
  return (
    <div
      className="progress-track"
      role="progressbar"
      aria-label="Ход выполнения"
      aria-valuenow={safe}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span style={{ width: `${safe}%` }} />
    </div>
  );
}
export const percentage = (value: number | null | undefined) =>
  value == null
    ? "Не измерено"
    : `${(100 * value).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
export const seconds = (value: number | null | undefined) =>
  value == null
    ? "—"
    : `${value.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} с`;
