import { ScrollArea } from "../components/ScrollArea";
import { useState } from "react";
import { useWorkspace } from "../state/Workspace";
import { api } from "../services/api";
import { workspaceApi } from "../services/workspace";
import { useDebouncedValue, useResource } from "../hooks/useResource";
import { Icon } from "../components/Icon";
import {
  Empty,
  ErrorBox,
  Notice,
  PageTitle,
  Pagination,
} from "../components/ui";
const labels: Record<string, string> = {
  SESSION_CREATED: "Сессия создана",
  DOCUMENT_UPLOADED: "Файл загружен",
  DOCUMENT_REPLACED: "Файл заменён",
  DOCUMENT_DELETED: "Файл удалён",
  DECISION_RECORDED: "Решение сохранено",
  SCHEMA_EDITED: "Схема изменена",
  TAXONOMY_APPROVED: "Схема утверждена",
  TAXONOMY_BUILT: "Классификатор построен",
  MAPPING_APPROVED: "Столбцы утверждены",
  MATCHED: "Подбор завершён",
  NORMALIZED: "Нормализация завершена",
  JOB_QUEUED: "Обработка запущена",
  QUALITY_REVIEW_SAVED: "Проверка качества сохранена",
  QUALITY_REVIEW_FINALIZED: "Проверка качества завершена",
  STOCK_USED: "Запас использован",
};
export function AuditPage() {
  const w = useWorkspace();
  const [query, setQuery] = useState(""),
    [eventType, setEventType] = useState("ALL"),
    [author, setAuthor] = useState(""),
    [from, setFrom] = useState(""),
    [to, setTo] = useState(""),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState<number | null>(null),
    [hash, setHash] = useState(""),
    [verification, setVerification] = useState<{
      verified: boolean;
      chain_valid: boolean;
      root_hash: string;
    } | null>(null);
  const q = useDebouncedValue(query);
  const rows = useResource(
    () =>
      workspaceApi.audit(
        w.sid,
        new URLSearchParams({
          q,
          event_type: eventType,
          offset: String(offset),
          limit: "20",
          ...(author ? { author_id: author } : {}),
          ...(from ? { date_from: from } : {}),
          ...(to ? { date_to: to } : {}),
        }),
      ),
    [w.sid, q, eventType, author, from, to, offset, w.data],
    !!w.sid,
  );
  const trail = useResource(() => api.trail(w.sid), [w.sid, w.data], !!w.sid);
  if (!w.sid) return <Empty />;
  const item = rows.data?.events.find((event) => event.id === selected);
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow="Прозрачность решений"
        title="Аудит действий"
        description="История обработки данных и экспертных решений по текущей сессии."
        actions={
          <button
            className="btn"
            title="Скачать всю историю текущей сессии"
            disabled={w.busy}
            onClick={() =>
              void w.act(
                () => api.download(w.sid, "audit"),
                "CSV всего аудита скачан",
              )
            }
          >
            Экспорт CSV
          </button>
        }
      />
      <section className="panel stack">
        <div className="row between">
          <h3 className="row">
            <Icon name="shield" />
            Проверка целостности
          </h3>
          <span
            className={`badge ${trail.data?.chain_valid ? "success" : "warning"}`}
          >
            {trail.data?.chain_valid
              ? "Цепочка согласована"
              : trail.loading
                ? "Проверяем…"
                : "Требует проверки"}
          </span>
        </div>
        <div className="row wrap">
          <input
            className="flex-1"
            aria-label="Ожидаемый корневой SHA-256"
            placeholder="Ожидаемый корневой SHA-256 · 64 символа"
            value={hash}
            maxLength={64}
            onChange={(event) => {
              setHash(event.target.value.trim());
              setVerification(null);
            }}
          />
          <button
            className="btn"
            disabled={w.busy}
            onClick={() =>
              void w.act(async () => {
                const result = await api.trail(w.sid);
                setHash(result.root_hash);
                setVerification(null);
                trail.refresh();
              }, "Текущий хеш получен")
            }
          >
            Получить текущий хеш
          </button>
          <button
            className="btn-primary"
            disabled={w.busy || !/^[0-9a-f]{64}$/.test(hash)}
            onClick={() =>
              void w.act(async () => {
                setVerification(await api.verify(w.sid, hash));
              }, "Проверка хеша выполнена")
            }
          >
            Проверить
          </button>
        </div>
        <small>
          Совпадение с сохранённым хешем проверяется отдельно. Проверка внутри
          базы не заменяет независимую подпись.
        </small>
        {trail.error && <ErrorBox message={trail.error} />}{" "}
        {verification && (
          <Notice
            title={
              verification.verified
                ? "Хеш совпадает, цепочка согласована"
                : "Хеш не совпадает или цепочка нарушена"
            }
            tone={verification.verified ? "success" : "danger"}
          />
        )}
      </section>
      <div className="toolbar">
        <input
          aria-label="Поиск по событию или автору"
          placeholder="Поиск по событию, автору или деталям"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOffset(0);
          }}
        />
        <select
          aria-label="Тип события"
          value={eventType}
          onChange={(event) => {
            setEventType(event.target.value);
            setOffset(0);
          }}
        >
          <option value="ALL">Все события</option>
          {rows.data?.event_types.map((value) => (
            <option value={value} key={value}>
              {labels[value] || value}
            </option>
          ))}
        </select>
        <select
          aria-label="Автор события"
          value={author}
          onChange={(event) => {
            setAuthor(event.target.value);
            setOffset(0);
          }}
        >
          <option value="">Все авторы</option>
          <option value="0">Система</option>
          {rows.data?.authors.map((value) => (
            <option key={value.id} value={value.id}>
              {value.name}
            </option>
          ))}
        </select>
      </div>
      <div className="row wrap">
        <label>
          С даты (UTC)
          <input
            type="date"
            value={from}
            onChange={(event) => {
              setFrom(event.target.value);
              setOffset(0);
            }}
          />
        </label>
        <label>
          По дату (UTC)
          <input
            type="date"
            value={to}
            onChange={(event) => {
              setTo(event.target.value);
              setOffset(0);
            }}
          />
        </label>
        <button
          className="btn-ghost"
          onClick={() => {
            setQuery("");
            setEventType("ALL");
            setAuthor("");
            setFrom("");
            setTo("");
            setOffset(0);
          }}
        >
          Сбросить фильтры
        </button>
      </div>
      {rows.error && <ErrorBox message={rows.error} />}
      <ScrollArea label="История действий">
        <table>
          <thead>
            <tr>
              <th>Время UTC</th>
              <th>Автор</th>
              <th>Событие</th>
              <th>Объект</th>
              <th>Детали</th>
            </tr>
          </thead>
          <tbody>
            {rows.data?.events.map((event) => (
              <tr
                className={selected === event.id ? "selected" : ""}
                key={event.id}
              >
                <td className="nowrap">
                  {new Date(event.created_at).toLocaleString("ru-RU", {
                    timeZone: "UTC",
                  })}
                </td>
                <td>{event.author}</td>
                <td>{labels[event.event_type] || event.event_type}</td>
                <td className="small">
                  {String(
                    event.event_details.item_id ||
                      event.event_details.document_id ||
                      event.event_details.job_id ||
                      event.event_details.title ||
                      "Сессия",
                  ).slice(0, 40)}
                </td>
                <td>
                  <button
                    className="btn-ghost"
                    onClick={() =>
                      setSelected(selected === event.id ? null : event.id)
                    }
                  >
                    {selected === event.id ? "Свернуть" : "Посмотреть"} →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.loading && !rows.data?.total && (
          <p className="muted" style={{ padding: 24 }}>
            Событий по выбранным условиям нет.
          </p>
        )}
      </ScrollArea>
      {item && (
        <section className="panel stack">
          <div className="row between">
            <h3>{labels[item.event_type] || item.event_type}</h3>
            <small>{item.event_type}</small>
          </div>
          <pre>{JSON.stringify(item.event_details, null, 2)}</pre>
          <small>Хеш события: {item.sha256_hash}</small>
          <small>Предыдущий хеш: {item.prev_hash || "Начало цепочки"}</small>
        </section>
      )}
      <Pagination
        offset={offset}
        total={rows.data?.total || 0}
        pageSize={20}
        onChange={setOffset}
        busy={rows.loading}
      />
    </div>
  );
}
