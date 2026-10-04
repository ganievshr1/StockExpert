import { useEffect, useState } from "react";
import { Globe, Server, RefreshCw } from "lucide-react";
import { api } from "../services/api";
import { useWorkspace } from "../state/Workspace";
import type { LLMModels } from "../types";

const statusLabels: Record<string, string> = {
  installed: "Загружена",
  configured: "Ключ задан",
  key_missing: "Нужен API-ключ",
  not_installed: "Нужно загрузить",
  unreachable: "Ollama недоступна",
  unsupported_version: "Обновите Ollama",
};

export function ModelPicker({
  value,
  onChange,
  disabled = false,
  currentLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  currentLabel?: string;
}) {
  const [catalog, setCatalog] = useState<LLMModels | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    api
      .models()
      .then((data) => {
        if (alive) setCatalog(data);
      })
      .catch((e) => {
        if (alive) setError((e as Error).message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [revision]);
  const selected = catalog?.models.find((m) => m.profile_id === value);
  return (
    <div className="space-y-3">
      <label>
        Модель для обработки сессии
        <select
          aria-label="Модель для обработки сессии"
          value={value}
          disabled={disabled || loading || !catalog}
          onChange={(e) => onChange(e.target.value)}
        >
          {!catalog?.models.some((m) => m.profile_id === value) && (
            <option value={value}>{currentLabel || "Загружаем модели…"}</option>
          )}
          {catalog?.models.map((m) => (
            <option key={m.profile_id} value={m.profile_id}>
              {m.label} ·{" "}
              {statusLabels[m.availability.status] || m.availability.status}
            </option>
          ))}
        </select>
      </label>
      {selected && (
        <div className="text-xs text-slate-400 space-y-2" aria-live="polite">
          <p className="flex gap-2 items-center">
            {selected.external_processing ? (
              <Globe size={15} className="text-amber-400" />
            ) : (
              <Server size={15} className="text-sky-400" />
            )}
            {selected.external_processing
              ? "Текст отправляется во внешний DeepSeek API."
              : "Генерация выполняется в вашей Ollama."}
          </p>
          <p>{selected.availability.message}</p>
          {selected.profile_id === "qwen3-8b" && (
            <p>Рассуждения отключены. Ответ — структурированный JSON.</p>
          )}
          {selected.profile_id === "qwen25-7b-q4" && (
            <p>
              Q4 — сжатые веса с меньшим расходом памяти. Качество нужно
              проверить на ваших данных.
            </p>
          )}
          {selected.profile_id === "qwen25-7b-q5" && (
            <p>
              Q5 — менее сильное сжатие; требуется больше памяти, чем для Q4.
            </p>
          )}
          {selected.profile_id === "qwen25-14b" && (
            <p>
              Резервная модель большего размера. Требует больше памяти и
              времени.
            </p>
          )}
          {!selected.availability.available && selected.pull_command && (
            <details>
              <summary className="cursor-pointer text-sky-300">
                Команда загрузки для администратора
              </summary>
              <code className="block break-all mt-2">
                docker compose exec ollama {selected.pull_command}
              </code>
            </details>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-rose-300">
          Не удалось проверить модели: {error}
        </p>
      )}
      <button
        type="button"
        className="btn !text-xs"
        disabled={loading}
        onClick={() => setRevision((r) => r + 1)}
      >
        <RefreshCw size={13} />
        {loading ? "Проверяем модели…" : "Проверить доступность"}
      </button>
    </div>
  );
}

export function SessionModelPanel() {
  const w = useWorkspace();
  const session = w.data?.session;
  if (!session) return null;
  const model = session.llm_config || w.config?.default_llm_config;
  if (!model) return null;
  const jobModel = w.data?.job.params?.llm_config;
  return (
    <section className="panel space-y-4">
      <div>
        <h2 className="font-semibold mb-2">Модель анализа</h2>
        <p className="text-xs text-slate-400">
          Выбор применяется к подготовке схемы, разбору потребности, извлечению
          характеристик и классификации.
        </p>
      </div>
      <ModelPicker
        value={model.profile_id}
        currentLabel={model.label}
        disabled={!w.canEdit || w.busy || w.active || session.llm_locked}
        onChange={(profile) => {
          void w.act(
            () => api.selectModel(w.sid, profile),
            "Модель сессии сохранена",
          );
        }}
      />
      <p className="text-xs text-slate-500 break-all">
        Выбрано: {model.model}
        {model.quantization ? ` · ${model.quantization}` : ""}.
      </p>
      {session.llm_locked ? (
        <p className="text-xs text-slate-400">
          Модель зафиксирована после начала обработки. Для сравнения создайте
          новую сессию и выберите другую модель. Можно взять исходные файлы из
          готовой базы: при смене модели они будут обработаны заново.
        </p>
      ) : (
        <p className="text-xs text-slate-400">
          До первого запуска модель можно изменить. Недоступную модель можно
          выбрать заранее, но для запуска потребуется её настроить.
        </p>
      )}
      {session.llm_legacy && (
        <p className="text-xs text-amber-200">
          Историческая сессия: полный набор настроек модели раньше не
          сохранялся. Следующий запуск зафиксирует доступную конфигурацию.
        </p>
      )}
      {jobModel && (
        <p className="text-xs text-slate-400">
          Модель последнего задания: {jobModel.label} ({jobModel.model}).
        </p>
      )}
      <p className="text-xs text-slate-500">
        Поиск использует отдельную модель эмбеддингов и ранжировщик; объяснения
        строятся по проверенным правилам.
      </p>
    </section>
  );
}
