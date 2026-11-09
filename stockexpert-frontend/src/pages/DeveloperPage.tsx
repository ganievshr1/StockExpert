import {QualityPage} from './QualityPage';
import {LabDashboard} from '../components/LabDashboard';
import {HypothesisComparison} from '../components/HypothesisComparison';
import { B1ExperimentPanel } from '../components/B1ExperimentPanel';
import { useEffect, useState } from "react";
import { useWorkspace } from "../state/Workspace";
import {
  developerApi,
  saveDownload,
  type Evaluation,
} from "../services/developer";
import { useResource } from "../hooks/useResource";
import {
  PageTitle,
  ErrorBox,
  Loading,
  Notice,
  Pagination,
  percentage,
} from "../components/ui";
import { ScrollArea } from "../components/ScrollArea";
import { RegexExperiments } from "../components/RegexExperiments";
import {
  HypothesisMetrics,
  MetricCalculation,
} from "../components/HypothesisMetrics";

const metrics = [
  ["h1_precision", "H1 Precision"],
  ["h1_recall", "H1 Recall"],
  ["h1_f1", "H1 F1"],
  ["name_accuracy", "Верные названия"],
  ["h2_l1_accuracy", "H2 Accuracy L1"],
  ["h2_leaf_accuracy", "H2 Accuracy конечных категорий"],
  ["h2_l3_accuracy", "H2 Accuracy L3"],
  ["h3_recall_retrieval", "H3 Recall поиска"],
  ["h3_recall_at_1", "Recall@1"],
  ["h3_recall_at_5", "Recall@5"],
  ["h3_recall_at_10", "Recall@10"],
  ["h3_recall_at_150", "H3 Recall@150"],
  ["h3_precision_shortlist", "Precision shortlist"],
  ["h3_mrr", "MRR"],
  ["h3_ndcg10", "nDCG@10"],
];
const status: Record<string, string> = {
  READY: "Рассчитано",
  PARTIAL: "Частично",
  WAITING: "Ожидает обработки",
  ERROR: "Ошибка оценки",
  INCOMPATIBLE: "База изменилась",
};

