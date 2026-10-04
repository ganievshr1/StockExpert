import { ScrollArea } from "../components/ScrollArea";
import { useWorkspace } from "../state/Workspace";
import { api } from "../services/api";
import { workspaceApi } from "../services/workspace";
import { useResource } from "../hooks/useResource";
import {
  Empty,
  ErrorBox,
  Loading,
  Notice,
  PageTitle,
  percentage,
  seconds,
} from "../components/ui";
export function MetricsPage() {
  const w = useWorkspace();
  const result = useResource(
    () => workspaceApi.measurement(w.sid),
    [w.sid, w.data],
    !!w.sid,
  );
  if (!w.sid) return <Empty />;
  if (result.error) return <ErrorBox message={result.error} />;
  if (!result.data) return <Loading />;
  const data = result.data,
    e = data.efficiency,
    q = data.quality,
    c = q.coverage;
  return (
    <div className="section-gap">
      <PageTitle
        eyebrow="Оценка результата"
        title="Качество и метрики"
        description={`Срез по текущей сессии · ${new Date(data.generated_at).toLocaleString("ru-RU")}`}
        actions={
          <>
            <button className="btn" onClick={result.refresh}>
              Обновить
            </button>
            <button
              className="btn-primary"
              disabled={w.busy}
              onClick={() =>
                void w.act(() => api.download(w.sid, "metrics"), "Excel скачан")
              }
            >
              Экспорт Excel
            </button>
          </>
        }
      />
      <section className="panel stack">
        <div className="row between wrap">
          <div className="row">
            <strong>{w.data?.session.llm_config.label}</strong>
            <span className="badge info">
              {w.data?.session.llm_config.provider === "ollama"
                ? "Локальная модель"
                : "Внешний API"}
            </span>
          </div>
          <small>Расчёт {e.job_id?.slice(0, 8) || "не завершён"}</small>
        </div>
        <div className="metric-grid">
          {[
            ["Время подбора", seconds(e.latest_job_seconds)],
            [
              "Повторно использовано",
              `${e.reused_passports} / ${e.stock_rows}`,
            ],
            [
              "Потребностей с кандидатами",
              `${e.requirements_with_candidates} / ${e.requirements}`,
            ],
            ["Сокращение кандидатов", percentage(e.reduction_rate)],
          ].map(([title, value]) => (
            <div key={title}>
              <small>{title}</small>
              <h2 style={{ marginTop: 8 }}>{value}</h2>
            </div>
          ))}
        </div>
      </section>
      <div className="metric-grid three">
        {data.targets.map((target) => {
          const measured = target.value !== null,
            passed = measured && target.value! >= target.target;
          return (
            <section className="panel stack" key={target.metric}>
              <h3>
                {target.metric === "F1"
                  ? "F1 извлечения"
                  : target.metric === "Accuracy"
                    ? "Точность категорий"
                    : target.label}
              </h3>
              <strong className={measured ? "metric-value" : ""}>
                {percentage(target.value)}
              </strong>
              <div className="row between wrap">
                <small>Цель ≥ {percentage(target.target)}</small>
                {measured && (
                  <span className={`badge ${passed ? "success" : "warning"}`}>
                    {passed ? "В норме" : "Ниже цели"}
                  </span>
                )}
              </div>
              <div className={`metric-target ${passed ? "" : "warning"}`}>
                <span style={{ width: `${(target.value || 0) * 100}%` }} />
              </div>
              <small>
                {!measured
                  ? target.reason
                  : passed
                    ? "Цель достигнута"
                    : `Ниже цели на ${((target.target - target.value!) * 100).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} п.п.`}
              </small>
            </section>
          );
        })}
      </div>
      <section className="panel row between wrap">
        <p>
          Precision shortlist ·{" "}
          <strong>{percentage(q.precision_shortlist)}</strong>
        </p>
        <small>
          Пары: {c.pairs_reviewed || 0}/{c.pairs_total || 0} · Shortlist:{" "}
          {c.shortlist_reviewed || 0}/{c.shortlist_total || 0} · Паспорта:{" "}
          {c.passports_reviewed || 0}/{c.passports_total || 0}
        </small>
      </section>
      <section className="panel stack">
        <div className="row between">
          <h3>Извлечение параметров</h3>
          <button className="btn-ghost" onClick={() => w.setTab("quality")}>
            Перейти к проверке
          </button>
        </div>
        <p className="muted">
          Нормализовано {data.extraction.normalized_rows} из{" "}
          {data.extraction.eligible_rows} строк · Заполненность{" "}
          {percentage(data.extraction.fill_rate)} · С цитатой{" "}
          {percentage(data.extraction.evidence_rate)}
        </p>
        <ScrollArea label="Метрики обработки">
          <table>
            <thead>
              <tr>
                <th>Параметр</th>
                <th>Заполнено</th>
                <th>С цитатой</th>
                <th>Пропуски</th>
                <th>Покрытие</th>
              </tr>
            </thead>
            <tbody>
              {data.extraction.fields.map((field) => (
                <tr key={field.key}>
                  <td>{field.label}</td>
                  <td>{field.filled}</td>
                  <td>{field.confirmed}</td>
                  <td>{field.missing}</td>
                  <td>{percentage(field.fill_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollArea>
        <small>
          Заполненность и наличие цитат не оценивают правильность извлечения.
        </small>
      </section>
      <details className="panel">
        <summary>Экспертная оценка по каждому параметру</summary>
        <div className="stack" style={{ marginTop: 20 }}>
          <p className="muted">
            {q.finalized
              ? "Проверка завершена"
              : "Для итоговых метрик завершите экспертную проверку"}
            . Полностью проверено потребностей:{" "}
            {c.requirements_fully_reviewed || 0} / {c.requirements_total || 0}.
          </p>
          <ScrollArea label="Метрики обработки">
            <table>
              <thead>
                <tr>
                  <th>Параметр</th>
                  <th>Проверено</th>
                  <th>TP</th>
                  <th>FP</th>
                  <th>FN</th>
                  <th>F1</th>
                </tr>
              </thead>
              <tbody>
                {q.fields.map((field) => (
                  <tr key={field.key}>
                    <td>
                      {w.data?.session.schema_doc.features?.find(
                        (value) => value.key === field.key,
                      )?.label || field.key}
                    </td>
                    <td>{field.checked}</td>
                    <td>{field.tp}</td>
                    <td>{field.fp}</td>
                    <td>{field.fn}</td>
                    <td>{percentage(field.f1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollArea>
          <small>
            TP — верное значение; FP — лишнее или неверное значение модели; FN —
            пропущенное или неверно извлечённое значение источника. Ошибочное
            значение даёт и FP, и FN.
          </small>
          <small>{q.note}</small>
        </div>
      </details>
      <details className="panel">
        <summary>Эффективность, категории и сжатие кандидатов</summary>
        <div className="stack" style={{ marginTop: 20 }}>
          <p>
            Доступно запасов: {e.available_stock_rows}; использовано или
            удалено: {e.unavailable_stock_rows}. Новых паспортов остатков:{" "}
            {e.new_stock_passports ?? "—"}; потребностей:{" "}
            {e.new_requirement_passports ?? "—"}.
          </p>
          <p>
            Векторов создано: {e.vectors_created ?? "—"}; повторно использовано:{" "}
            {e.vectors_reused ?? "—"}. Сжатие:{" "}
            {e.compression_factor?.toFixed(2) ?? "—"}×. Доля противоречий:{" "}
            {percentage(e.contradiction_rate)}.
          </p>
          <p>
            Категория присвоена: {percentage(data.taxonomy.coverage)}. Проверки
            правил пройдены в Top 150: {e.known_rules_top150}.
          </p>
          <div className="row wrap">
            {data.taxonomy.categories.map((category) => (
              <span className="badge" key={category.id}>
                {category.label}: {category.count}
              </span>
            ))}
          </div>
          <ScrollArea label="Метрики обработки">
            <table>
              <thead>
                <tr>
                  <th>Потребность</th>
                  <th>До отбора</th>
                  <th>Найдено</th>
                  <th>Top 150</th>
                  <th>Shortlist</th>
                  <th>Сжатие</th>
                </tr>
              </thead>
              <tbody>
                {data.matching.requirements.map((row) => (
                  <tr key={row.requirement_id}>
                    <td>{row.requirement_id.slice(0, 8)}</td>
                    <td>{row.before}</td>
                    <td>{row.retrieved}</td>
                    <td>{row.top150}</td>
                    <td>{row.shortlisted}</td>
                    <td>{row.compression_factor?.toFixed(2) ?? "—"}×</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollArea>
          <small>
            Наличие кандидатов и сжатие списка не доказывают правильность
            подбора. Учёт новых паспортов доступен для запусков версии 3.3 и
            новее.
          </small>
        </div>
      </details>
      <details className="panel">
        <summary>Время этапов, задания и попытки</summary>
        <div className="stack" style={{ marginTop: 20 }}>
          <p>
            Всего по сессии:{" "}
            {data.timing.measured
              ? seconds(data.timing.processing_seconds)
              : "Нет замеров"}
            .
          </p>
          <ScrollArea label="Метрики обработки">
            <table>
              <thead>
                <tr>
                  <th>Этап</th>
                  <th>Время</th>
                </tr>
              </thead>
              <tbody>
                {data.timing.stages.map((stage) => (
                  <tr key={stage.stage}>
                    <td>{stage.label}</td>
                    <td>{seconds(stage.seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollArea>
          <ScrollArea label="Метрики обработки">
            <table>
              <thead>
                <tr>
                  <th>Задание / попытка</th>
                  <th>Этап</th>
                  <th>Время</th>
                  <th>Исход</th>
                </tr>
              </thead>
              <tbody>
                {data.timing.segments.map((segment) => (
                  <tr key={segment.id}>
                    <td className="small">
                      {segment.job_id}
                      <br />
                      {segment.attempt}
                    </td>
                    <td>{segment.label}</td>
                    <td>{seconds(segment.seconds)}</td>
                    <td>{segment.outcome}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollArea>
          <small>
            Время включает повторные, неудачные и отменённые попытки, но не
            ожидание в очереди. Параллельные запросы не суммируются.
          </small>
        </div>
      </details>
      {!q.finalized && (
        <Notice
          title="Метрики качества требуют завершённой экспертной проверки"
          tone="warning"
        >
          Проверяйте параметры, категории и пары, затем нажмите «Завершить
          экспертную проверку». Для Recall@150 проверьте все пары выбранных
          потребностей.
        </Notice>
      )}
    </div>
  );
}
