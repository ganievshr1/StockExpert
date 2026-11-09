import { useEffect, useState, type FormEvent } from "react";
import { useWorkspace } from "../../state/Workspace";
import { api } from "../../services/api";
import type { NormalizedBase } from "../../types";
import { ModelPicker } from "../../components/ModelPicker";
import { NormalizationPicker } from "../../components/NormalizationPicker";
import { TaxonomyModePicker } from "../../components/TaxonomyModePicker";
import { ErrorBox, Modal, Notice } from "../../components/ui";
export function NewSessionDialog({ onClose }: { onClose: () => void }) {
  const w = useWorkspace();
  const [method, setMethod] = useState<"llm" | "regex">(
    w.config?.default_normalization_method || "regex",
  );
  const [taxonomyEnabled, setTaxonomyEnabled] = useState(false);
  const [profile, setProfile] = useState(
    w.config?.default_llm_config?.profile_id || "deepseek",
  );
  const [bases, setBases] = useState<NormalizedBase[]>([]),
    [mode, setMode] = useState<"new" | "reuse">("new"),
    [selected, setSelected] = useState(""),
    [pending, setPending] = useState(false),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [offset, setOffset] = useState(0),
    [hasMore, setHasMore] = useState(false);
  const load = async (next = 0) => {
    setLoading(true);
    try {
      const rows = await api.bases(next);
      setBases((previous) => (next ? [...previous, ...rows] : rows));
      setOffset(next);
      setHasMore(rows.length === 100);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const title = String(new FormData(event.currentTarget).get("title")).trim();
    if (!title) {
      setError("Введите название сессии");
      return;
    }
    setPending(true);
    setError("");
    try {
      await w.create(
        title,
        mode === "reuse" ? selected : undefined,
        profile,
        method,
        taxonomyEnabled,
      );
      w.setTab("data");
      onClose();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(false);
    }
  }
  return (
    <Modal
      title="Новая закупочная сессия"
      onClose={() => !pending && onClose()}
    >
      <form onSubmit={submit} className="stack">
        <p className="muted">
          Объедините потребность и складскую базу
          <br />
          для предзакупочной проверки.
        </p>
        <label>
          Название сессии
          <input
            name="title"
            placeholder="МТО — Кабель силовой Q4"
            required
            maxLength={255}
            autoFocus
          />
        </label>
        <NormalizationPicker
          value={method}
          onChange={setMethod}
          disabled={pending}
        />
        <TaxonomyModePicker value={taxonomyEnabled} onChange={setTaxonomyEnabled} disabled={pending}/>
        {(taxonomyEnabled || method === "llm") && <ModelPicker
          value={profile}
          onChange={setProfile}
          disabled={pending}
          label={
            method === "regex"
              ? "Модель H2 — классификация паспортов"
              : mode === "reuse"
                ? "Модель для нового опросного листа"
                : undefined
          }
        />}
        <div className="stack">
          <label>Складская база</label>
          <div className="tabs">
            <button
              type="button"
              className={mode === "new" ? "active" : ""}
              onClick={() => setMode("new")}
            >
              Загрузить новый Excel
            </button>
            <button
              type="button"
              className={mode === "reuse" ? "active" : ""}
              onClick={() => setMode("reuse")}
            >
              Использовать готовую базу
            </button>
          </div>
        </div>
        {mode === "reuse" && (
          <>
            <label>
              Готовая база
              <select
                value={selected}
                required
                onChange={(event) => setSelected(event.target.value)}
              >
                <option value="">
                  {loading ? "Загружаем…" : "Выберите базу"}
                </option>
                {bases.map((base) => (
                  <option key={base.session_id} value={base.session_id}>
                    {base.title} · {base.normalized_rows} позиций
                  </option>
                ))}
              </select>
            </label>
            {hasMore && (
              <button
                type="button"
                className="btn"
                disabled={loading}
                onClick={() => void load(offset + 100)}
              >
                Ещё базы
              </button>
            )}
            <small>
              Нормализованные значения берутся из готовой базы без
              повторной обработки, даже при выборе другой модели или метода H1.
              {taxonomyEnabled ? " Категории используются в режиме H2." : " Категории в режиме без H2 не используются."}{" "}
              Новый метод применяется к опросному листу. Для сравнения H1 на
              складе выберите «Загрузить новый Excel».
            </small>
          </>
        )}
        <Notice title="Два источника для проверки">
          {mode === "new"
            ? "На следующем шаге загрузите базу остатков Excel (.xlsx) и опросный лист (.txt)."
            : "Загрузите только новый опросный лист (.txt) и запустите сопоставление с готовой базой."}
        </Notice>
        {error && <ErrorBox message={error} />}
        <div className="modal-actions">
          <button
            type="button"
            className="btn"
            onClick={onClose}
            disabled={pending}
          >
            Отмена
          </button>
          <button
            className="btn-primary"
            disabled={pending || (mode === "reuse" && !selected)}
          >
            {pending ? "Создаём…" : "Создать и загрузить файлы"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
