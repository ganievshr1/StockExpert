import { ScrollArea } from "../components/ScrollArea";
import { useEffect, useState, type FormEvent } from "react";
import { useWorkspace } from "../state/Workspace";
import { api } from "../services/api";
import { useResource } from "../hooks/useResource";
import type { CandidateItem, ExpertDecision } from "../types";
import { ComparisonTable } from "../components/ComparisonTable";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  Notice,
  PageTitle,
  Pagination,
} from "../components/ui";
export function ShortlistPage() {
  const w = useWorkspace();
  const [requirement, setRequirement] = useState(""),
    [filter, setFilter] = useState("ALL"),
    [shortlisted, setShortlisted] = useState(true),
    [offset, setOffset] = useState(0),
    [selectedId, setSelectedId] = useState(""),
    [decision, setDecision] = useState<CandidateItem | null>(null),
    [using, setUsing] = useState<CandidateItem | null>(null),
    [passport, setPassport] = useState(false),
    [revision, setRevision] = useState(0);
  const requirements = useResource(
    () => api.requirements(w.sid),
    [w.sid, w.data?.job.id, w.data?.job.status],
    !!w.sid,
  );
  const rows = useResource(
    () =>
      api.candidates(
        w.sid,
        new URLSearchParams({
          status: filter,
          shortlisted: String(shortlisted),
          limit: "10",
          offset: String(offset),
          ...(requirement ? { requirement_id: requirement } : {}),
        }),
      ),
    [
      w.sid,
      filter,
      shortlisted,
      offset,
      requirement,
      revision,
      w.data?.job.id,
      w.data?.job.status,
    ],
    !!w.sid,
  );
  useEffect(() => {
    if (rows.data)
      setSelectedId((previous) =>
        rows.data!.items.some((row) => row.id === previous)
          ? previous
          : rows.data!.items[0]?.id || "",
      );
  }, [rows.data]);
  if (!w.sid) return <Empty />;
  if (!w.data) return <Loading />;
  const selected = rows.data?.items.find((row) => row.id === selectedId);
  const locked = w.busy || w.active || !w.canExpert;
  async function saveDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!decision) return;
    const values = new FormData(event.currentTarget);
    if (
      await w.act(
        () =>
          api.decide(
            w.sid,
            decision.id,
            String(values.get("decision")) as ExpertDecision,
            String(values.get("comment")),
          ),
        "Решение сохранено",
      )
    ) {
      setDecision(null);
      setRevision((value) => value + 1);
    }
  }
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow="Сопоставление"
        title="Подбор запасов"
        description="Сравните потребность со складскими позициями и зафиксируйте решение."
        actions={
          <button
            className="btn"
            disabled={w.busy}
            onClick={() =>
              void w.act(() => api.download(w.sid, "excel"), "Excel скачан")
            }
          >
            Экспорт Excel
          </button>
        }
      />
      <section className="panel stack">
        <div className="row between wrap">
          <span className="badge info">Потребность</span>
          <label className="flex-1">
            <select
              aria-label="Позиция потребности"
              value={requirement}
              onChange={(event) => {
                setRequirement(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">Все позиции потребности</option>
              {requirements.data?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.passport?.name || item.source_text}
                </option>
              ))}
            </select>
          </label>
          <small>{requirements.data?.items.length || 0} позиций</small>
        </div>
        {requirement && (
          <p className="muted small">
            {
              requirements.data?.items.find((item) => item.id === requirement)
                ?.source_text
            }
          </p>
        )}
      </section>
      {requirements.error && <ErrorBox message={requirements.error} />}
      <div className="tabs">
        {[
          ["ALL", "Все"],
          ["PASSES_KNOWN_RULES", "По правилам"],
          ["NEEDS_REVIEW", "Нужна проверка"],
          ["CONTRADICTED", "Противоречия"],
        ].map(([value, label]) => (
          <button
            key={value}
            className={filter === value ? "active" : ""}
            onClick={() => {
              setFilter(value);
              setOffset(0);
              if (value === "CONTRADICTED") setShortlisted(false);
            }}
          >
            {label}
            {filter === value ? ` · ${rows.data?.total || 0}` : ""}
          </button>
        ))}
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={shortlisted}
            onChange={(event) => {
              setShortlisted(event.target.checked);
              setOffset(0);
            }}
          />
          Только shortlist
        </label>
      </div>
      {rows.error && <ErrorBox message={rows.error} />}
      <div className="split">
        <section>
          <ScrollArea label="Кандидаты складских запасов">
            <table>
              <thead>
                <tr>
                  <th>Складской запас</th>
                  <th>Проверка</th>
                  <th>Ранг</th>
                </tr>
              </thead>
              <tbody>
                {rows.data?.items.map((row) => (
                  <tr
                    key={row.id}
                    className={selectedId === row.id ? "selected" : ""}
                  >
                    <td style={{ width: "56%" }}>
                      <button
                        className="btn-ghost"
                        style={{
                          textAlign: "left",
                          justifyContent: "flex-start",
                          padding: 0,
                        }}
                        onClick={() => setSelectedId(row.id)}
                      >
                        {row.residual.passport?.name ||
                          row.residual.source_text}
                      </button>
                      {!requirement && (
                        <small style={{ display: "block", marginTop: 8 }}>
                          Потребность:{" "}
                          {row.requirement.passport?.name ||
                            row.requirement.source_text}
                        </small>
                      )}
                      {row.residual.stock_state &&
                        row.residual.stock_state !== "AVAILABLE" && (
                          <Badge value={row.residual.stock_state} />
                        )}
                    </td>
                    <td>
                      <Badge value={row.state} />
                      {row.decision !== "PENDING" && (
                        <div style={{ marginTop: 8 }}>
                          <Badge value={row.decision} />
                        </div>
                      )}
                    </td>
                    <td>{row.rank ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.loading && !rows.data ? (
              <Loading />
            ) : (
              !rows.data?.items.length && (
                <p className="muted" style={{ padding: 28 }}>
                  Кандидатов по выбранным условиям нет. Проверьте завершение
                  подбора или измените фильтры.
                </p>
              )
            )}
          </ScrollArea>
          <Pagination
            offset={offset}
            total={rows.data?.total || 0}
            pageSize={10}
            onChange={setOffset}
            busy={rows.loading}
          />
          <p className="small muted" style={{ marginTop: 16 }}>
            Ранг — порядок подбора, а не вероятность соответствия.
          </p>
        </section>
        {selected ? (
          <aside className="panel stack comparison-panel">
            <h3>Сравнение параметров</h3>
            <p>
              {selected.residual.passport?.name ||
                selected.residual.source_text}
            </p>
            {selected.decision !== "PENDING" && (
              <div>
                <Badge value={selected.decision} />
              </div>
            )}
            <ComparisonTable
              requirement={selected.requirement}
              residual={selected.residual}
              diff={selected.diff}
            />
            <div className="source-box">
              <small>
                Источник · остатки · строка {selected.residual.row_index ?? "—"}
              </small>
              {selected.residual.source_text}
            </div>
            {selected.expert_comment && (
              <p className="small muted">
                Обоснование: {selected.expert_comment}
              </p>
            )}
            {selected.decision === "RECOMMENDED" &&
              selected.state !== "CONTRADICTED" &&
              (!selected.residual.stock_state ||
                selected.residual.stock_state === "AVAILABLE") && (
                <button
                  className="btn-primary"
                  disabled={locked}
                  onClick={() => setUsing(selected)}
                >
                  Использовать запас
                </button>
              )}
            <button
              className={
                selected.decision === "RECOMMENDED"
                  ? "btn-ghost"
                  : "btn-primary"
              }
              disabled={
                locked ||
                (!!selected.residual.stock_state &&
                  selected.residual.stock_state !== "AVAILABLE")
              }
              onClick={() => setDecision(selected)}
            >
              {selected.decision === "PENDING"
                ? "Решение эксперта"
                : "Изменить решение"}
            </button>
            <button className="btn-ghost" onClick={() => setPassport(true)}>
              Открыть паспорт
            </button>
            <details>
              <summary>Характеристики и основания подбора</summary>
              <div className="stack" style={{ marginTop: 16 }}>
                <p className="small">{selected.reason}</p>
                {selected.missing_fields.length > 0 && (
                  <small>
                    Не хватает данных: {selected.missing_fields.join(", ")}
                  </small>
                )}
                {selected.category_review && (
                  <small>
                    Категория отсутствует или отличается — требуется проверка.
                  </small>
                )}
                <small>
                  RRF: {selected.rrf_score ?? "—"} · CrossEncoder:{" "}
                  {selected.cross_encoder_score ?? "Не использовался"}
                </small>
                <small>ID пары: {selected.id}</small>
              </div>
            </details>
          </aside>
        ) : (
          <section className="panel muted">
            Выберите складскую позицию для сравнения.
          </section>
        )}
      </div>
      <Notice title="Рекомендация ещё не означает использование">
        После одобрения запас можно отдельно отметить как использованный.
        История решения сохраняется.
      </Notice>
      <div>
        <button className="btn" onClick={() => w.setTab("quality")}>
          Проверить shortlist для расчёта метрик
        </button>
      </div>
      {decision && (
        <Modal
          title="Решение эксперта"
          onClose={() => !w.busy && setDecision(null)}
        >
          <form
            onSubmit={(event) => void saveDecision(event)}
            className="stack"
          >
            <div className="source-box">
              <p>{decision.residual.source_text}</p>
              <small style={{ margin: "8px 0 0" }}>
                Потребность: {decision.requirement.source_text}
              </small>
            </div>
            <label>
              Решение
              <select
                name="decision"
                defaultValue={
                  decision.decision === "PENDING"
                    ? "NEEDS_REVIEW"
                    : decision.decision
                }
              >
                <option value="RECOMMENDED">
                  Рекомендовать к использованию
                </option>
                <option value="NEEDS_REVIEW">
                  Требуется дополнительная проверка
                </option>
                <option value="REJECTED_MANUAL">Отклонить</option>
              </select>
            </label>
            <label>
              Обоснование решения
              <textarea
                name="comment"
                rows={4}
                required
                maxLength={2000}
                defaultValue={decision.expert_comment || ""}
                placeholder="Укажите основания решения или сведения, которые необходимо уточнить"
              />
            </label>
            <Notice title="Использование — отдельное действие">
              После сохранения рекомендации подтвердите использование запаса.
              Тогда позиция будет исключена из доступной базы.
            </Notice>
            {w.error && <ErrorBox message={w.error} />}
            <div className="modal-actions">
              <button
                className="btn"
                type="button"
                onClick={() => setDecision(null)}
              >
                Отмена
              </button>
              <button className="btn-primary" disabled={locked}>
                Сохранить решение
              </button>
            </div>
          </form>
        </Modal>
      )}
      {using && (
        <Modal
          title="Подтвердить использование"
          onClose={() => !w.busy && setUsing(null)}
        >
          <div className="stack">
            <h3>{using.residual.source_text}</h3>
            <small>
              Складская позиция · строка {using.residual.row_index ?? "—"}
            </small>
            <Notice
              title="Позиция будет исключена из доступной базы"
              tone="warning"
            >
              Изменение затронет все связанные сессии. История проверки и
              решение эксперта сохранятся. Позиция используется целиком;
              частичное списание не выполняется.
            </Notice>
            {w.error && <ErrorBox message={w.error} />}
          </div>
          <div className="modal-actions">
            <button
              className="btn"
              disabled={w.busy}
              onClick={() => setUsing(null)}
            >
              Отмена
            </button>
            <button
              className="btn-primary"
              disabled={locked}
              onClick={async () => {
                if (
                  await w.act(
                    () => api.useStock(w.sid, using.id),
                    "Запас исключён из общей базы",
                  )
                ) {
                  setUsing(null);
                  setRevision((value) => value + 1);
                }
              }}
            >
              Подтвердить использование
            </button>
          </div>
        </Modal>
      )}
      {passport && selected && (
        <Modal
          title="Паспорт складской позиции"
          onClose={() => setPassport(false)}
        >
          <div className="stack">
            <p>{selected.residual.source_text}</p>
            <pre>{JSON.stringify(selected.residual.passport, null, 2)}</pre>
            {selected.residual.issues.length > 0 && (
              <Notice title="Замечания к извлечению" tone="warning">
                {selected.residual.issues.map((issue) => issue.code).join(", ")}
              </Notice>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
