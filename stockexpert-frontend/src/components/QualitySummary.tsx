import { useEffect, useState } from "react";
import { qualityApi, percent, type Quality } from "../services/quality";
import { useWorkspace } from "../state/Workspace";
export function QualityCards({ q }: { q: Quality }) {
  const c = q.coverage;
  return (
    <section className="panel">
      <h2 className="font-semibold mb-3">Качество по экспертной проверке</h2>
      <p className="text-xs text-slate-400 mb-4">
        {q.finalized
          ? "Проверка завершена"
          : "Разметка не завершена — итоговые метрики пока не рассчитаны"}
        . Проверено пар: {c.pairs_reviewed || 0} / {c.pairs_total || 0};
        shortlist: {c.shortlist_reviewed || 0} / {c.shortlist_total || 0};
        паспортов: {c.passports_reviewed || 0} / {c.passports_total || 0}.
      </p>
      <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3">
        {[
          ["H1 · F1 текста", q.text_f1],
          ["Accuracy категорий", q.accuracy],
          ["Precision shortlist", q.precision_shortlist],
          ["Recall@150", q.recall_at_150],
        ].map(([label, value]) => (
          <div className="p-4 rounded-lg bg-slate-800" key={String(label)}>
            <p className="text-xs text-slate-400 mb-2">{label}</p>
            <b>{percent(value as number | null)}</b>
          </div>
        ))}
      </div>
      <p className="text-xs text-slate-500 mt-4">
        Проверено текстов МТР: {c.text_reviewed || 0} / {c.stock_total || 0}. F1 технических параметров отдельно: {percent(q.f1)}.{" "}
        Проверено значений: {c.fields_checked || 0}; категорий:{" "}
        {c.categories_checked || 0}. Полностью проверено потребностей для
        Recall: {c.requirements_fully_reviewed || 0} /{" "}
        {c.requirements_total || 0}. Для Recall проверяйте все пары запуска,
        включая позиции вне Top 150. Непроверенные позиции не считаются
        ошибками. Метрики описывают размеченную выборку.
      </p>
    </section>
  );
}
export function QualitySummary() {
  const w = useWorkspace();
  const [q, setQ] = useState<Quality | null>(null);
  useEffect(() => {
    let live = true;
    if (w.sid && !w.active)
      void qualityApi
        .summary(w.sid)
        .then((x) => {
          if (live) setQ(x);
        })
        .catch(() => {
          if (live) setQ(null);
        });
    else setQ(null);
    return () => {
      live = false;
    };
  }, [w.sid, w.active, w.data]);
  if (!q?.job_id) return null;
  return (
    <div className="space-y-3">
      <QualityCards q={q} />
      <button className="btn" onClick={() => w.setTab("quality")}>
        Открыть экспертную проверку
      </button>
    </div>
  );
}
