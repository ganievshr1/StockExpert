import { ScrollArea } from "../components/ScrollArea";
import { useEffect, useState, useRef } from "react";
import { useWorkspace } from "../state/Workspace";
import {
  qualityApi,
  type Answer,
  type QualityTarget,
} from "../services/quality";
import { labApi } from "../services/laboratory";
import type { SchemaDraft } from "../types";
import { materialFeatures } from "../services/taxonomy";
import { useDebouncedValue, useResource } from "../hooks/useResource";
import { ComparisonTable } from "../components/ComparisonTable";
import {
  Empty,
  ErrorBox,
  Loading,
  Notice,
  PageTitle,
  Pagination,
  percentage,
} from "../components/ui";

function reviewedInMode(row: QualityTarget, mode: "text" | "parameters" | "category") {
  const answer=row.review?.answer;
  return mode === "text" ? Object.hasOwn(answer?.attributes || {}, "name") : mode === "category" ? !!answer?.category_reviewed : Object.keys(answer?.attributes || {}).some(key=>key!=="name");
}
function PassportReview({
  row,
  disabled,
  onSave,
  onSkip,
  schema,
  mode,
}: {
  mode: "text" | "parameters" | "category";
  schema?: SchemaDraft;
  row: QualityTarget;
  disabled: boolean;
  onSave: (answer: Answer) => Promise<void>;
  onSkip: () => void;
}) {
  const w = useWorkspace();
  const [showAllFields, setShowAllFields] = useState(false);
  const configured = materialFeatures(
    schema || w.data?.session.schema_doc,
    row.target.passport?.category_id,
  );
  const categoryFeatures = configured.length ? configured : row.target.passport?.normalized_features || [];
  const allFeatures = (schema?.features?.length ? schema.features : categoryFeatures);
  const textOnly = mode === "text";
  const categoryOnly = mode === "category";
  const old = row.review?.answer;
  const [custom,setCustom]=useState<NonNullable<Answer['custom_features']>>(old?.custom_features||[]);
  const [newKey,setNewKey]=useState(''),[newLabel,setNewLabel]=useState(''),[newUnit,setNewUnit]=useState('none');
  const [reviewComment,setReviewComment]=useState(old?.comment||'');
  const [expectedCategory,setExpectedCategory]=useState(old?.expected_category||'');
  const features = textOnly ? [{key:"name",label:"Текст наименования",kind:"string",unit:"none"}] : categoryOnly ? [] : [...(showAllFields ? allFeatures : categoryFeatures).filter(f => f.key !== "name"),...custom.map(f=>({...f,kind:f.unit==='none'?'string':'number'}))];
  const [checked, setChecked] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(
      Object.keys(old?.attributes || {}).map(key => [key, true]),
    ),
  );
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries({ ...(row.target.passport?.attributes || {}), name: row.target.passport?.name ?? row.target.passport?.attributes?.name, ...(old?.attributes || {}) })
        .map(([key, value]) => [key, String(value ?? "")]),
    ),
  );
  const [categoryChecked, setCategoryChecked] = useState(
      old?.category_reviewed || false,
    ),
    [category, setCategory] = useState(
      old?.category_reviewed
        ? old.category_id || ""
        : row.target.passport?.category_id || "",
    ),
    [error, setError] = useState("");
  async function save() {
    try {
      const attributes: Record<string, string | number | null> = {};
      for (const feature of features) {
        if (!checked[feature.key]) continue;
        const raw = (values[feature.key] || "").trim();
        const value =
          raw === ""
            ? null
            : feature.kind === "string"
              ? raw
              : Number(raw.replace(",", "."));
        if (
          typeof value === "number" &&
          (!Number.isFinite(value) ||
            (feature.kind === "integer" && !Number.isInteger(value)))
        )
          throw Error("Введите корректное число для " + feature.label);
        attributes[feature.key] = value;
      }
      if (!Object.keys(attributes).length && !(categoryOnly && categoryChecked))
        throw Error("Отметьте проверенные параметры или категорию");
      setError("");
      await onSave({
        attributes: {...(old?.attributes || {}), ...attributes},
        ...(custom.length ? {custom_features: custom} : {}),
        ...(reviewComment ? {comment: reviewComment} : {}),
        category_reviewed: categoryOnly ? categoryChecked : old?.category_reviewed || false,
        category_id: categoryOnly ? (categoryChecked ? category || null : null) : old?.category_id || null,
        ...(expectedCategory || old?.expected_category ? {expected_category: categoryOnly ? expectedCategory||null : old?.expected_category||null} : {}),
      });
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  return (
    <div className="stack">
      <div className="row between">
        <h2>{textOnly ? "H1 · Проверьте текст наименования" : categoryOnly ? "H2 · Проверьте категорию" : "Проверьте технические параметры"}</h2>
        <span className={`badge ${reviewedInMode(row, mode) ? "success" : "warning"}`}>
          {reviewedInMode(row, mode) ? "Проверено" : "Не проверено"}
        </span>
      </div>
      <div className="source-box">
        <small>
          Источник ·{" "}
          {row.target.kind === "requirement" ? "опросный лист" : "остатки"} ·{" "}
          {row.target.sheet || "без листа"} · строка{" "}
          {row.target.row_index ?? "—"}
        </small>
        {row.target.source_text}
      </div>
      <details><summary>Исходные ячейки и полный паспорт</summary><pre className="passport-json">{JSON.stringify({ source: row.target.raw, passport: row.target.passport }, null, 2)}</pre></details>
      <label>Комментарий и цитаты из исходника<textarea value={reviewComment} onChange={e=>setReviewComment(e.target.value)} maxLength={2000} rows={2} disabled={disabled}/></label>
      {mode === "parameters" && <label className="checkbox-label"><input type="checkbox" checked={showAllFields} onChange={e => setShowAllFields(e.target.checked)} />Все параметры схемы, включая отсутствующие в категории</label>}
      {mode === "parameters" && <details><summary>Добавить параметр, пропущенный моделью</summary><div className="stack"><label>Ключ параметра<input value={newKey} onChange={e=>setNewKey(e.target.value)} placeholder="power_kw"/></label><label>Название<input value={newLabel} onChange={e=>setNewLabel(e.target.value)} placeholder="Мощность"/></label><label>Единица<input value={newUnit} onChange={e=>setNewUnit(e.target.value)} placeholder="kW или none"/></label><button className="btn" disabled={disabled} onClick={()=>{if(!/^[a-z][a-z0-9_]{0,63}$/.test(newKey)||!newLabel.trim()||features.some(f=>f.key===newKey)){setError('Введите уникальный английский ключ и название параметра');return;}setCustom(v=>[...v,{key:newKey,label:newLabel.trim(),unit:newUnit||'none',aliases:[]}]);setChecked(v=>({...v,[newKey]:true}));setNewKey('');setNewLabel('');setError('');}}>Добавить поле</button><small>Укажите значение и цитату в комментарии. Отсутствие предсказания будет учтено как FN.</small></div></details>}
      {textOnly && <p>Одна позиция — одно сравнение текста. Категории H2 и технические параметры не участвуют. Сравниваются все символы, кроме пробелов по краям.</p>}
      {!categoryOnly && <button className="btn" disabled={disabled} onClick={() => setChecked(previous => ({ ...previous, ...Object.fromEntries(features.map(f => [f.key, true])) }))}>{textOnly ? "Текст проверен" : "Отметить показанные параметры проверенными"}</button>}
      {categoryOnly && <><label>
        Конечная категория, если её нет в списке
        <input value={expectedCategory} disabled={disabled} placeholder="Введите правильную категорию" onChange={e=>{setExpectedCategory(e.target.value);setCategoryChecked(true);}}/>
        <small>Текст имеет приоритет над списком. Отсутствующая или другая категория модели считается ошибкой H2.</small>
      </label><label>
        Правильная категория
        <select
          value={category}
          disabled={disabled}
          onChange={(event) => {
            setCategory(event.target.value);
            setCategoryChecked(true);
          }}
        >
          <option value="">Нет определённой категории — вне Accuracy</option>
          {(schema || w.data?.session.schema_doc)?.categories?.map((value) => (
            <option value={value.id} key={value.id}>
              {value.name}
            </option>
          ))}
        </select>
      </label>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={categoryChecked}
          disabled={disabled}
          onChange={(event) => setCategoryChecked(event.target.checked)}
        />
        Категория проверена
      </label></>}
      {!categoryOnly && <ScrollArea label="Проверка параметров паспорта" className="review-table">
        <table>
          <thead>
            <tr>
              <th>Параметр</th>
              <th>Ответ модели</th>
              <th>Верное значение</th>
              <th>Оценка</th>
            </tr>
          </thead>
          <tbody>
            {features.map((feature) => {
              const actual = feature.key === "name" ? (row.target.passport?.name ?? row.target.passport?.attributes?.name) : row.target.passport?.attributes[feature.key];
              const expected = values[feature.key] || "";
              const comparable =
                expected === ""
                  ? null
                  : feature.kind === "string"
                    ? expected.trim()
                    : Number(expected.replace(",", "."));
              const same = (feature.key === "name" && typeof actual === "string" ? actual.trim() : actual ?? null) === comparable;
              return (
                <tr key={feature.key}>
                  <td>
                    {feature.label}
                    {feature.unit === "none" ? "" : `, ${feature.unit}`}
                  </td>
                  <td>
                    {actual ?? "Нет данных"}
                    {row.target.passport?.evidence?.[feature.key]?.quote && (
                      <small style={{ display: "block", marginTop: 8 }}>
                        «{row.target.passport.evidence?.[feature.key]?.quote}»
                      </small>
                    )}
                  </td>
                  <td>
                    <input
                      aria-label={`Правильное значение ${feature.label}`}
                      value={expected || ""}
                      disabled={disabled}
                      onChange={(event) => {
                        setValues({
                          ...values,
                          [feature.key]: event.target.value,
                        });
                        setChecked({ ...checked, [feature.key]: true });
                      }}
                    />
                    <button
                      className="link-button"
                      disabled={disabled}
                      style={{ marginTop: 8 }}
                      onClick={() => {
                        setValues({ ...values, [feature.key]: "" });
                        setChecked({ ...checked, [feature.key]: true });
                      }}
                    >
                      Нет данных
                    </button>
                  </td>
                  <td>
                    <label className="checkbox-label">
                      <input
                        aria-label={`Проверен ${feature.label}`}
                        type="checkbox"
                        checked={checked[feature.key] || false}
                        disabled={disabled}
                        onChange={(event) =>
                          setChecked({
                            ...checked,
                            [feature.key]: event.target.checked,
                          })
                        }
                      />
                      <span
                        className={`badge ${checked[feature.key] ? (same ? "success" : "warning") : ""}`}
                      >
                        {checked[feature.key]
                          ? same
                            ? "Верно"
                            : "Исправлено"
                          : "Не проверено"}
                      </span>
                    </label>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollArea>}
      {!categoryOnly && <small>
        Проверьте каждый отмеченный параметр по источнику. «Нет данных» означает
        отсутствие значения в источнике. Непроверенные поля не участвуют в F1.
      </small>}
      {mode === "parameters" && features.length === 0 && (
        <Notice title="В схеме доступно только наименование" tone="warning">
          Размеры и другие свойства не оцениваются отдельно. Чтобы проверять их,
          добавьте признаки в классификатор до утверждения схемы новой сессии.
        </Notice>
      )}
      {error && <ErrorBox message={error} />}
      <div className="row between">
        <button className="btn-ghost" disabled={disabled} onClick={onSkip}>
          Пропустить
        </button>
        <button
          className="btn-primary"
          disabled={disabled}
          onClick={() => void save()}
        >
          Сохранить и далее
        </button>
      </div>
    </div>
  );
}
function CandidateReview({
  row,
  disabled,
  onSave,
  onSkip,
}: {
  row: QualityTarget;
  disabled: boolean;
  onSave: (answer: Answer) => Promise<void>;
  onSkip: () => void;
}) {
  const [suitable, setSuitable] = useState<boolean | null>(
      row.review?.answer.suitable ?? null,
    ),
    [comment, setComment] = useState(row.review?.answer.comment || "");
  return (
    <div className="stack">
      <div className="row between">
        <h2>Оцените соответствие пары</h2>
        <span className={`badge ${row.review ? "success" : "warning"}`}>
          {row.review ? "Проверено" : "Не проверено"}
        </span>
      </div>
      <div className="source-box">
        <small>Складской запас</small>
        {row.target.residual?.source_text}
      </div>
      <div>
        <small>Потребность</small>
        <p style={{ marginTop: 6 }}>{row.target.requirement?.source_text}</p>
      </div>
      {row.target.requirement && row.target.residual && (
        <ComparisonTable
          requirement={row.target.requirement}
          residual={row.target.residual}
        />
      )}
      <div className="row wrap">
        <button
          className={suitable === true ? "btn-primary" : "btn"}
          disabled={disabled}
          onClick={() => setSuitable(true)}
        >
          ✓ Подходит
        </button>
        <button
          className={suitable === false ? "btn-primary" : "btn"}
          disabled={disabled}
          onClick={() => setSuitable(false)}
        >
          Не подходит
        </button>
        <button
          className={suitable === null ? "btn-success" : "btn-ghost"}
          disabled={disabled}
          onClick={() => setSuitable(null)}
        >
          Не могу оценить
        </button>
      </div>
      <label>
        Комментарий эксперта
        <textarea
          rows={2}
          maxLength={2000}
          value={comment}
          disabled={disabled}
          onChange={(event) => setComment(event.target.value)}
        />
      </label>
      <small>
        Ранг {row.target.result?.rank ?? "не определён"} ·{" "}
        {row.target.result?.is_shortlisted ? "В shortlist" : "Вне shortlist"}.
        Разметка качества не заменяет закупочное решение.
      </small>
      {suitable === null && (
        <small>
          Пара будет пропущена без сохранения новой оценки. Для Recall её нужно
          будет проверить после уточнения данных.
        </small>
      )}
      <div className="row between">
        <button className="btn-ghost" disabled={disabled} onClick={onSkip}>
          Пропустить
        </button>
        <button
          className="btn-primary"
          disabled={disabled}
          onClick={() =>
            suitable === null ? onSkip() : void onSave({ suitable, comment })
          }
        >
          {suitable === null ? "Далее без оценки" : "Сохранить и далее"}
        </button>
      </div>
    </div>
  );
}
export function QualityPage({initialMode="text",onReviewed}:{initialMode?:"text"|"parameters"|"category";onReviewed?:()=>void}={}) {
  const w = useWorkspace();
  const [kind, setKind] = useState<"passport" | "candidate">("passport"),
    [shortlisted, setShortlisted] = useState(false),
    [unreviewed, setUnreviewed] = useState(false),
    [query, setQuery] = useState(""),
    [requirement, setRequirement] = useState(""),
    [offset, setOffset] = useState(0),
    [pageSize, setPageSize] = useState(10),
    [passportKind, setPassportKind] = useState("residual"),
    [selectedId, setSelectedId] = useState(""),
    [revision, setRevision] = useState(0);
  const [jobId,setJobId]=useState("");
  const [reviewMode,setReviewMode]=useState<"text" | "parameters" | "category">(initialMode);
  const lastOnPage=useRef(false);
  const runs=useResource(()=>labApi.dashboard(w.sid),[w.sid,revision],!!w.sid);
  const q = useDebouncedValue(query);
  const summary = useResource(
    () => qualityApi.summary(w.sid,jobId),
    [w.sid, jobId, w.data?.job.id, w.data?.job.status, revision],
    !!w.sid,
  );
  const rows = useResource(
    () =>
      qualityApi.targets(w.sid, kind, offset, shortlisted, {
        q,
        ...(jobId ? {job_id:jobId}:{}),
        limit: String(pageSize),
        ...(kind === "passport" && passportKind ? { passport_kind: passportKind } : {}),
        ...(kind === "passport" ? {review_mode: reviewMode} : {}),
        unreviewed: String(unreviewed),
        ...(kind === "candidate" && requirement
          ? { requirement_id: requirement }
          : {}),
      }),
    [
      w.sid,
      kind,
      offset,
      pageSize,
      passportKind,
      reviewMode,
      shortlisted,
      q,
      unreviewed,
      requirement,
      revision,
      summary.data?.job_id,
    ],
    !!summary.data?.job_id,
  );
  useEffect(() => {
    if (!rows.data) return;
    const chooseLast=lastOnPage.current;lastOnPage.current=false;
    setSelectedId((previous) =>
        chooseLast ? (rows.data!.items.at(-1)?.target.id || "") :
        rows.data!.items.some((row) => row.target.id === previous)
          ? previous
          : rows.data!.items[0]?.target.id || "",
      );
  }, [rows.data]);
  if (!w.sid) return <Empty />;
  if (summary.loading && !summary.data) return <Loading />;
  if (summary.error) return <ErrorBox message={summary.error} />;
  if (!summary.data?.job_id)
    return (
      <Empty
        title="Нормализация ещё не завершена"
        description="Завершите нормализацию, чтобы просмотреть и проверить все паспорта сессии."
      />
    );
  const quality = summary.data,
    coverage = quality.coverage;
  const selected = rows.data?.items.find((row) => row.target.id === selectedId);
  const disabled = w.busy || w.active || !w.canExpert;
  function next() {
    if (!rows.data) return;
    const index = rows.data.items.findIndex(
      (row) => row.target.id === selectedId,
    );
    const item = rows.data.items[index + 1];
    if (item) setSelectedId(item.target.id);
    else if (offset + pageSize < rows.data.total) setOffset(offset + pageSize);
    // Stay on the last position; do not wrap to the beginning.
  }
  async function save(answer: Answer) {
    if (!selected || !rows.data) return;
    if (
      await w.act(
        () =>
          qualityApi.save(
            w.sid,
            rows.data!.job_id,
            kind,
            selected.target.id,
            answer,
          ),
        "Проверка сохранена",
      )
    ) {
      if (unreviewed) {
        const remaining = rows.data.items.filter(
          (row) => row.target.id !== selected.target.id,
        );
        setSelectedId(remaining[0]?.target.id || "");
        if (rows.data.total - 1 <= offset) setOffset(Math.max(0, offset - pageSize));
      } else next();
      setRevision((value) => value + 1); onReviewed?.();
    }
  }
  const reviewed =
      kind === "passport"
        ? (reviewMode === "text" ? coverage.text_reviewed : coverage.passports_reviewed)
        : coverage.pairs_reviewed,
    total =
      kind === "passport" ? (reviewMode === "text" ? coverage.stock_total : coverage.passports_total) : coverage.pairs_total;
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow="Экспертная разметка"
        title="Проверка качества"
        description="Создайте эталонные ответы для оценки извлечения, классификации и подбора."
        actions={
          <button
            className="btn-primary"
            disabled={
              disabled ||
              (!coverage.pairs_reviewed && !coverage.passports_reviewed)
            }
            onClick={async () => {
              if (
                await w.act(
                  () => qualityApi.finalize(w.sid, quality.job_id!),
                  "Проверка завершена, доступные метрики рассчитаны",
                )
              )
                setRevision((value) => value + 1); onReviewed?.();
            }}
          >
            Завершить экспертную проверку
          </button>
        }
      />
      <label>Расчёт для ручной проверки<select value={jobId} onChange={e=>{setJobId(e.target.value);setOffset(0);setSelectedId("");}}><option value="">Последний завершённый</option>{runs.data?.runs.map(r=><option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString("ru-RU")} · {r.kind} · {r.id.slice(0,8)}</option>)}</select></label>
      <div className="tabs">
        <button
          className={kind === "passport" ? "active" : ""}
          onClick={() => {
            setKind("passport");
            setOffset(0);
          }}
        >
          Проверка МТР
        </button>
        <button
          className={kind === "candidate" ? "active" : ""}
          onClick={() => {
            setKind("candidate");
            setOffset(0);
          }}
        >
          Подходит / не подходит
        </button>
        <span className="badge info" style={{ marginLeft: "auto" }}>
          {reviewed || 0} из {total || 0}{" "}
          {kind === "passport" ? "паспортов" : "пар"}
        </span>
      </div>
      {kind === "passport" && <label>Что проверяем<select aria-label="Режим проверки паспорта" value={reviewMode} onChange={e=>{setReviewMode(e.target.value as typeof reviewMode);setPassportKind("residual");setUnreviewed(false);setOffset(0);}}><option value="text">Совпадение названия · дополнительная метрика</option><option value="parameters">H1 · Извлечение технических параметров</option><option value="category">H2 · Только категория</option></select></label>}
      <div className="split queue-layout">
        <aside className="panel compact stack">
          <h3>Все паспорта и пары сессии</h3>
          <label>На странице<select aria-label="Позиций на странице" value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setOffset(0); }}><option value={10}>10</option><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option></select></label>
          {kind === "passport" && reviewMode !== "text" && <label>Тип позиции<select value={passportKind} onChange={e => { setPassportKind(e.target.value); setOffset(0); }}><option value="">Все паспорта</option><option value="residual">Остатки Excel</option><option value="requirement">Потребности</option></select></label>}
          <input
            placeholder="Поиск позиции"
            aria-label="Поиск позиции"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setOffset(0);
            }}
          />
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={unreviewed}
              onChange={(event) => {
                setUnreviewed(event.target.checked);
                setOffset(0);
              }}
            />
            Только непроверенные
          </label>
          {kind === "candidate" && (
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
          )}
          {requirement && kind === "candidate" && (
            <button
              className="btn"
              onClick={() => {
                setRequirement("");
                setOffset(0);
              }}
            >
              Все потребности
            </button>
          )}
          <div className="queue">
            {rows.data?.items.map((row) => (
              <button
                key={row.target.id}
                className={`queue-item ${selectedId === row.target.id ? "active" : ""}`}
                onClick={() => setSelectedId(row.target.id)}
              >
                <span>
                  {kind === "passport"
                    ? row.target.passport?.name || row.target.source_text
                    : row.target.residual?.passport?.name ||
                      row.target.residual?.source_text}
                </span>
                <small>
                  {(kind === "passport" ? reviewedInMode(row,reviewMode) : !!row.review) ? "Проверено" : "Не проверено"} ·{" "}
                  {kind === "passport"
                    ? row.target.kind === "requirement"
                      ? "Потребность"
                      : "Остаток"
                    : `Ранг ${row.target.result?.rank ?? "—"}`}
                </small>
              </button>
            ))}
          </div>
          <small>
            Сохраняйте проверку после исправления каждого паспорта или оценки
            пары.
          </small>
        </aside>
        <section className="panel stack">
          {rows.error && <ErrorBox message={rows.error} />}{" "}
          {rows.loading ? (
            <Loading />
          ) : selected ? (
            <>
              <div className="row between"><button className="btn" disabled={!rows.data || (offset === 0 && rows.data.items[0]?.target.id === selectedId)} onClick={() => { const index = rows.data!.items.findIndex(r => r.target.id === selectedId); if (index > 0) setSelectedId(rows.data!.items[index - 1].target.id); else { lastOnPage.current=true; setOffset(Math.max(0, offset - pageSize)); } }}>Предыдущая позиция</button><button className="btn" onClick={next}>Следующая позиция</button></div>
              <div
                key={`${selected.target.id}-${selected.review?.updated_at || "new"}`}
              >
                {kind === "passport" ? (
                  <PassportReview
                    key={reviewMode}
                    mode={reviewMode}
                    schema={rows.data?.schema}
                    row={selected}
                    disabled={disabled}
                    onSave={save}
                    onSkip={next}
                  />
                ) : (
                  <CandidateReview
                    row={selected}
                    disabled={disabled}
                    onSave={save}
                    onSkip={next}
                  />
                )}
              </div>
              {kind === "candidate" && selected.target.requirement && (
                <button
                  className="link-button"
                  onClick={() => {
                    setRequirement(selected.target.requirement!.id);
                    setShortlisted(false);
                    setOffset(0);
                  }}
                >
                  Проверять все пары этой потребности
                </button>
              )}
              {selected.review && (
                <button
                  className="link-button"
                  disabled={disabled}
                  onClick={async () => {
                    if (
                      await w.act(
                        () =>
                          qualityApi.remove(
                            w.sid,
                            quality.job_id!,
                            kind,
                            selected.target.id,
                          ),
                        "Отметка проверки снята",
                      )
                    )
                      setRevision((value) => value + 1); onReviewed?.();
                  }}
                >
                  Снять отметку проверки
                </button>
              )}
            </>
          ) : (
            <p className="muted">Позиций по выбранным фильтрам нет.</p>
          )}
          <Pagination
            offset={offset}
            total={rows.data?.total || 0}
            pageSize={pageSize}
            onChange={setOffset}
            busy={rows.loading}
          />
        </section>
      </div>
      <Notice
        title={
          quality.finalized
            ? "Проверка завершена"
            : kind === "candidate"
              ? "Для Recall@150 нужна полная проверка пар"
              : "Проверка ещё не завершена"
        }
        tone={quality.finalized ? "success" : "warning"}
      >
        {quality.finalized
          ? `F1: ${percentage(quality.f1)} · Accuracy: ${percentage(quality.accuracy)} · Precision: ${percentage(quality.precision_shortlist)} · Recall@150: ${percentage(quality.recall_at_150)}. Любая правка потребует повторного завершения.`
          : kind === "candidate"
            ? `Проверяйте все пары выбранной потребности, включая позиции вне shortlist. Полностью проверено потребностей: ${coverage.requirements_fully_reviewed || 0} из ${coverage.requirements_total || 0}. Метрика доступна при наличии подходящих пар.`
            : `Проверено ${coverage.fields_checked || 0} значений и ${coverage.categories_checked || 0} категорий. Итоговые метрики появятся после завершения проверки.`}
      </Notice>
      <footer className="page-footer">
        <span>
          Расчёт: {quality.job_id} · метрики описывают только проверенную
          выборку.
        </span>
        <button className="btn" onClick={() => w.setTab("metrics")}>
          Открыть метрики
        </button>
      </footer>
    </div>
  );
}
