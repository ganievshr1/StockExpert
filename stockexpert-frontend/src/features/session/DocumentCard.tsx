import { ScrollArea } from "../../components/ScrollArea";
import { useEffect, useState, type DragEvent } from "react";
import { useWorkspace } from "../../state/Workspace";
import { api, type RequirementMode } from "../../services/api";
import type { DocumentRecord } from "../../types";
import { Icon } from "../../components/Icon";
import { Modal } from "../../components/ui";
import { excelLetter } from "./MappingEditor";
type SheetPreview = {
  name: string;
  rows: number;
  preview: { row_index: number; cells: Record<string, string> }[];
};
function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () =>
      reject(new Error(`Не удалось прочитать ${file.name}`));
    reader.readAsText(file);
  });
}
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
  const [files, setFiles] = useState<File[]>([]),
    [requirementMode, setRequirementMode] = useState<RequirementMode>("auto"),
    [positionPreview, setPositionPreview] = useState<{
      count: number;
      error?: string;
    } | null>(null),
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
  useEffect(() => {
    setFiles([]);
    setReplace(undefined);
    setRequirementMode("auto");
    setInputKey((value) => value + 1);
  }, [w.sid]);
  useEffect(() => {
    let current = true;
    setPositionPreview(null);
    if (!isRequirement || requirementMode !== "lines" || !files.length) return;
    if (files.some((file) => !file.name.toLowerCase().endsWith(".txt"))) {
      setPositionPreview({
        count: 0,
        error: "Для списка позиций выберите только TXT-файлы.",
      });
      return;
    }
    if (files.some((file) => file.size > 20 * 1024 * 1024)) {
      setPositionPreview({
        count: 0,
        error: "Размер каждого файла должен быть не более 20 МБ.",
      });
      return;
    }
    void Promise.all(files.map(readText))
      .then((texts) => {
        if (!current) return;
        const counts = texts.map(
          (text) =>
            text.split(/\r\n|\r|\n/).filter((line) => line.trim()).length,
        );
        setPositionPreview({
          count: counts.reduce((total, count) => total + count, 0),
          ...(counts.some((count) => count === 0)
            ? { error: "В одном из выбранных файлов нет непустых строк." }
            : {}),
        });
      })
      .catch((error) => {
        if (current)
          setPositionPreview({ count: 0, error: (error as Error).message });
      });
    return () => {
      current = false;
    };
  }, [files, isRequirement, requirementMode]);
  function selectFiles(next: File[]) {
    setFiles(isRequirement && !replace ? next : next.slice(0, 1));
  }
  function drop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (!locked) selectFiles(Array.from(event.dataTransfer.files));
  }
  async function upload() {
    if (!files.length || locked) return;
    const selected = [...files];
    for (const file of selected) {
      if (file.size > 20 * 1024 * 1024)
        throw Error(`${file.name}: размер файла превышает 20 МБ`);
      if (
        isRequirement &&
        requirementMode === "lines" &&
        !file.name.toLowerCase().endsWith(".txt")
      )
        throw Error("Для списка позиций выберите только TXT-файлы.");
    }
    let completed = 0;
    try {
      for (const file of selected) {
        if (replace)
          await api.replaceDocument(
            w.sid,
            replace,
            file,
            isRequirement ? requirementMode : undefined,
          );
        else
          await api.upload(
            w.sid,
            kind,
            file,
            isRequirement ? requirementMode : undefined,
          );
        completed += 1;
      }
    } catch (error) {
      setFiles(selected.slice(completed));
      setInputKey((value) => value + 1);
      let refreshError = "";
      try {
        await w.refresh();
      } catch {
        refreshError =
          " Не удалось обновить список; обновите страницу перед повтором.";
      }
      throw Error(
        `Загружено файлов: ${completed} из ${selected.length}. Ошибка в ${selected[completed].name}: ${(error as Error).message}. Неотправленные файлы оставлены для повторной загрузки.${refreshError}`,
      );
    }
    setFiles([]);
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
          ? "Что планируется закупить — один опросный лист или список отдельных позиций"
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
              {isRequirement && (
                <span className="block">
                  {document.meta.requirement_mode === "lines"
                    ? `Список: одна строка = одна позиция${typeof document.meta.requirement_positions === "number" ? ` · ${document.meta.requirement_positions} позиций` : ""}`
                    : "Опросный лист — обычный разбор"}
                </span>
              )}
            </small>
          </div>
          <div className="row wrap">
            <button
              className="btn"
              disabled={locked}
              onClick={() => {
                setReplace(document.id);
                setFiles([]);
                setRequirementMode(
                  document.meta.requirement_mode === "lines" ? "lines" : "auto",
                );
                setInputKey((value) => value + 1);
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
      {(isRequirement || !documents.length || replace) && (
        <>
          {isRequirement && (
            <label>
              Формат потребности
              <select
                value={requirementMode}
                disabled={locked}
                onChange={(event) =>
                  setRequirementMode(event.target.value as RequirementMode)
                }
              >
                <option value="auto">Опросный лист — обычный разбор</option>
                <option value="lines">
                  Список позиций — одна строка = одна позиция
                </option>
              </select>
            </label>
          )}
          {isRequirement && (
            <small>
              {requirementMode === "lines"
                ? "Каждая непустая строка TXT — отдельная позиция. Параметры одной позиции должны быть в одной строке."
                : "Многострочный опросный лист обрабатывается обычным способом. Для списка изделий выберите режим «одна строка = одна позиция»."}
            </small>
          )}
          <label
            className={`file-drop ${dragging ? "dragging" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              if (!locked) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={drop}
          >
            <span>
              {files.length
                ? files.map((file) => file.name).join(", ")
                : "Перетащите файл сюда или выберите его"}
            </span>
            <input
              key={inputKey}
              aria-label={
                isRequirement ? "Опросный лист TXT" : "База остатков Excel"
              }
              type="file"
              multiple={isRequirement && !replace}
              accept={
                isRequirement
                  ? requirementMode === "lines"
                    ? ".txt"
                    : ".txt,.xlsx,.docx,.pdf"
                  : ".xlsx"
              }
              disabled={locked}
              onChange={(event) =>
                selectFiles(Array.from(event.target.files || []))
              }
            />
            <small>
              До 20 МБ ·{" "}
              {isRequirement
                ? requirementMode === "lines"
                  ? "TXT, одна позиция в каждой строке"
                  : "TXT; также поддерживаются XLSX, DOCX и текстовый PDF"
                : "XLSX"}
            </small>
          </label>
          {isRequirement && !replace && (
            <small>
              Можно добавить несколько файлов. Загрузите все потребности до
              начала обработки сессии.
            </small>
          )}
          {isRequirement && requirementMode === "lines" && files.length > 0 && (
            <p role={positionPreview?.error ? "alert" : "status"}>
              {positionPreview?.error ||
                (positionPreview
                  ? `Позиций к загрузке: ${positionPreview.count}. Пустые строки пропускаются.`
                  : "Подсчёт позиций…")}
            </p>
          )}
          <div className="row">
            <button
              className="btn-primary"
              disabled={
                locked ||
                !files.length ||
                (isRequirement &&
                  requirementMode === "lines" &&
                  (!positionPreview || !!positionPreview.error))
              }
              onClick={() =>
                void w.act(
                  upload,
                  replace
                    ? "Файл заменён"
                    : files.length > 1
                      ? "Файлы загружены"
                      : "Файл загружен",
                )
              }
            >
              {replace
                ? "Сохранить замену"
                : files.length > 1
                  ? `Загрузить файлы (${files.length})`
                  : "Загрузить файл"}
            </button>
            {replace && (
              <button
                className="btn-ghost"
                onClick={() => {
                  setReplace(undefined);
                  setFiles([]);
                  setInputKey((value) => value + 1);
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
