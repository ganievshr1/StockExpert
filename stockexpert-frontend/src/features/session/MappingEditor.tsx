import { useEffect, useState } from "react";
import { useWorkspace } from "../../state/Workspace";
import { api } from "../../services/api";
import type { SchemaDraft } from "../../types";
import { Notice } from "../../components/ui";
export function excelLetter(index: string) {
  let number = Number(index) + 1;
  if (!Number.isInteger(number) || number < 1) return index;
  let result = "";
  while (number) {
    number--;
    result = String.fromCharCode(65 + (number % 26)) + result;
    number = Math.floor(number / 26);
  }
  return result;
}
export function columnIndex(value: string) {
  const raw = value.trim().toUpperCase();
  if (/^\d+$/.test(raw)) return String(Number(raw));
  if (!/^[A-Z]+$/.test(raw))
    throw Error("Укажите буквы Excel, например A, C, D");
  let number = 0;
  for (const letter of raw) number = number * 26 + letter.charCodeAt(0) - 64;
  return String(number - 1);
}
export function MappingEditor() {
  const w = useWorkspace();
  const server = w.data?.session.schema_doc;
  const [draft, setDraft] = useState<SchemaDraft | null>(null),
    [dirty, setDirty] = useState(false),
    [names, setNames] = useState<string[]>([]),
    [categories, setCategories] = useState<string[]>([]);
  useEffect(() => {
    if (server?.mappings && !dirty) {
      setDraft(structuredClone(server as SchemaDraft));
      setNames(
        server.mappings.map((mapping) =>
          mapping.name_columns.map(excelLetter).join(", "),
        ),
      );
      setCategories(
        server.mappings.map((mapping) =>
          mapping.category_column === null
            ? ""
            : excelLetter(mapping.category_column),
        ),
      );
    }
  }, [server, dirty]);
  if (!draft) return null;
  const locked =
    w.active ||
    w.busy ||
    !w.canExpert ||
    w.data?.session.approved ||
    draft.mapping_approved;
  async function save(approve: boolean) {
    if (!draft) return;
    const schema = {
      ...draft,
      mappings: draft.mappings.map((mapping, index) => ({
        ...mapping,
        name_columns: names[index].split(",").map(columnIndex),
        category_column: categories[index].trim()
          ? columnIndex(categories[index])
          : null,
      })),
    };
    await api.saveSchema(w.sid, schema);
    if (approve) await api.approveMapping(w.sid);
    setDirty(false);
  }
  return (
    <section className="panel stack">
      <h3>Проверьте столбцы файла</h3>
      {draft.mappings.map((mapping, index) => (
        <div className="stack" key={mapping.document_id + mapping.sheet}>
          <small>
            {
              w.data?.documents.find(
                (document) => document.id === mapping.document_id,
              )?.filename
            }{" "}
            · {mapping.sheet}
          </small>
          <div className="filters-grid">
            <label>
              Лист
              <input value={mapping.sheet} disabled />
            </label>
            <label>
              Строка заголовков
              <input
                type="number"
                min={0}
                value={mapping.header_row}
                disabled={locked}
                onChange={(event) => {
                  setDirty(true);
                  setDraft({
                    ...draft,
                    mappings: draft.mappings.map((item, i) =>
                      i === index
                        ? { ...item, header_row: Number(event.target.value) }
                        : item,
                    ),
                  });
                }}
              />
            </label>
            <label>
              Описание позиции
              <input
                aria-label={`Столбцы описания ${mapping.sheet}`}
                value={names[index] || ""}
                disabled={locked}
                onChange={(event) => {
                  setDirty(true);
                  setNames(
                    names.map((value, i) =>
                      i === index ? event.target.value : value,
                    ),
                  );
                }}
              />
            </label>
            <label>
              Категория — контекст
              <input
                value={categories[index] || ""}
                placeholder="Не задана"
                disabled={locked}
                onChange={(event) => {
                  setDirty(true);
                  setCategories(
                    categories.map((value, i) =>
                      i === index ? event.target.value : value,
                    ),
                  );
                }}
              />
            </label>
          </div>
        </div>
      ))}
      <small>
        Используйте буквы Excel: A, B, C. В описание включите все столбцы с
        техническими характеристиками. Строка 0 означает отсутствие заголовка.
      </small>
      {!draft.mapping_approved && (
        <div className="row wrap">
          <button
            className="btn"
            disabled={locked}
            onClick={() => void w.act(() => save(false), "Столбцы сохранены")}
          >
            Сохранить столбцы
          </button>
          <button
            className="btn-primary"
            disabled={locked}
            onClick={() => void w.act(() => save(true), "Столбцы утверждены")}
          >
            Утвердить столбцы
          </button>
        </div>
      )}
      {draft.mapping_approved && (
        <Notice title="Столбцы утверждены" tone="success">
          Следующий этап — извлечение характеристик и построение категорий.
        </Notice>
      )}
    </section>
  );
}
