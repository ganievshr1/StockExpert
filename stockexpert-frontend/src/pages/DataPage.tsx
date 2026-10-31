import { useState } from "react";
import { useWorkspace } from "../state/Workspace";
import { api } from "../services/api";
import { DocumentCard } from "../features/session/DocumentCard";
import { MappingEditor } from "../features/session/MappingEditor";
import { SessionControls } from "../features/session/SessionControls";
import { SessionModelPanel } from "../components/ModelPicker";
import { Stepper } from "../components/Stepper";
import { SessionTaxonomyPanel } from "../components/TaxonomyModePicker";
import { Empty, Loading, Modal, Notice, PageTitle } from "../components/ui";
export function DataPage() {
  const w = useWorkspace();
  const [settings, setSettings] = useState(false),
    [controls, setControls] = useState(false);
  if (!w.sid) return <Empty />;
  if (!w.data) return <Loading />;
  const { session, documents } = w.data;
  const source = session.base_snapshot;
  const readyBase =
    !!source?.source_session_id && source.reused_passports !== false;
  const hasFiles = ["residual", "requirement"].every((kind) =>
    documents.some((document) => document.kind === kind),
  );
  const schema = session.schema_doc;
  const regex = session.normalization_method === "regex";
  const h1Ready = session.status === "H1_READY";
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
    } else if (h1Ready) {
      if (
        await w.act(
          () => api.rebuildTaxonomy(w.sid),
          "Классификация H2 поставлена в очередь",
        )
      )
        w.setTab("processing");
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
      ? readyBase
        ? "Сопоставить с готовой базой"
        : "Запустить подбор"
      : schema.taxonomy_status === "review"
        ? "Проверить классификатор"
        : h1Ready
          ? "H2: построить категории"
          : schema.mapping_approved
            ? regex
              ? "H1: нормализовать регулярками"
              : "H1: нормализовать через LLM"
            : schema.mappings
              ? "Утвердите столбцы ниже"
              : "Продолжить";
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow="Подготовка сессии"
        title="Загрузка и проверка данных"
        description={
          readyBase
            ? "Загрузите новый опросный лист TXT. Сопоставление использует уже нормализованные значения складской базы."
            : "Загрузите базу складских остатков Excel и опросный лист с потребностью в TXT."
        }
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
      <SessionTaxonomyPanel />
      {regex && (
        <Notice title="H1 — регулярные выражения на CPU">
          {h1Ready
            ? "Паспорта сохранены в сессии. Можно рассчитать H1 в разделе «Для разработчика» или продолжить классификацию H2."
            : "После утверждения столбцов запустите H1. На этом этапе Qwen/DeepSeek не вызываются. H2 запускается следующим шагом, если он включён."}
        </Notice>
      )}
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
                ? `${source.normalized_rows} нормализованных позиций. Значения и категории используются без повторной нормализации, независимо от выбранной модели.`
                : "Эта сессия создана в прежнем режиме копирования исходных строк. Чтобы использовать готовые паспорта, создайте новую сессию с этой базой."}
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
      {schema.mappings && !readyBase && <MappingEditor />}
      {hasFiles && (
        <Notice
          title={
            readyBase
              ? "Готово к сопоставлению"
              : schema.mapping_approved
                ? "Файлы и столбцы готовы к обработке"
                : "Исходные файлы загружены"
          }
          tone="success"
        >
          {readyBase
            ? "Нажмите «Сопоставить с готовой базой». Будет обработан только новый опросный лист; готовые складские паспорта и классификатор сохранятся."
            : schema.mapping_approved
              ? regex
                ? (session.taxonomy_enabled === false ? "Выполните H1, проверьте параметры и запустите поиск без дерева категорий." : "Выполните H1 регулярками, затем запустите H2 для классификации паспортов.")
                : (session.taxonomy_enabled === false ? "Запустите нормализацию. Затем доступны правила параметров и подбор без H2." : "Выполните H1 через LLM, затем отдельно запустите H2 и проверьте категории.")
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
          H1: {regex ? "Регулярки" : "LLM"} · H2: {session.taxonomy_enabled === false ? "отключён" : "включён"} · Модель:{" "}
          {session.llm_config.label} ·{" "}
          {session.llm_config.provider === "ollama"
            ? "обработка внутри компании"
            : "внешний DeepSeek API"}
        </span>
        <div className="row">
          <button className="btn" onClick={() => setControls(true)}>
            Управление данными
          </button>
          <button className="btn" onClick={() => setSettings(true)}>
            Настройки H1 и H2
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
