import { useEffect, useRef, useState } from "react";
import { useWorkspace } from "../state/Workspace";
import { useResource } from "../hooks/useResource";
import { regexApi } from "../services/regexExperiments";
import { saveDownload } from "../services/developer";
import { ErrorBox, Pagination, percentage } from "./ui";
import { ScrollArea } from "./ScrollArea";

const statuses: Record<string, string> = {
  QUEUED: "В очереди CPU",
  RUNNING: "Расчёт регулярками",
  COMPLETED: "Завершён",
  FAILED: "Ошибка",
};
const fixed = (value: number | undefined, digits = 3) =>
  value == null ? "—" : value.toFixed(digits);

function PassportDetails({ id }: { id: string }) {
  const [offset, setOffset] = useState(0);
  const [reviewOnly, setReviewOnly] = useState(false);
  const rows = useResource(
    () =>
      reviewOnly
        ? regexApi.passports(id, offset, true)
        : regexApi.passports(id, offset),
    [id, offset, reviewOnly],
  );
  return (
    <div className="stack">
      <h3>Паспорта регулярных выражений и цитаты</h3>
      <label className="row">
        <input
          type="checkbox"
          checked={reviewOnly}
          onChange={(e) => {
            setReviewOnly(e.target.checked);
            setOffset(0);
          }}
        />
        Только строки для проверки
      </label>
      {rows.error && <ErrorBox message={rows.error} />}
      {rows.data?.items.map((row) => (
        <details className="panel" key={row.source_item_id}>
          <summary>
            {row.sheet} · строка {row.row} · {row.source_text}
          </summary>
          {row.company && (
            <p className="muted">
              {row.company} · Исходная группа: {row.source_group || "—"}
            </p>
          )}
          <ScrollArea label="Параметры и правила извлечения">
            <table>
              <thead>
                <tr>
                  <th>Параметр</th>
                  <th>Значение</th>
                  <th>Цитата</th>
                  <th>Правило</th>
                  <th>Единица</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(row.passport.attributes).map(([key, value]) => (
                  <tr key={key}>
                    <td>
                      {key === "name"
                        ? "Исходное наименование"
                        : row.passport.normalized_features?.find(
                            (f) => f.key === key,
                          )?.label || key}
                    </td>
                    <td>{value == null ? "Не определено" : String(value)}</td>
                    <td>{row.passport.evidence[key]?.quote || "—"}</td>
                    <td>
                      {row.passport.evidence[key]?.rule ||
                        "Конфликт / нет значения"}
                    </td>
                    <td>{row.passport.evidence[key]?.target_unit || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollArea>
          {row.issues.map((issue, index) => (
            <p className="muted" key={index}>
              {issue.code}
              {issue.key ? `: ${issue.key}` : ""}
              {issue.message ? ` — ${issue.message}` : ""}
              {issue.quote ? ` «${issue.quote}»` : ""}
            </p>
          ))}
          {!!row.evaluation?.errors.length && (
            <div>
              <strong>Расхождения с эталоном</strong>
              {row.evaluation.errors.map((e) => (
                <p key={e.key}>
                  {e.key}: эксперт — {JSON.stringify(e.expected)}; регулярки —{" "}
                  {JSON.stringify(e.actual)}
                </p>
              ))}
            </div>
          )}
        </details>
      ))}
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

export function RegexExperiments({
  referenceId,
  selectedSourceIds,
}: {
  referenceId: string;
  selectedSourceIds: string[];
}) {
  const w = useWorkspace();
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [detail, setDetail] = useState("");
  const [starting, setStarting] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [useReference, setUseReference] = useState(false);
  const [fileError, setFileError] = useState("");
  const uploadPending = useRef<{
    file: File;
    reference: string;
    id: string;
  } | null>(null);
  const pending = useRef<{
    source: string;
    reference: string;
    id: string;
  } | null>(null);
  const runs = useResource(
    () => regexApi.list("", offset),
    [referenceId, offset, revision],
  );
  useEffect(() => {
    setOffset(0);
    setDetail("");
    pending.current = null;
    uploadPending.current = null;
  }, [referenceId]);
  useEffect(() => {
    if (
      !runs.data?.items.some(
        (r) => r.status === "QUEUED" || r.status === "RUNNING",
      )
    )
      return;
    const timer = setInterval(() => setRevision((v) => v + 1), 3000);
    return () => clearInterval(timer);
  }, [runs.data]);
  async function start() {
    if (selectedSourceIds.length !== 1 || !referenceId || starting) return;
    const source = selectedSourceIds[0];
    if (
      !pending.current ||
      pending.current.source !== source ||
      pending.current.reference !== referenceId
    )
      pending.current = {
        source,
        reference: referenceId,
        id: crypto.randomUUID(),
      };
    const key = pending.current.id;
    setStarting(true);
    try {
      const ok = await w.act(
        () => regexApi.start(source, referenceId, key),
        "Эксперимент регулярных выражений поставлен в отдельную CPU-очередь",
      );
      if (ok) {
        pending.current = null;
        setOffset(0);
        setRevision((v) => v + 1);
      }
    } finally {
      setStarting(false);
    }
  }
  async function upload() {
    if (!file || starting || (useReference && !referenceId)) return;
    const reference = useReference ? referenceId : "";
    if (
      !uploadPending.current ||
      uploadPending.current.file !== file ||
      uploadPending.current.reference !== reference
    )
      uploadPending.current = { file, reference, id: crypto.randomUUID() };
    setStarting(true);
    try {
      const ok = await w.act(
        () =>
          regexApi.upload(
            file,
            uploadPending.current!.id,
            reference || undefined,
          ),
        "Excel принят. Запущен отдельный расчёт регулярными выражениями.",
      );
      if (ok) {
        uploadPending.current = null;
        setOffset(0);
        setRevision((v) => v + 1);
      }
    } finally {
      setStarting(false);
    }
  }
  return (
    <section
      className="panel stack"
      aria-label="Эксперимент регулярных выражений"
    >
      <div>
        <h2>Эксперимент H1: регулярные выражения</h2>
        <p>
          Правила v2 поддерживают форматы файла «Складские позиции 12000»: марки
          оборудования, кабельные группы, явные единицы, диапазоны сигналов и
          размеры. Нерасшифрованные коды остаются в обозначении и помечаются для
          проверки.
        </p>
      </div>
      <div className="stack">
        <h3>Проверить складской Excel без LLM</h3>
        <p>
          Для файла с колонками A «Предприятие», B «КИП и средства
          автоматизации», C «Система». Извлекаются характеристики только из C; A
          и B сохраняются рядом с результатом. Предварительно создавать сессию
          не нужно.
        </p>
        <div className="row wrap">
          <input
            type="file"
            accept=".xlsx"
            aria-label="Складской Excel для регулярок"
            disabled={starting}
            onChange={(e) => {
              const next = e.target.files?.[0] || null;
              setFileError(
                next && next.size > 20 * 1024 * 1024
                  ? "Файл должен быть не больше 20 МБ"
                  : "",
              );
              setFile(next);
              uploadPending.current = null;
            }}
          />
          <button
            className="btn-primary"
            disabled={
              w.busy ||
              starting ||
              !file ||
              !!fileError ||
              (useReference && !referenceId)
            }
            onClick={() => void upload()}
          >
            Обработать Excel регулярками
          </button>
        </div>
        {fileError && <ErrorBox message={fileError} />}
        <label className="row">
          <input
            type="checkbox"
            checked={useReference}
            disabled={!referenceId || starting}
            onChange={(e) => setUseReference(e.target.checked)}
          />
          Сравнить с выбранным выше эталоном эксперта
        </label>
        <p className="muted">
          Без эталона сохраняются паспорта, охват правил и время. Точность,
          полнота и F1 не вычисляются. Для файла, по которому разработаны
          правила, результат относится к данным разработки.
        </p>
      </div>
      <div>
        <h3>Обработать исходные строки сохранённой сессии</h3>
        <p>
          Выберите выше одну сессию с этой базой. Правила обработают её исходные
          строки и сравнят новые паспорта с выбранным эталоном. Прежние
          паспорта, модели и расчёты сохраняются.
        </p>
        <p className="muted">
          Только CPU, без LLM. Сопоставление столбцов берётся из исходной
          сессии. Таксономия H2 и поиск H3 в этом запуске не выполняются.
        </p>
      </div>
      <div className="row wrap">
        <button
          className="btn-primary"
          disabled={
            w.busy || starting || !referenceId || selectedSourceIds.length !== 1
          }
          onClick={() => void start()}
        >
          {starting ? "Создание эксперимента…" : "Рассчитать только регулярки"}
        </button>
        <button className="btn" onClick={() => setRevision((v) => v + 1)}>
          Обновить эксперименты
        </button>
        {selectedSourceIds.length !== 1 && (
          <span className="muted">Нужна ровно одна исходная сессия.</span>
        )}
      </div>
      {runs.error && <ErrorBox message={runs.error} />}
      <ScrollArea label="Результаты регулярных выражений">
        <table>
          <thead>
            <tr>
              <th>Запуск / версия правил</th>
              <th>Статус</th>
              <th>Строки</th>
              <th>Охват правил</th>
              <th>Точность P</th>
              <th>Полнота R</th>
              <th>micro-F1</th>
              <th>Полнота строк</th>
              <th>Сохранённый F1 исходной сессии</th>
              <th>Время правил, с</th>
              <th>Результат</th>
            </tr>
          </thead>
          <tbody>
            {runs.data?.items.map((run) => (
              <tr key={run.id}>
                <td>
                  {run.source_title}
                  <small className="block">
                    {run.rules_version} · {run.id.slice(0, 8)}
                  </small>
                  <small className="block">
                    {new Date(run.created_at).toLocaleString("ru-RU")}
                  </small>
                </td>
                <td>
                  {statuses[run.status] || run.status}
                  {run.error && <p className="danger-text">{run.error}</p>}
                  {run.result.evaluation_status === "NO_REFERENCE" && (
                    <small className="block">Без экспертной оценки</small>
                  )}
                  {run.result.is_development_dataset && (
                    <small className="block">Данные разработки правил</small>
                  )}
                </td>
                <td>
                  {run.processed_rows} / {run.row_count}
                </td>
                <td>
                  {run.result.extraction ? (
                    <>
                      С характеристиками:{" "}
                      {run.result.extraction.rows_with_parameters} /{" "}
                      {run.result.extraction.rows}
                      <small className="block">
                        Только обозначения:{" "}
                        {run.result.extraction.model_only_rows}
                      </small>
                      <small className="block">
                        Для проверки: {run.result.extraction.review_rows}
                      </small>
                      <small className="block">Охват не равен точности</small>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td>{percentage(run.result.metrics?.h1_precision)}</td>
                <td>{percentage(run.result.metrics?.h1_recall)}</td>
                <td>{percentage(run.result.metrics?.h1_f1)}</td>
                <td>{percentage(run.result.metrics?.full_row_recall)}</td>
                <td>
                  {percentage(run.baseline.metrics?.h1_f1)}
                  <small className="block">
                    {run.baseline.models?.map((m) => m.model).join(", ") ||
                      "Нет завершённого сравнения"}
                  </small>
                </td>
                <td>
                  {fixed(run.result.timing?.normalization_seconds)}
                  <small className="block">
                    p95 строки: {fixed(run.result.timing?.p95_row_ms)} мс
                  </small>
                </td>
                <td>
                  <button
                    className="link-button"
                    disabled={run.status !== "COMPLETED"}
                    onClick={() => setDetail(detail === run.id ? "" : run.id)}
                  >
                    Паспорта и ошибки
                  </button>
                  <button
                    className="link-button"
                    disabled={run.status !== "COMPLETED"}
                    onClick={() =>
                      void w.act(() =>
                        saveDownload(
                          `/developer/regex-experiments/${run.id}/export?format=passports_csv`,
                          `regex-passports-${run.id}.csv`,
                        ),
                      )
                    }
                  >
                    Паспорта CSV
                  </button>
                  <button
                    className="link-button"
                    onClick={() =>
                      void w.act(() =>
                        saveDownload(
                          `/developer/regex-experiments/${run.id}/export?format=json`,
                          `regex-${run.id}.json`,
                        ),
                      )
                    }
                  >
                    JSON
                  </button>
                  <button
                    className="link-button"
                    onClick={() =>
                      void w.act(() =>
                        saveDownload(
                          `/developer/regex-experiments/${run.id}/export?format=csv`,
                          `regex-${run.id}.csv`,
                        ),
                      )
                    }
                  >
                    CSV
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollArea>
      {!runs.loading && !runs.data?.total && (
        <p className="muted">
          Экспериментов пока нет. Каждый новый запуск сохраняется отдельной
          записью.
        </p>
      )}
      <Pagination
        offset={offset}
        total={runs.data?.total || 0}
        pageSize={20}
        onChange={setOffset}
        busy={runs.loading}
      />
      <p className="muted">
        F1 = 2TP / (2TP + FP + FN). Полнота строк — доля строк, где найдены все
        размеченные параметры. Время правил исключает чтение Excel, очередь и
        сравнение с эталоном; историческое время LLM напрямую с ним не
        сопоставляйте.
      </p>
      <p className="muted">
        Если запуск долго остаётся в очереди, проверьте сервисы beat и
        worker-regex.
      </p>
      {detail && <PassportDetails key={detail} id={detail} />}
    </section>
  );
}
