import { useState } from "react";
import { useWorkspace } from "../state/Workspace";
import { api } from "../services/api";
import { DocumentCard } from "../features/session/DocumentCard";
import { MappingEditor } from "../features/session/MappingEditor";
import { SessionControls } from "../features/session/SessionControls";
import { SessionModelPanel } from "../components/ModelPicker";
import { Stepper } from "../components/Stepper";
import { Empty, Loading, Modal, Notice, PageTitle } from "../components/ui";
export function DataPage() {
  const w = useWorkspace();
  const [settings, setSettings] = useState(false),
    [controls, setControls] = useState(false);
  if (!w.sid) return <Empty />;
  if (!w.data) return <Loading />;
  const { session, documents } = w.data;
  const source = session.base_snapshot;
  const hasFiles = ["residual", "requirement"].every((kind) =>
    documents.some((document) => document.kind === kind),
  );
  const schema = session.schema_doc;
  const blocked = w.busy || w.active || !w.canEdit;
  async function next() {
    if (w.active) {
      w.setTab("processing");
      return;
    }
    if (session.approved) {
      if (await w.act(() => api.run(w.sid, 150), "Подбор поставлен в очередь"))
        w.setTab("processing");
    } else if (schema.taxonomy_status === "review") {
      w.setTab("taxonomy");
    } else if (schema.mapping_approved) {
      if (
        await w.act(
          () => api.normalize(w.sid),
          "Нормализация поставлена в очередь",
        )
      )
        w.setTab("processing");
    } else if (!schema.mappings) {
      if (
        await w.act(
          () => api.prepare(w.sid),
          "Подготовка столбцов поставлена в очередь",
        )
      )
        w.setTab("processing");
    }
  }
  const label = w.active
    ? "Ход обработки"
    : session.approved
      ? "Запустить подбор"
      : schema.taxonomy_status === "review"
        ? "Проверить классификатор"
        : schema.mapping_approved
          ? "Нормализовать позиции"
          : schema.mappings
            ? "Утвердите столбцы ниже"
            : "Продолжить";
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow="Подготовка сессии"
        title="Загрузка и проверка данных"
        description="Загрузите базу складских остатков Excel и опросный лист с потребностью в TXT."
        actions={
          <button
            className="btn-primary"
            disabled={
              !w.active &&
              (blocked ||
                !hasFiles ||
                (!schema.mapping_approved &&
                  !!schema.mappings &&
                  !session.approved))
            }
            onClick={() => void next()}
          >
            {label}
          </button>
        }
      />
      <Stepper current={0} />
      <div className="split equal">
        {source?.source_session_id ? (
          <section className="panel stack">
            <h3>Готовая складская база</h3>
            <p>{source.source_title}</p>
            <span className="badge success">
              {source.reused_passports !== false
                ? "Паспорта используются повторно"
                : "Исходные строки перенесены"}
            </span>
            <p className="muted">
              {source.reused_passports !== false
                ? `${source.normalized_rows} нормализованных позиций. Повторно загружать Excel не нужно.`
                : "Выбранная модель отличается. Исходные строки будут обработаны заново."}
            </p>
            <small>
              Связанная база: {source.source_session_id.slice(0, 8)}
            </small>
          </section>
        ) : (
          <DocumentCard kind="residuals" />
        )}
        <DocumentCard kind="requirement" />
      </div>
      {schema.mappings && <MappingEditor />}
      {hasFiles && (
        <Notice
          title={
            schema.mapping_approved
              ? "Файлы и столбцы готовы к обработке"
              : "Исходные файлы загружены"
          }
          tone="success"
        >
          {schema.mapping_approved
            ? "Запустите нормализацию, затем проверьте категории и правила сравнения."
            : "Нажмите «Продолжить», чтобы определить столбцы Excel. Опросный лист TXT будет разобран отдельно."}
        </Notice>
      )}
      {schema.taxonomy_status === "review" && (
        <Notice title="Классификатор готов к проверке">
          Все позиции обработаны. Проверьте категории и правила перед запуском
          подбора.
        </Notice>
      )}
      <footer className="page-footer">
        <span>
          Модель: {session.llm_config.label} ·{" "}
          {session.llm_config.provider === "ollama"
            ? "обработка внутри компании"
            : "внешний DeepSeek API"}
        </span>
        <div className="row">
          <button className="btn" onClick={() => setControls(true)}>
            Управление данными
          </button>
          <button className="btn" onClick={() => setSettings(true)}>
            Изменить модель
          </button>
        </div>
      </footer>
      {settings && (
        <Modal title="Модель обработки" onClose={() => setSettings(false)}>
          <SessionModelPanel />
        </Modal>
      )}
      {controls && (
        <Modal
          title="Управление данными и запуском"
          onClose={() => !w.busy && setControls(false)}
        >
          <SessionControls />
        </Modal>
      )}
    </div>
  );
}
