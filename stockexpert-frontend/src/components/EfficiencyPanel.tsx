import { useEffect, useState } from "react";
import { request, api } from "../services/api";
import { useWorkspace } from "../state/Workspace";
import { ErrorBox } from "./ui";
interface Efficiency {
  available_stock_rows: number;
  unavailable_stock_rows: number;
  available_shortlisted_pairs: number;
  ready: boolean;
  job_id: string | null;
  stock_rows: number;
  reused_passports: number;
  reuse_rate: number | null;
  new_stock_passports: number | null;
  new_requirement_passports: number | null;
  requirements: number;
  requirements_with_candidates: number;
  requirement_coverage: number | null;
  pairs_before: number;
  pairs_top150: number;
  shortlisted_pairs: number;
  reduction_rate: number | null;
  compression_factor: number | null;
  vectors_reused: number | null;
  vectors_created: number | null;
  latest_job_seconds: number | null;
  session_processing_seconds: number | null;
  contradiction_rate: number | null;
  known_rules_top150: number;
}
const pct = (v: number | null) =>
  v === null
    ? "—"
    : `${(v * 100).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
const sec = (v: number | null) =>
  v === null
    ? "—"
    : `${v.toLocaleString("ru-RU", { maximumFractionDigits: v < 1 ? 3 : 1 })} с`;
export function EfficiencyPanel() {
  const w = useWorkspace();
  const [data, setData] = useState<Efficiency | null>(null),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0);
  const status = w.data?.job.status;
  useEffect(() => {
    let alive = true;
    if (!w.sid || w.active) {
      setData(null);
      return;
    }
    setError("");
    void request<{ efficiency: Efficiency }>(`/metrics/${w.sid}`)
      .then((r) => {
        if (alive) setData(r.efficiency);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [w.sid, w.data?.job.id, status, w.active, revision, w.data]);
  if (error)
    return (
      <div>
        <ErrorBox message={error} />
        <button className="btn mt-2" onClick={() => setRevision((x) => x + 1)}>
          Обновить метрики
        </button>
      </div>
    );
  if (!data?.ready) return null;
  return (
    <details open className="panel !border-emerald-500/30">
      <summary className="cursor-pointer font-semibold text-emerald-300">
        Результаты и эффективность сессии
      </summary>
      <p className="text-xs text-slate-500 my-4 break-all">
        Последний завершённый подбор · {data.job_id}
        {status !== "COMPLETED"
          ? " · показан предыдущий успешный результат"
          : ""}
      </p>
      <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {[
          ["Время последнего подбора", sec(data.latest_job_seconds)],
          [
            "Повторно использовано паспортов",
            `${data.reused_passports} / ${data.stock_rows}`,
          ],
          [
            "Потребностей с кандидатами",
            `${data.requirements_with_candidates} / ${data.requirements} (${pct(data.requirement_coverage)})`,
          ],
          ["Сокращение до Top 150", pct(data.reduction_rate)],
        ].map(([label, value]) => (
          <div className="bg-slate-800/70 rounded-xl p-4" key={label}>
            <p className="text-xs text-slate-400 mb-2">{label}</p>
            <p className="text-xl font-semibold">{value}</p>
          </div>
        ))}
      </div>
      <div className="grid sm:grid-cols-2 gap-3 mt-5 text-sm text-slate-300">
        <p>
          Сейчас доступно запасов: {data.available_stock_rows}; использовано или
          удалено: {data.unavailable_stock_rows}; доступно пар shortlist:{" "}
          {data.available_shortlisted_pairs}.
        </p>
        <p>
          Пар до отбора: {data.pairs_before}; Top 150: {data.pairs_top150};
          shortlist: {data.shortlisted_pairs}.
        </p>
        <p>
          Сжатие: {data.compression_factor?.toFixed(2) ?? "—"}×; противоречия:{" "}
          {pct(data.contradiction_rate)}.
        </p>
        <p>
          Новых паспортов в этой сессии: остатки —{" "}
          {data.new_stock_passports ?? "—"}, потребность —{" "}
          {data.new_requirement_passports ?? "—"}.
        </p>
        <p>
          Векторы последнего подбора: использовано повторно —{" "}
          {data.vectors_reused ?? "—"}, создано — {data.vectors_created ?? "—"}.
        </p>
        <p>
          Обработка сессии с повторами: {sec(data.session_processing_seconds)}.
        </p>
        <p>
          Проверены по известным правилам в Top 150: {data.known_rules_top150}.
        </p>
      </div>
      <p className="text-xs text-slate-500 mt-4">
        Показатели рассчитаны автоматически. Наличие кандидатов и сжатие списка
        не доказывают правильность подбора. F1, Accuracy, Precision и Recall@150
        рассчитываются по разметке в разделе «Экспертная проверка» после её
        завершения. Учёт новых паспортов доступен для запусков версии 3.3.
      </p>
      <div className="flex flex-wrap gap-3 mt-5">
        <button className="btn" onClick={() => w.setTab("metrics")}>
          Все метрики и время этапов
        </button>
        <button
          className="btn"
          onClick={() =>
            void w.act(() => api.download(w.sid, "metrics"), "Excel скачан")
          }
        >
          Скачать показатели Excel
        </button>
      </div>
    </details>
  );
}