function EvaluationDetails({ id }: { id: string }) {
  const data = useResource(() => developerApi.detail(id), [id]);
  const [onlyErrors, setOnlyErrors] = useState(true);
  const [offset, setOffset] = useState(0);
  if (data.error) return <ErrorBox message={data.error} />;
  if (!data.data) return <Loading />;
  const result = data.data.result;
  const rows = (result.rows || []).filter(
    (row) => !onlyErrors || row.missing || row.errors.length,
  );
  return (
    <div className="panel stack">
      <h2>Расшифровка сравнения с экспертом</h2>
      {result.hypotheses && (
        <HypothesisMetrics
          metrics={result.hypotheses}
          population={result.h3_population}
        />
      )}
      {!!result.h3_queries?.length && (
        <ScrollArea label="H3 по каждой потребности">
          <table>
            <thead>
              <tr>
                <th>Потребность</th>
                <th>Все строки</th>
                <th>Проверено</th>
                <th>Подходящих во всей базе</th>
                <th>Найдено в Top 150</th>
                <th>Пропущено подходящих</th>
                <th>Recall@150</th>
              </tr>
            </thead>
            <tbody>
              {result.h3_queries.map((row) => (
                <tr key={row.requirement_id}>
                  <td>{row.requirement_id}</td>
                  <td>{row.catalog_rows}</td>
                  <td>{row.pairs_reviewed}</td>
                  <td>{row.relevant}</td>
                  <td>{row.hits_150}</td>
                  <td>{row.missed_relevant}</td>
                  <td>{percentage(row.recall_at_150)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollArea>
      )}
      <details>
        <summary>Дополнительные метрики и время</summary>
        <ScrollArea label="Дополнительные метрики">
          <table>
            <tbody>
              {metrics.map(([key, label]) => (
                <tr key={key}>
                  <th>{label}</th>
                  <td>{percentage(result.metrics?.[key])}</td>
                </tr>
              ))}
              {Object.entries(result.timing || {}).map(([key, value]) => (
                <tr key={key}>
                  <th>{key}</th>
                  <td>{value ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollArea>
      </details>
      <p>
        TP: {result.coverage?.tp ?? "—"} · FP: {result.coverage?.fp ?? "—"} ·
        FN: {result.coverage?.fn ?? "—"}
      </p>
      {(result.notes || []).map((note) => (
        <p className="muted" key={note}>
          {note}
        </p>
      ))}
      <button
        className="btn"
        onClick={() =>
          void saveDownload(
            `/developer/evaluations/${id}`,
            `expert-evaluation-${id}.json`,
          ).catch((reason) => alert(reason.message))
        }
      >
        Скачать полный результат и ошибки (JSON)
      </button>
      <details>
        <summary>Метрики отдельных параметров</summary>
        <ScrollArea label="F1 отдельных параметров">
          <table>
            <thead>
              <tr>
                <th>Параметр</th>
                <th>TP</th>
                <th>FP</th>
                <th>FN</th>
                <th>F1</th>
              </tr>
            </thead>
            <tbody>
              {result.fields?.map((f) => (
                <tr key={f.key}>
                  <td>{f.key}</td>
                  <td>{f.tp || 0}</td>
                  <td>{f.fp || 0}</td>
                  <td>{f.fn || 0}</td>
                  <td>{percentage(f.f1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollArea>
      </details>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={onlyErrors}
          onChange={(e) => {
            setOnlyErrors(e.target.checked);
            setOffset(0);
          }}
        />
        Только пропуски и ошибки параметров
      </label>
      <ScrollArea label="Сравнение всех паспортов с эталоном">
        <table>
          <thead>
            <tr>
              <th>Источник</th>
              <th>Паспорт эксперта</th>
              <th>Категория эксперта</th>
              <th>Расхождения параметров</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(offset, offset + 20).map((row) => (
              <tr key={row.id}>
                <td>
                  {row.sheet} · {row.row}
                </td>
                <td>
                  {row.name}
                  {row.missing && (
                    <strong className="danger-text"> · нет паспорта</strong>
                  )}
                </td>
                <td>{row.expected_category.join(" / ") || "Не определена"}</td>
                <td>
                  {row.errors.length
                    ? row.errors.map((e) => (
                        <div key={e.key}>
                          <strong>{e.key}</strong>: эксперт{" "}
                          {JSON.stringify(e.expected)}; модель{" "}
                          {JSON.stringify(e.actual)}
                        </div>
                      ))
                    : "Нет"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollArea>
      <Pagination
        offset={offset}
        total={rows.length}
        pageSize={20}
        onChange={setOffset}
      />
    </div>
  );
}

function AutomaticDeveloperPanel() {
  const w = useWorkspace();
  const [referenceId, setReferenceId] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [file, setFile] = useState<File | null>(null);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [detail, setDetail] = useState("");
  const allowed = w.user?.role === "admin";
  const references = useResource(developerApi.references, [revision], allowed);
  const sessions = useResource(
    () => developerApi.sessions(referenceId, offset),
    [referenceId, offset, revision],
    allowed && !!referenceId,
  );
  const results = useResource(
    () => developerApi.results(referenceId),
    [referenceId, revision],
    allowed && !!referenceId,
  );
  useEffect(() => {
    if (!referenceId && references.data?.length)
      setReferenceId(references.data[0].id);
  }, [references.data, referenceId]);
  useEffect(() => {
    if (!results.data?.some((row) => row.status === "WAITING")) return;
    const timer = setInterval(() => setRevision((value) => value + 1), 5000);
    return () => clearInterval(timer);
  }, [results.data]);
  if (!allowed)
    return <ErrorBox message="Раздел доступен только администратору" />;
  const reference = references.data?.find((r) => r.id === referenceId);
  async function upload() {
    if (!file) return;
    await w.act(async () => {
      const result = await developerApi.upload(file);
      setReferenceId(result.id);
      setSelected(new Set());
      setOffset(0);
      setDetail("");
      setFile(null);
      setRevision((v) => v + 1);
    }, "Эталон загружен");
  }
  function choose(id: string, checked: boolean) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  async function evaluate() {
    if (
      await w.act(
        () => developerApi.evaluate(referenceId, [...selected]),
        "Расчёт метрик выполнен; активные сессии ожидают завершения",
      )
    )
      setRevision((v) => v + 1);
  }
  function modelName(row: Evaluation) {
    return (
      row.result.models?.map((m) => `${m.model} (${m.passports})`).join(", ") ||
      row.result.selected_model?.model ||
      "Ещё не определена"
    );
  }
  return (
    <div className="section-gap developer-page">
      <PageTitle
        eyebrow="Аналитика и контроль"
        title="Эталон и автоматическое сравнение"
        description="Excel-эталон и независимые оценки H1, H2, H3 для сохранённых расчётов."
      />
      <section className="panel stack">
        <h2>Загрузить исходные проверенные данные эксперта</h2>
        <p>
          Загрузите один файл JSON или XLSX. Исправленный эталон сохранится как
          отдельная версия. Ручные оценки в сессиях сохраняются отдельно.
        </p>
        <div className="row wrap">
          <input
            aria-label="Файл экспертных данных"
            type="file"
            accept=".json,.xlsx"
            disabled={w.busy}
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <button
            className="btn-primary"
            disabled={!file || w.busy}
            onClick={() => void upload()}
          >
            Загрузить эталон
          </button>
        </div>
        {references.error && <ErrorBox message={references.error} />}
        <label>
          Экспертный эталон
          <select
            aria-label="Экспертный эталон"
            value={referenceId}
            onChange={(e) => {
              setReferenceId(e.target.value);
              setSelected(new Set());
              setOffset(0);
              setDetail("");
            }}
          >
            <option value="">Выберите эталон</option>
            {references.data?.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title} · {r.version}
              </option>
            ))}
          </select>
        </label>
        {reference && (
          <>
            <small>
              {reference.passport_count} паспортов · {reference.pair_count} пар
              для H3 · версия {reference.version}
            </small>
            <details>
              <summary>Условия разметки</summary>
              {reference.notes.map((n) => (
                <p key={n}>{n}</p>
              ))}
            </details>
          </>
        )}
      </section>
      {referenceId && (
        <>
          <section className="panel stack">
            <h2>Сессии с этой базой</h2>
            <p>
              Совпадение проверяется по содержимому Excel. Для H3 должен
              совпадать и опросный лист. Недостающие строки базы будут показаны
              в покрытии.
            </p>
            {sessions.error && <ErrorBox message={sessions.error} />}
            <div className="row wrap">
              <button
                className="btn"
                disabled={!sessions.data?.items.length}
                onClick={() =>
                  setSelected(
                    new Set([
                      ...selected,
                      ...(sessions.data?.items || []).map((s) => s.id),
                    ]),
                  )
                }
              >
                Выбрать эту страницу
              </button>
              <button className="btn" onClick={() => setSelected(new Set())}>
                Снять выбор
              </button>
              <button className="btn" onClick={() => setRevision((v) => v + 1)}>
                Обновить
              </button>
              <span>Выбрано: {selected.size}</span>
            </div>
            <ScrollArea label="Выбор сессий для сравнения">
              <table>
                <thead>
                  <tr>
                    <th>Выбор</th>
                    <th>Сессия</th>
                    <th>H1 / модель H2</th>
                    <th>Статус</th>
                    <th>Паспорта базы</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.data?.items.map((s) => (
                    <tr key={s.id}>
                      <td>
                        <input
                          aria-label={`Выбрать ${s.title}`}
                          type="checkbox"
                          checked={selected.has(s.id)}
                          onChange={(e) => choose(s.id, e.target.checked)}
                        />
                      </td>
                      <td>
                        {s.title}
                        <small className="block">{s.id.slice(0, 8)}</small>
                      </td>
                      <td>
                        {s.normalization_method === "regex"
                          ? "H1: регулярки"
                          : "H1: LLM"}
                        <small className="block">
                          H2: {s.llm_config?.model || "Старая конфигурация"}
                        </small>
                      </td>
                      <td>{s.status}</td>
                      <td>
                        {s.base_snapshot?.reused_passports
                          ? "Повторно использованы"
                          : "Собственный расчёт"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollArea>
            {!sessions.loading && sessions.data?.total === 0 && (
              <p className="muted">Нет сессий с таким исходным Excel.</p>
            )}
            <Pagination
              offset={offset}
              total={sessions.data?.total || 0}
              pageSize={100}
              onChange={setOffset}
              busy={sessions.loading}
            />
            <button
              className="btn-primary"
              disabled={w.busy || selected.size === 0 || selected.size > 500}
              onClick={() => void evaluate()}
            >
              Рассчитать метрики выбранных сессий
            </button>
          </section>
          <section className="panel stack">
            <div className="row between wrap">
              <h2>Сравнение с экспертом</h2>
              <button
                className="btn"
                disabled={!results.data?.length || w.busy}
                onClick={() =>
                  void w.act(() =>
                    saveDownload(
                      `/developer/references/${referenceId}/export`,
                      "expert-model-comparison.csv",
                    ),
                  )
                }
              >
                Скачать таблицу CSV
              </button>
            </div>
            <p className="muted">
              Модель указана по происхождению сохранённых паспортов. Пустая
              метрика означает отсутствие данных, а не нулевое качество. Таблицу
              можно прокручивать по горизонтали.
            </p>
            {results.error && <ErrorBox message={results.error} />}
            <ScrollArea label="Три метрики моделей">
              <table className="developer-results">
                <thead>
                  <tr>
                    <th>Сессия / модель</th>
                    <th>Оценка и охват</th>
                    <th>H1 · F1 выбранного эталона</th>
                    <th>H2 · Accuracy конечного уровня</th>
                    <th>H3 · Recall@150 всей базы</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {results.data?.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <strong>{row.session_title}</strong>
                        <small className="block">{modelName(row)}</small>
                      </td>
                      <td>
                        {status[row.status] || row.status}
                        {row.error && (
                          <small className="block">{row.error}</small>
                        )}
                        <small className="block">
                          Паспорта:{" "}
                          {row.result.coverage
                            ? `${row.result.coverage.normalized_stock}/${row.result.coverage.expected_stock}${row.result.coverage.extra_stock_ids?.length ? ` (+${row.result.coverage.extra_stock_ids.length} вне эталона)` : ""}`
                            : "—"}
                        </small>
                      </td>
                      {["H1", "H2", "H3"].map((id) => (
                        <td key={id}>
                          {row.result.hypotheses?.find(
                            (metric) => metric.id === id,
                          ) ? (
                            <MetricCalculation
                              metric={row.result.hypotheses.find(
                                (metric) => metric.id === id,
                              )!}
                            />
                          ) : (
                            <small>
                              {row.result.note ||
                                "Выберите сессию и нажмите «Рассчитать метрики выбранных сессий», чтобы получить формулы с числами."}
                            </small>
                          )}
                        </td>
                      ))}
                      <td>
                        <button
                          className="link-button"
                          onClick={() =>
                            setDetail(detail === row.id ? "" : row.id)
                          }
                        >
                          Расчёт и ошибки
                        </button>
                        <button
                          className="link-button"
                          onClick={() => {
                            w.selectSession(row.session_id);
                            w.setTab("metrics");
                          }}
                        >
                          Открыть метрики
                        </button>
                        <button
                          className="link-button"
                          onClick={() => {
                            w.selectSession(row.session_id);
                            w.setTab("quality");
                          }}
                        >
                          Ручная проверка
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollArea>
            {!results.data?.length && (
              <p>
                Выберите сессии и запустите расчёт метрик. Существующие
                результаты моделей останутся без изменений.
              </p>
            )}
          </section>
          {detail && <EvaluationDetails key={detail} id={detail} />}
          <Notice title="Как читать H2 и H3" tone="warning">
            Основной показатель H2 — Accuracy конечной категории. Accuracy L3 доступна дополнительно, если в дереве три уровня. H3 проверяет все строки
            базы, включая строки вне выдачи. Знаменатель Recall — все подходящие
            позиции, а доля отбора считается от всех строк. На базе из 98 строк
            результат не подтверждает эксперимент на 12 000 строках.
          </Notice>
        </>
      )}
      <RegexExperiments
        referenceId={referenceId}
        selectedSourceIds={[...selected]}
      />
    </div>
  );
}


export function DeveloperPage() {
 const w=useWorkspace(); const [source,setSource]=useState<"manual"|"automatic"|"b1">("automatic"); const [revision,setRevision]=useState(0);
 if(w.user?.role!=="admin") return <ErrorBox message="Раздел доступен только администратору"/>;
 return <div className="section-gap"><PageTitle eyebrow="Эксперименты StockExpert" title="Для разработчика" description="Ручная и автоматическая проверка исходных данных, отчёты и сравнение H1, H2, H3."/>
 <div className="tabs" role="tablist" aria-label="Режим проверки метрик"><button role="tab" aria-selected={source==='manual'} className={source==='manual'?'active':''} onClick={()=>setSource('manual')}>Ручная проверка метрик</button><button role="tab" aria-selected={source==='automatic'} className={source==='automatic'?'active':''} onClick={()=>setSource('automatic')}>Автоматическая проверка метрик</button><button role="tab" aria-selected={source==='b1'} className={source==='b1'?'active':''} onClick={()=>setSource('b1')}>Проверка гипотезы B1</button></div>
 {source==='b1'?<B1ExperimentPanel/>:<>
 <label>Сессия для проверки и лабораторных методов<select aria-label="Сессия разработчика" value={w.sid||''} onChange={e=>{w.selectSession(e.target.value);w.setTab('developer');}}><option value="">Выберите сессию</option>{(w.sessions||[]).map(s=><option key={s.id} value={s.id}>{s.title}</option>)}</select></label>
 {source==='automatic'?<AutomaticDeveloperPanel/>:<><Notice title="Проверка исходных данных">H1: сверяйте значения, единицы и цитаты. H2: проверьте конечную категорию. H3: оцените все пары «потребность–позиция», включая позиции вне выдачи. Ответы сохраняются по выбранному запуску.</Notice>{w.sid?<QualityPage key={w.sid} initialMode="parameters" onReviewed={()=>setRevision(v=>v+1)}/>:<p>Выберите сессию с завершённым расчётом.</p>}</>}
 {w.sid&&<LabDashboard key={`${source}-${w.sid}`} source={source} refreshKey={revision}/>}
 <HypothesisComparison source={source} refreshKey={revision}/>
 </>}
 </div>;
}
