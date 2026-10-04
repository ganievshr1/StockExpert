import { ScrollArea } from "../components/ScrollArea";
import { useEffect, useState } from "react";
import { DeleteSessionDialog } from "../features/session/DeleteSessionDialog";
import type { ProcurementSession } from "../types";
import { useWorkspace } from "../state/Workspace";
import { workspaceApi } from "../services/workspace";
import { useDebouncedValue, useResource } from "../hooks/useResource";
import {
  Badge,
  ErrorBox,
  Loading,
  MetricCard,
  PageTitle,
  Pagination,
  percentage,
  statuses,
} from "../components/ui";
export function DashboardPage({ onCreate }: { onCreate: () => void }) {
  const w = useWorkspace();
  const [deletion, setDeletion] = useState<ProcurementSession | "all" | null>(
    null,
  );
  const [query, setQuery] = useState(""),
    [status, setStatus] = useState("ALL"),
    [days, setDays] = useState("0"),
    [offset, setOffset] = useState(0);
  const q = useDebouncedValue(query);
  const overview = useResource(
    () => workspaceApi.overview(Number(days)),
    [days, w.data, w.sessions],
  );
  const sessions = useResource(
    () =>
      workspaceApi.sessions(
        new URLSearchParams({
          q,
          status,
          days,
          offset: String(offset),
          limit: "10",
        }),
      ),
    [q, status, days, offset, w.sessions],
  );
  const data = overview.data;
  useEffect(() => {
    if (
      !sessions.loading &&
      sessions.data &&
      offset >= sessions.data.total &&
      offset > 0
    )
      setOffset(Math.max(0, Math.ceil(sessions.data.total / 10) - 1) * 10);
  }, [sessions.loading, sessions.data, offset]);
  const efficiency = data?.efficiency;
  const refresh = () => {
    overview.refresh();
    sessions.refresh();
    void w.reloadSessions();
  };
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow="Обзор"
        title="Рабочий стол"
        description="Проверяйте запасы, согласовывайте решения и контролируйте закупки."
        actions={
          <button
            className="btn-primary"
            disabled={!w.canEdit}
            onClick={onCreate}
          >
            Создать сессию
          </button>
        }
      />
      <div className="metric-grid">
        <MetricCard
          title="Активные сессии"
          value={data?.active_sessions ?? "—"}
          detail="За выбранный период"
        />
        <MetricCard
          title="Потребностей с кандидатами"
          value={
            efficiency
              ? `${efficiency.requirements_with_candidates} из ${efficiency.requirements}`
              : "—"
          }
          detail="Последний завершённый подбор"
        />
        <MetricCard
          title="Требуют проверки"
          value={data?.needs_review_pairs ?? "—"}
          detail="Пары с неполными характеристиками"
        />
        <MetricCard
          title="Повторно использовано"
          value={percentage(efficiency?.reuse_rate)}
          detail={
            efficiency
              ? `${efficiency.reused_passports} из ${efficiency.stock_rows} паспортов`
              : "После завершения подбора"
          }
        />
      </div>
      {overview.error && <ErrorBox message={overview.error} />}
      <section className="panel stack">
        <div className="row between">
          <h3>Закупочные сессии</h3>
          <div className="row wrap">
            <button
              className="btn-ghost"
              onClick={() => {
                setQuery("");
                setStatus("ALL");
                setDays("0");
                setOffset(0);
              }}
            >
              Все сессии
            </button>
            {w.canEdit && (
              <button
                className="btn-danger"
                disabled={w.busy}
                onClick={() => setDeletion("all")}
              >
                Удалить все сессии
              </button>
            )}
          </div>
        </div>
        <div className="toolbar">
          <input
            aria-label="Поиск по названию сессии"
            placeholder="Поиск по названию сессии"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setOffset(0);
            }}
          />
          <select
            aria-label="Статус сессии"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}
          >
            <option value="ALL">Все статусы</option>
            {[
              "CREATED",
              "UPLOADED",
              "QUEUED",
              "RUNNING",
              "REVIEW_REQUIRED",
              "MAPPING_APPROVED",
              "TAXONOMY_REVIEW",
              "APPROVED",
              "NORMALIZED",
              "MATCHED",
              "FAILED",
              "CANCELLED",
              "BASE_READY",
              "CLEANUP_FAILED",
            ].map((value) => (
              <option key={value} value={value}>
                {statuses[value]}
              </option>
            ))}
          </select>
          <select
            aria-label="Период сессий"
            value={days}
            onChange={(event) => {
              setDays(event.target.value);
              setOffset(0);
            }}
          >
            <option value="0">За всё время</option>
            <option value="7">Последние 7 дней</option>
            <option value="30">Последние 30 дней</option>
            <option value="90">Последние 90 дней</option>
          </select>
        </div>
        {sessions.error && <ErrorBox message={sessions.error} />}
        <ScrollArea label="Закупочные сессии">
          <table>
            <thead>
              <tr>
                <th>Название</th>
                <th>Статус</th>
                <th>Обновлено</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {sessions.data?.items.map((session) => (
                <tr key={session.id}>
                  <td style={{ width: "38%" }}>{session.title}</td>
                  <td>
                    <Badge value={session.status} />
                  </td>
                  <td>
                    {new Date(
                      session.updated_at || session.created_at,
                    ).toLocaleDateString("ru-RU", {
                      day: "2-digit",
                      month: "short",
                      year: "numeric",
                    })}
                  </td>
                  <td>
                    <div className="row wrap">
                      <button
                        className="btn-ghost"
                        onClick={() => w.selectSession(session.id)}
                      >
                        Открыть →
                      </button>
                      {w.canEdit && (
                        <button
                          className="btn-danger"
                          disabled={w.busy}
                          aria-label={`Удалить сессию «${session.title}»`}
                          onClick={() => setDeletion(session)}
                        >
                          Удалить
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {sessions.loading && !sessions.data ? (
            <Loading />
          ) : (
            !sessions.data?.items.length && (
              <p className="muted" style={{ padding: 32 }}>
                Сессий по выбранным условиям нет.
              </p>
            )
          )}
        </ScrollArea>
        <Pagination
          offset={offset}
          total={sessions.data?.total || 0}
          pageSize={10}
          onChange={setOffset}
          busy={sessions.loading}
        />
      </section>
      {data?.latest_session_id && (
        <section className="notice row between">
          <div>
            <h3>Продолжите проверку: {data.latest_session_title}</h3>
            <small>
              {data.needs_review_pairs} пар требуют уточнения характеристик.
              Откройте подбор и проверьте результаты.
            </small>
          </div>
          <button
            className="btn-primary"
            onClick={() => {
              w.selectSession(data.latest_session_id!);
              w.setTab("shortlist");
            }}
          >
            Открыть подбор
          </button>
        </section>
      )}
      <footer className="page-footer">
        <span>
          Данные обновлены{" "}
          {data ? new Date(data.generated_at).toLocaleString("ru-RU") : "—"}
        </span>
        <button className="btn" onClick={refresh}>
          Обновить
        </button>
      </footer>
      {deletion && (
        <DeleteSessionDialog
          session={deletion === "all" ? undefined : deletion}
          total={
            deletion === "all" && days === "0"
              ? data?.total_sessions
              : undefined
          }
          onClose={() => setDeletion(null)}
          onDeleted={() => {
            overview.refresh();
            sessions.refresh();
          }}
        />
      )}
    </div>
  );
}
