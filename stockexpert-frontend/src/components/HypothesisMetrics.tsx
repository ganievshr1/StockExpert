import { percentage } from "./ui";

export interface Hypothesis {
  id: string;
  label: string;
  target: number;
  value: number | null;
  formula: string;
  numerator: number | null;
  denominator: number;
  substitution: string | null;
  scope: string;
  reason: string;
  diagnostic?: boolean;
}
export interface H3Population {
  catalog_rows: number;
  extra_rows: number;
  requirements: number;
  pairs_total: number;
  pairs_reviewed: number;
  relevant: number;
  unknown: number;
  hits_150: number | null;
  top150: number | null;
  retained_share: number | null;
  reduction_rate: number | null;
}
export function MetricCalculation({ metric }: { metric: Hypothesis }) {
  const measured = metric.value !== null;
  // Saved H2 revisions predate the closed-test experiment. Their mapped
  // cluster score must never be presented as confirmation of the hypothesis.
  const diagnostic = metric.diagnostic || metric.id === 'H2';
  return (
    <div className="stack hypothesis-calculation">
      <strong className="metric-value">{percentage(metric.value)}</strong>
      <small>{metric.formula}</small>
      {metric.substitution && (
        <p>
          <strong>
            {metric.substitution} = {percentage(metric.value)}
          </strong>
        </p>
      )}
      <small>{metric.scope}</small>
      {metric.reason && <p className="muted">{metric.reason}</p>}
      <div className="row wrap">
        <small>Цель ≥ {percentage(metric.target)}</small>
        {measured && !diagnostic && (
          <span
            className={`badge ${metric.value! >= metric.target ? "success" : "warning"}`}
          >
            {metric.value! >= metric.target ? "Порог достигнут" : "Ниже порога"}
          </span>
        )}
        {measured && diagnostic && <small>Оценка сохранённого запуска. Для проверки H2 нужен закрытый эксперимент и сравнение с базовым методом.</small>}
      </div>
    </div>
  );
}
export function HypothesisMetrics({
  metrics,
  population,
}: {
  metrics: Hypothesis[];
  population?: H3Population;
}) {
  return (
    <div className="stack">
      <div className="metric-grid three">
        {metrics.map((metric) => (
          <section className="panel stack" key={metric.id}>
            <h3>
              {metric.id} · {metric.label}
            </h3>
            <MetricCalculation metric={metric} />
          </section>
        ))}
      </div>
      {population && (
        <section className="panel stack">
          <h3>H3 · Отбор из общего количества строк</h3>
          <p>
            База: <strong>{population.catalog_rows} строк</strong> ·
            Потребностей: {population.requirements} · Проверено пар:{" "}
            {population.pairs_reviewed} / {population.pairs_total} · Подходящих
            во всей базе: {population.relevant}.
          </p>
          {population.top150 !== null && (
            <p>
              В Top 150:{" "}
              <strong>
                {population.top150} / {population.pairs_total} ={" "}
                {percentage(population.retained_share)}
              </strong>{" "}
              всех пар. Сокращение:{" "}
              <strong>{percentage(population.reduction_rate)}</strong>. Найдено
              подходящих: {population.hits_150} / {population.relevant}.
            </p>
          )}
          {!!population.extra_rows && (
            <p>
              Вне эталона ещё {population.extra_rows} нормализованных строк
              сессии: для расчёта по всей базе нужна их разметка.
            </p>
          )}
          <small>
            Для одной потребности знаменатель доли отбора — все строки базы. Для
            нескольких — строки × потребности. Recall@150 измеряет сохранение
            подходящих позиций, доля отбора — размер списка кандидатов.
          </small>
        </section>
      )}
    </div>
  );
}
