import { ScrollArea } from "../../components/ScrollArea";
import { useState, type DragEvent } from "react";
import { useWorkspace } from "../../state/Workspace";
import { api } from "../../services/api";
import type { DocumentRecord } from "../../types";
import { Icon } from "../../components/Icon";
import { Modal } from "../../components/ui";
import { excelLetter } from "./MappingEditor";
type SheetPreview = {
  name: string;
  rows: number;
  preview: { row_index: number; cells: Record<string, string> }[];
};
export function DocumentPreview({
  document,
  onClose,
}: {
  document: DocumentRecord;
  onClose: () => void;
}) {
  const sheets = (document.meta.sheets || []) as SheetPreview[];
  const [index, setIndex] = useState(0);
  const sheet = sheets[index];
  const columns = Array.from(
    new Set(sheet?.preview.flatMap((row) => Object.keys(row.cells)) || []),
  ).sort((a, b) => Number(a) - Number(b));
  return (
    <Modal title={`Предпросмотр · ${document.filename}`} onClose={onClose}>
      <div className="stack file-preview">
        {sheet ? (
          <>
            <label>
              Лист
              <select
                value={index}
                onChange={(event) => setIndex(Number(event.target.value))}
              >
                {sheets.map((item, i) => (
                  <option key={item.name} value={i}>
                    {item.name} · {item.rows} строк
                  </option>
                ))}
              </select>
            </label>
            <ScrollArea label="Предпросмотр Excel" className="excel-table">
              <table>
                <thead>
                  <tr>
                    <th>Строка</th>
                    {columns.map((column) => (
                      <th key={column}>{excelLetter(column)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sheet.preview.map((row) => (
                    <tr key={row.row_index}>
                      <td>{row.row_index}</td>
                      {columns.map((column) => (
                        <td key={column}>{row.cells[column] || ""}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollArea>
            <small>
              Первые {sheet.preview.length} непустых строк. Для обработки будут
              использованы все строки файла.
            </small>
          </>
        ) : (
          <pre>{String(document.meta.text || "Предпросмотр недоступен")}</pre>
        )}
      </div>
    </Modal>
  );
}
export function DocumentCard({ kind }: { kind: "residuals" | "requirement" }) {
  const w = useWorkspace();
  const [file, setFile] = useState<File | null>(null),
    [preview, setPreview] = useState<DocumentRecord | null>(null),
    [replace, setReplace] = useState<string | undefined>(),
    [dragging, setDragging] = useState(false),
    [inputKey, setInputKey] = useState(0),
    [removing, setRemoving] = useState<DocumentRecord | null>(null);
  const documents =
    w.data?.documents.filter(
      (document) =>
        document.kind === (kind === "residuals" ? "residual" : "requirement"),
    ) || [];
  const isRequirement = kind === "requirement";
  const reusable = !!w.data?.session.base_snapshot?.source_session_id;
  const locked =
    w.active ||
    w.busy ||
    !w.canEdit ||
    (!!w.data?.session.schema_doc.mappings &&
      !(reusable && isRequirement && w.data?.job.status === "NOT_STARTED"));
  function drop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (!locked) setFile(event.dataTransfer.files[0] || null);
  }
  async function upload() {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024)
      throw Error("Размер файла превышает 20 МБ");
    if (replace) await api.replaceDocument(w.sid, replace, file);
    else await api.upload(w.sid, kind, file);
    setFile(null);
    setReplace(undefined);
    setInputKey((value) => value + 1);
  }
  return (
    <section className="panel document-card">
      <div className="row between">
        <h3 className="row">
          <Icon name="upload" />
          {isRequirement ? "Опросный лист" : "Остатки склада"}
        </h3>
        {documents.length > 0 && (
          <span className="badge success">Загружено</span>
        )}
      </div>
      <p className="muted">
        {isRequirement
          ? "Что планируется закупить — потребность из TXT"
          : "Что уже есть на складах компании — база Excel"}
      </p>
      {documents.map((document) => (
        <div className="stack" key={document.id}>
          <div className="source-box">
            <p>{document.filename}</p>
            <small style={{ margin: "6px 0 0", textTransform: "none" }}>
              {document.kind === "residual"
                ? `${((document.meta.sheets as SheetPreview[]) || []).reduce((sum, sheet) => sum + sheet.rows, 0)} непустых строк · ${((document.meta.sheets as SheetPreview[]) || []).length} листов`
                : `${String(document.meta.text || "").length} символов`}
              {document.meta.size_bytes
                ? ` · ${(Number(document.meta.size_bytes) / 1024).toFixed(0)} КБ`
                : ""}
            </small>
          </div>
          <div className="row wrap">
            <button
              className="btn"
              disabled={locked}
              onClick={() => {
                setReplace(document.id);
                setFile(null);
              }}
            >
              Заменить файл
            </button>
            <button className="btn-ghost" onClick={() => setPreview(document)}>
              Предпросмотр
            </button>
            <button
              className="link-button"
              disabled={locked}
              onClick={() => setRemoving(document)}
            >
              Удалить
            </button>
          </div>
        </div>
      ))}
      {(!documents.length || replace) && (
        <>
          <label
            className={`file-drop ${dragging ? "dragging" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              if (!locked) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={drop}
          >
            <span>{file?.name || "Перетащите файл сюда или выберите его"}</span>
            <input
              key={inputKey}
              aria-label={
                isRequirement ? "Опросный лист TXT" : "База остатков Excel"
              }
              type="file"
              accept={isRequirement ? ".txt,.xlsx,.docx,.pdf" : ".xlsx"}
              disabled={locked}
              onChange={(event) => setFile(event.target.files?.[0] || null)}
            />
            <small>
              До 20 МБ ·{" "}
              {isRequirement
                ? "TXT; также поддерживаются XLSX, DOCX и текстовый PDF"
                : "XLSX"}
            </small>
          </label>
          <div className="row">
            <button
              className="btn-primary"
              disabled={locked || !file}
              onClick={() =>
                void w.act(upload, replace ? "Файл заменён" : "Файл загружен")
              }
            >
              {replace ? "Сохранить замену" : "Загрузить файл"}
            </button>
            {replace && (
              <button
                className="btn-ghost"
                onClick={() => {
                  setReplace(undefined);
                  setFile(null);
                }}
              >
                Отмена
              </button>
            )}
          </div>
        </>
      )}
      {locked && documents.length > 0 && (
        <small>
          После начала обработки источники зафиксированы. Для другой исходной
          базы создайте новую сессию.
        </small>
      )}
      {preview && (
        <DocumentPreview document={preview} onClose={() => setPreview(null)} />
      )}{" "}
      {removing && (
        <Modal
          title="Удалить исходный файл?"
          onClose={() => !w.busy && setRemoving(null)}
        >
          <p>{removing.filename}</p>
          <div className="modal-actions">
            <button className="btn" onClick={() => setRemoving(null)}>
              Отмена
            </button>
            <button
              className="btn-danger"
              disabled={w.busy}
              onClick={async () => {
                if (
                  await w.act(
                    () => api.deleteDocument(w.sid, removing.id),
                    "Файл удалён",
                  )
                )
                  setRemoving(null);
              }}
            >
              Удалить
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
