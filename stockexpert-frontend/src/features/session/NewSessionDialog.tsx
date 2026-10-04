import { useEffect, useState, type FormEvent } from "react";
import { useWorkspace } from "../../state/Workspace";
import { api } from "../../services/api";
import type { NormalizedBase } from "../../types";
import { ModelPicker } from "../../components/ModelPicker";
import { ErrorBox, Modal, Notice } from "../../components/ui";
export function NewSessionDialog({ onClose }: { onClose: () => void }) {
  const w = useWorkspace();
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
      await w.create(title, mode === "reuse" ? selected : undefined, profile);
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
        <ModelPicker value={profile} onChange={setProfile} disabled={pending} />
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
              При совпадении модели паспорта используются повторно. Если выбрана
              другая модель, исходные строки будут обработаны заново.
            </small>
          </>
        )}
        <Notice title="Два источника для проверки">
          {mode === "new"
            ? "На следующем шаге загрузите базу остатков Excel (.xlsx) и опросный лист (.txt)."
            : "На следующем шаге загрузите новый опросный лист (.txt). База будет взята из выбранной сессии."}
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
