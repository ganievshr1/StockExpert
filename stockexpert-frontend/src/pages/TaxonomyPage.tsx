import { ScrollArea } from "../components/ScrollArea";
import { useEffect, useState, type FormEvent } from "react";
import { useWorkspace } from "../state/Workspace";
import { api } from "../services/api";
import { workspaceApi } from "../services/workspace";
import { useResource } from "../hooks/useResource";
import type { SchemaDraft, Feature, Category } from "../types";
import { MappingEditor } from "../features/session/MappingEditor";
import { Stepper } from "../components/Stepper";
import {
  Empty,
  ErrorBox,
  Loading,
  Modal,
  Notice,
  PageTitle,
  Pagination,
} from "../components/ui";
export function TaxonomyPage() {
  const w = useWorkspace();
  const [draft, setDraft] = useState<SchemaDraft | null>(null),
    [dirty, setDirty] = useState(false),
    [selected, setSelected] = useState(""),
    [version, setVersion] = useState("v1"),
    [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0),
    [showJson, setShowJson] = useState(false),
    [jsonText, setJsonText] = useState(""),
    [addition, setAddition] = useState<"category" | "feature" | null>(null),
    [featureKind, setFeatureKind] = useState<Feature["kind"]>("string");
  const server = w.data?.session.schema_doc;
  useEffect(() => {
    if (server?.mappings && !dirty) {
      setDraft(structuredClone(server as SchemaDraft));
      setVersion(w.data?.session.taxonomy_version || "v1");
      setSelected((previous) =>
        server.categories?.some((category) => category.id === previous)
          ? previous
          : server.categories?.[0]?.id || "",
      );
    }
  }, [server, dirty, w.data?.session.taxonomy_version]);
  const items = useResource(
    () => api.passports(w.sid, offset, selected),
    [w.sid, selected, offset, server],
    !!w.sid && !!selected,
  );
  if (!w.sid) return <Empty />;
  if (!w.data) return <Loading />;
  if (!draft)
    return (
      <Empty
        title="Столбцы ещё не определены"
        description="Загрузите остатки и опросный лист, затем запустите подготовку данных."
      >
        <button
          className="btn-primary"
          style={{ marginTop: 20 }}
          onClick={() => w.setTab("data")}
        >
          Открыть данные
        </button>
      </Empty>
    );
  const current = draft,
    modern = current.taxonomy_mode === "passports",
    built = !modern || current.taxonomy_status !== "pending";
  const locked = w.data.session.approved || w.active || !w.canExpert;
  const category = current.categories.find((value) => value.id === selected);
  const keys = new Set(category?.feature_keys || []);
  const fields = modern
    ? current.features.filter((feature) => keys.has(feature.key))
    : current.features;
  const change = (schema: SchemaDraft) => {
    setDraft(schema);
    setDirty(true);
  };
  const edit = (key: string, patch: Partial<Feature>) =>
    change({
      ...current,
      features: current.features.map((feature) =>
        feature.key === key ? { ...feature, ...patch } : feature,
      ),
    });
  async function save(approve = false) {
    const result = await api.saveSchema(w.sid, current);
    setDraft(result);
    setDirty(false);
    if (approve) await api.approve(w.sid, version);
  }
  function choose(value: string) {
    setSelected(value);
    setOffset(0);
  }
  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const success = await w.act(async () => {
      if (dirty) await save();
      let result: SchemaDraft;
      if (addition === "category") {
        result = await workspaceApi.category(
          w.sid,
          String(form.get("name")).trim(),
          String(form.get("parent")) || null,
        );
      } else {
        result = await workspaceApi.feature(w.sid, selected, {
          key: "manual_" + crypto.randomUUID().replaceAll("-", ""),
          label: String(form.get("name")).trim(),
          kind: featureKind,
          unit: featureKind === "string" ? "none" : String(form.get("unit")),
          comparison: "informational",
          tolerance: 0,
        });
      }
      setDraft(result);
      setDirty(false);
    }, "Классификатор дополнен");
    if (success) setAddition(null);
  }
  function categoryButton(value: Category) {
    return (
      <button
        key={value.id}
        className={`queue-item ${selected === value.id ? "active" : ""}`}
        style={{
          background: selected === value.id ? undefined : "transparent",
          marginLeft: value.parent_id ? 12 : 0,
          width: value.parent_id ? "calc(100% - 12px)" : undefined,
        }}
        onClick={() => choose(value.id)}
      >
        <span>{value.name}</span>
        <small>
          {value.item_count || 0} остатков · {value.requirement_count || 0}{" "}
          потребностей
        </small>
      </button>
    );
  }
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow="Схема данных"
        title="Классификатор и правила"
        description="Уточните категории и правила сравнения перед запуском подбора."
        actions={
          built ? (
            <>
              <button
                className="btn"
                disabled={locked || w.busy || showJson}
                onClick={() => void w.act(() => save(), "Схема сохранена")}
              >
                Сохранить
              </button>
              <button
                className="btn-primary"
                aria-label="Утвердить таксономию"
                disabled={locked || w.busy || showJson || !version.trim()}
                onClick={() =>
                  void w.act(() => save(true), "Классификатор утверждён")
                }
              >
                Утвердить схему
              </button>
            </>
          ) : undefined
        }
      />
      <Stepper current={1} />
      {!built && (
        <>
          <MappingEditor />
          {current.mapping_approved && (
            <section className="panel stack">
              <Notice title="Столбцы утверждены">
                Следующий шаг — нормализация всех позиций и построение
                классификатора.
              </Notice>
              <button
                className="btn-primary"
                disabled={w.active || w.busy || !w.canEdit}
                onClick={async () => {
                  if (
                    await w.act(
                      () => api.normalize(w.sid),
                      "Нормализация и построение категорий поставлены в очередь",
                    )
                  )
                    w.setTab("processing");
                }}
              >
                Нормализовать все позиции и построить категории
              </button>
            </section>
          )}
        </>
      )}
      {built && (
        <div className="split sidebar-left">
          <section className="panel compact stack">
            <h3>Категории МТР</h3>
            <input
              aria-label="Найти категорию"
              placeholder="Найти категорию"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <div className="queue">
              {current.categories
                .filter((value) =>
                  value.name
                    .toLocaleLowerCase()
                    .includes(query.toLocaleLowerCase()),
                )
                .map(categoryButton)}
            </div>
            <div className="danger-zone">
              <button
                className="btn full-width"
                disabled={locked || w.busy || !modern}
                onClick={() => {
                  setAddition("category");
                }}
              >
                Добавить категорию
              </button>
            </div>
            <small>
              Категории сформированы по наименованиям и характеристикам всех
              позиций.
            </small>
          </section>
          <div className="stack">
            {category && (
              <section className="panel stack">
                <div className="row between wrap">
                  <label className="flex-1">
                    Название категории
                    <input
                      value={category.name}
                      disabled={locked}
                      onChange={(event) =>
                        change({
                          ...current,
                          categories: current.categories.map((value) =>
                            value.id === selected
                              ? { ...value, name: event.target.value }
                              : value,
                          ),
                        })
                      }
                    />
                  </label>
                  <span
                    className={`badge ${w.data.session.approved ? "success" : "warning"}`}
                  >
                    {w.data.session.approved
                      ? "Схема утверждена"
                      : "Проверить перед утверждением"}
                  </span>
                </div>
                <h3>Технические признаки</h3>
                <ScrollArea
                  label="Характеристики классификатора"
                  className="taxonomy-table"
                >
                  <table>
                    <thead>
                      <tr>
                        <th>Параметр</th>
                        <th>Сравнение</th>
                        <th>Единица</th>
                        <th>Допуск</th>
                        <th>Роль</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fields.map((feature) => (
                        <tr key={feature.key}>
                          <td>
                            <input
                              aria-label="Признак"
                              value={feature.label}
                              disabled={locked}
                              onChange={(event) =>
                                edit(feature.key, { label: event.target.value })
                              }
                            />
                          </td>
                          <td>
                            <select
                              aria-label="Сравнение"
                              value={feature.comparison}
                              disabled={locked || feature.key === "name"}
                              onChange={(event) =>
                                edit(feature.key, {
                                  comparison: event.target
                                    .value as Feature["comparison"],
                                })
                              }
                            >
                              <option value="informational">
                                Не сравнивать
                              </option>
                              <option value="eq">Равно</option>
                              {feature.kind !== "string" && (
                                <>
                                  <option value="gte">Не менее</option>
                                  <option value="lte">Не более</option>
                                </>
                              )}
                            </select>
                          </td>
                          <td>
                            {feature.unit === "none" ? "—" : feature.unit}
                          </td>
                          <td>
                            <input
                              style={{ minWidth: 64 }}
                              aria-label={`Допуск ${feature.label}`}
                              type="number"
                              min={0}
                              step="any"
                              value={feature.tolerance}
                              disabled={locked || feature.kind === "string"}
                              onChange={(event) =>
                                edit(feature.key, {
                                  tolerance: Number(event.target.value),
                                })
                              }
                            />
                          </td>
                          <td className="small">
                            {feature.comparison === "informational"
                              ? "Информация"
                              : "Обязательный"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </ScrollArea>
                <div className="row wrap">
                  <button
                    className="btn"
                    disabled={locked || w.busy || !modern}
                    onClick={() => {
                      setAddition("feature");
                      setFeatureKind("string");
                    }}
                  >
                    Добавить признак
                  </button>
                  <button
                    className="btn-ghost"
                    disabled={locked}
                    onClick={() => {
                      setShowJson(true);
                      setJsonText(JSON.stringify(current, null, 2));
                    }}
                  >
                    Редактировать JSON
                  </button>
                </div>
                <Notice title="Как работают правила">
                  Информационные признаки видны эксперту, но не влияют на
                  автоматическое соответствие. Новый признак не заполняет
                  существующие паспорта автоматически: отсутствующие значения
                  требуют проверки.
                </Notice>
              </section>
            )}
            <details className="panel">
              <summary>Позиции категории и источники</summary>
              {items.error && <ErrorBox message={items.error} />}
              <div className="stack" style={{ marginTop: 20 }}>
                {items.loading ? (
                  <Loading />
                ) : (
                  items.data?.items.map((item) => (
                    <details key={item.id}>
                      <summary>
                        {item.passport?.name || item.source_text}
                      </summary>
                      <p className="small muted" style={{ margin: "12px 0" }}>
                        {item.source_text}
                      </p>
                      <pre>
                        {JSON.stringify(
                          item.passport?.normalized_attributes ||
                            item.passport?.attributes,
                          null,
                          2,
                        )}
                      </pre>
                      {item.issues.length > 0 && (
                        <small>
                          {item.issues.map((issue) => issue.code).join(", ")}
                        </small>
                      )}
                    </details>
                  ))
                )}
              </div>
              <Pagination
                offset={offset}
                total={items.data?.total || 0}
                onChange={setOffset}
                busy={items.loading}
              />
            </details>
            <details className="panel">
              <summary>Версия и дополнительные действия</summary>
              <div className="stack" style={{ marginTop: 20 }}>
                <label>
                  Версия схемы
                  <input
                    value={version}
                    maxLength={50}
                    disabled={locked}
                    onChange={(event) => setVersion(event.target.value)}
                  />
                </label>
                <div className="row wrap">
                  {modern && !w.data.session.approved && (
                    <button
                      className="btn"
                      disabled={locked || w.busy || dirty || showJson}
                      onClick={async () => {
                        if (
                          await w.act(
                            () => api.rebuildTaxonomy(w.sid),
                            "Перестроение поставлено в очередь",
                          )
                        )
                          w.setTab("processing");
                      }}
                    >
                      Перестроить по паспортам
                    </button>
                  )}
                  <button className="btn" onClick={() => w.setTab("data")}>
                    Сопоставление столбцов
                  </button>
                  {w.data.session.approved && (
                    <button
                      className="btn-primary"
                      onClick={() => w.setTab("data")}
                    >
                      Перейти к запуску подбора
                    </button>
                  )}
                </div>
                <small>
                  Перестроение повторно создаёт категории из сохранённых
                  паспортов и заменяет ручные дополнения. Утверждённая схема
                  неизменна.
                </small>
              </div>
            </details>
          </div>
        </div>
      )}
      {!w.canExpert && (
        <Notice title="Режим просмотра">
          Изменение и утверждение доступны эксперту МТО или администратору.
        </Notice>
      )}
      {showJson && (
        <Modal title="JSON схемы" onClose={() => setShowJson(false)}>
          <div className="stack">
            <textarea
              aria-label="JSON схемы"
              rows={18}
              value={jsonText}
              onChange={(event) => setJsonText(event.target.value)}
            />
            <small>
              Идентификаторы, единицы, состав существующих категорий и
              назначения защищены от рассогласования с паспортами.
            </small>
            <button
              className="btn-primary"
              disabled={locked || w.busy}
              onClick={() =>
                void w.act(async () => {
                  const result = await api.saveSchema(
                    w.sid,
                    JSON.parse(jsonText),
                  );
                  setDraft(result);
                  setDirty(false);
                  setShowJson(false);
                }, "Схема сохранена")
              }
            >
              Проверить и сохранить JSON
            </button>
          </div>
        </Modal>
      )}
      {addition && (
        <Modal
          title={
            addition === "category" ? "Добавить категорию" : "Добавить признак"
          }
          onClose={() => !w.busy && setAddition(null)}
        >
          <form onSubmit={(event) => void add(event)} className="stack">
            <label>
              Название
              <input name="name" required maxLength={150} autoFocus />
            </label>
            {addition === "category" ? (
              <label>
                Родительская категория
                <select name="parent" defaultValue="">
                  <option value="">Новый тип материала</option>
                  {current.categories.map((value) => (
                    <option key={value.id} value={value.id}>
                      {value.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <>
                <label>
                  Тип значения
                  <select
                    value={featureKind}
                    onChange={(event) =>
                      setFeatureKind(event.target.value as Feature["kind"])
                    }
                  >
                    <option value="string">Текст</option>
                    <option value="number">Число</option>
                    <option value="integer">Целое число</option>
                  </select>
                </label>
                {featureKind !== "string" && (
                  <label>
                    Единица
                    <select name="unit" defaultValue="none">
                      {[
                        "none",
                        "V",
                        "kV",
                        "mm",
                        "mm2",
                        "MPa",
                        "bar",
                        "m",
                        "kg",
                        "pcs",
                      ].map((unit) => (
                        <option key={unit} value={unit}>
                          {unit === "none" ? "Без единицы" : unit}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <small>
                  Признак будет доступен всему типу материала и его
                  подкатегориям. Значения в готовых паспортах автоматически не
                  заполняются.
                </small>
              </>
            )}
            {w.error && <ErrorBox message={w.error} />}
            <div className="modal-actions">
              <button
                className="btn"
                type="button"
                onClick={() => setAddition(null)}
              >
                Отмена
              </button>
              <button className="btn-primary" disabled={w.busy}>
                Добавить
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
