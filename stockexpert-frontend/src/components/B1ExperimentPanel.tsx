import { useEffect, useState } from "react";
import { api } from "../services/api";
import { b1Api, type B1Experiment, type B1Scenario, type B1Profile, type B1Summary } from "../services/b1Experiments";
import { saveDownload } from "../services/developer";
import { useResource } from "../hooks/useResource";
import { ErrorBox, Loading, MetricCard, Notice, Progress, percentage } from "./ui";
import { ScrollArea } from "./ScrollArea";

const terminal = new Set(["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"]);
const statuses: Record<string, string> = {
  PREPARING: "Подготовка копий сессий",
  QUEUED: "В очереди",
  RUNNING: "Выполняется",
  AWAITING_PROFILE: "Ожидает смены конфигурации",
  COMPLETED: "Завершён",
  FAILED: "Завершён с ошибкой",
  CANCELLED: "Остановлен",
  INTERRUPTED: "Прерван",
  PENDING: "Ожидает запуска",
};
const phases: Record<string, string> = {
  SETUP: "Подготовка",
  WARMUP: "Прогрев — вне измеряемой выборки",
  MEASURING: "Измерение",
  DRAINING: "Ожидание завершения начатых запросов",
};
const scenarios: Record<B1Scenario, { title: string; description: string }> = {
  concurrent_10: {
    title: "10 пользователей × 1 запрос одновременно",
    description: "Диагностика конкуренции: 10 изолированных сессий запускают подбор параллельно.",
  },
  sequential_10: {
    title: "1 пользователь × 10 запросов последовательно",
    description: "Диагностика последовательной нагрузки: следующий запрос начинается после завершения предыдущего.",
  },
  confirmation: {
    title: "Полный протокол: 5 пар baseline / split",
    description: "10 измерений: в каждом 3 минуты прогрева и 600 запланированных запросов за 30 минут (20 запросов/мин). Фоновая обработка работает непрерывно. Между конфигурациями требуется смена режима очередей.",
  },
};
const profileLabel = (value?: B1Profile) => value === "shared" ? "Baseline — общая очередь" : value === "split" ? "Split — раздельные очереди" : "Не определён";
const number = (value: unknown, unit = "") => typeof value === "number" && Number.isFinite(value)
  ? `${value.toLocaleString("ru-RU", { maximumFractionDigits: 3 })}${unit ? ` ${unit}` : ""}`
  : "Не измерено";
const errorText = (error: unknown) => error instanceof Error ? error.message : "Не удалось выполнить запрос";

function Summary({ summary }: { summary?: B1Summary | null }) {
  return <div className="metric-grid">
    <MetricCard title="p95 полного подбора" value={number(summary?.p95_seconds, "с")} detail="95% успешных подборов не медленнее этого значения" />
    <MetricCard title="Время всей серии" value={number(summary?.scenario_makespan_seconds, "с")} detail="От начала серии до последнего результата" />
    <MetricCard title="Доля ошибок" value={percentage(summary?.error_rate)} detail="Ошибки / измеренные запросы" />
    <MetricCard title="Фоновая производительность" value={number(summary?.batch_rows_per_minute, "строк/мин")} detail="Обработанные фоновые строки / время наблюдения" />
  </div>;
}

export function B1ExperimentPanel() {
  const config = useResource(b1Api.config, []);
  const history = useResource(b1Api.list, []);
  const [baseId, setBaseId] = useState("");
  const [requirementId, setRequirementId] = useState("");
  const [backgroundId, setBackgroundId] = useState("");
  const [scenario, setScenario] = useState<B1Scenario>("concurrent_10");
  const [title, setTitle] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [pollVersion, setPollVersion] = useState(0);
  const [experiment, setExperiment] = useState<B1Experiment | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const requirements = useResource(() => api.requirements(requirementId), [requirementId], !!requirementId);
  const readyRequirements = (requirements.data?.items || []).filter(item => item.passport)
    .sort((a, b) => (a.document_id || "").localeCompare(b.document_id || "") || (a.row_index || 0) - (b.row_index || 0) || a.id.localeCompare(b.id)).slice(0, 10);
  const base = config.data?.ready_bases.find(item => item.session_id === baseId);
  const isFull = scenario === "confirmation";
  const active = !!experiment && !terminal.has(experiment.status);

  useEffect(() => {
    if (!selectedId && history.data?.items.length) setSelectedId(history.data.items[0].id);
  }, [history.data, selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await b1Api.detail(selectedId);
        if (!alive) return;
        setExperiment(next);
        setPollError("");
        if (!terminal.has(next.status)) {
          if (next.status === "AWAITING_PROFILE") config.refresh();
          timer = setTimeout(poll, 3000);
        }
      } catch (failure) {
        if (!alive) return;
        setPollError(errorText(failure));
        timer = setTimeout(poll, 5000);
      }
    };
    setExperiment(null);
    void poll();
    return () => { alive = false; clearTimeout(timer); };
    // Polling belongs to the selected server experiment and stops on unmount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, pollVersion]);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try { await action(); } catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); }
  };

  const start = () => act(async () => {
    const created = await b1Api.create({
      scenario, base_session_id: baseId, requirement_session_id: requirementId,
      ...(isFull && backgroundId ? { background_session_id: backgroundId } : {}),
      ...(title.trim() ? { campaign_title: title.trim() } : {}),
    });
    setExperiment(created);
    setSelectedId(created.id);
    history.refresh();
  });
  const current = experiment?.current_run;
  const summary = current?.summary || experiment?.summary;
  const pendingMode = experiment?.expected_routing_mode || experiment?.plan?.find(run => run.status === "PENDING")?.routing_mode;
  const canContinue = experiment?.status === "AWAITING_PROFILE" || (!!experiment && ["FAILED", "INTERRUPTED", "CANCELLED"].includes(experiment.status) && !!pendingMode);
  const canStart = !!config.data && !!baseId && !!requirementId && readyRequirements.length === 10 && !requirements.loading && !requirements.error && (!isFull || !!backgroundId) && !active && !busy;

  return <div className="section-gap b1-experiments">
    <section className="panel stack" aria-labelledby="b1-title">
      <div className="row between wrap"><div><h2 id="b1-title">Проверка гипотезы B1</h2><p className="muted">Влияние разделения очередей на отклик подбора при фоновой обработке базы.</p></div>
        <button className="btn" disabled={busy} onClick={() => { config.refresh(); history.refresh(); }}>Обновить конфигурацию</button>
      </div>
      <Notice title="Реальные запросы к backend">
        Эксперимент использует готовую базу и первые 10 нормализованных потребностей из выбранной сессии. Для нагрузки создаются отдельные сессии. Исходные файлы, паспорта и результаты сохраняются. Синтетические задержки не используются.
      </Notice>
      {config.loading && !config.data && <Loading />}
      {config.error && <ErrorBox message={config.error} />}
      {config.data && <>
        <div className="b1-profile"><strong>Текущая конфигурация: {profileLabel(config.data.routing_mode)}</strong><small>Режим задаётся настройкой backend и рабочих процессов. Эта форма запускает измерение в действующей конфигурации.</small></div>
        <div className="b1-form-grid">
          <label>Готовая база (цель — 12 000 строк)<select value={baseId} onChange={event => setBaseId(event.target.value)} disabled={busy || active}>
            <option value="">Выберите нормализованную базу</option>
            {config.data.ready_bases.map(item => <option key={item.session_id} value={item.session_id}>{item.title} — {number(item.normalized_rows)} строк</option>)}
          </select></label>
          <label>Сессия с 10 потребностями<select value={requirementId} onChange={event => setRequirementId(event.target.value)} disabled={busy || active}>
            <option value="">Выберите готовые потребности</option>
            {config.data.requirement_sessions.map(item => <option key={item.session_id} value={item.session_id}>{item.title} — {number(item.normalized_requirements)} потребностей</option>)}
          </select></label>
        </div>
        {base && base.normalized_rows !== 12000 && <Notice title="Размер базы отличается от целевых 12 000 строк" tone="warning">Фактический размер: {number(base.normalized_rows)}. Укажите его в отчёте; результат диагностики на другой базе не подтверждает протокол на 12 000 строках.</Notice>}
        {!config.data.ready_bases.length && <p>Подготовьте и нормализуйте базу в обычном рабочем процессе, затем обновите конфигурацию.</p>}
        {!config.data.requirement_sessions.length && <p>Загрузите и нормализуйте не менее 10 потребностей в рабочей сессии, затем вернитесь к эксперименту.</p>}
        {requirements.loading && <Loading />}
        {requirements.error && <ErrorBox message={requirements.error} />}
        {requirementId && !requirements.loading && requirements.data && <details className="b1-requirements" open>
          <summary>Входные данные: {readyRequirements.length} из 10 потребностей с параметрами</summary>
          {readyRequirements.length < 10 && <ErrorBox message="Для запуска нужны 10 нормализованных потребностей. Завершите обработку исходной сессии." />}
          <ScrollArea label="Десять потребностей B1"><table><thead><tr><th>№</th><th>Потребность</th><th>Нормализованные параметры</th></tr></thead><tbody>
            {readyRequirements.map((item, index) => <tr key={item.id}><td>{index + 1}</td><td>{item.passport?.name || item.source_text}<small className="block">{item.id}</small></td><td><code>{JSON.stringify(item.passport?.normalized_attributes || item.passport?.attributes || {})}</code></td></tr>)}
          </tbody></table></ScrollArea>
        </details>}
        <label>Сценарий проверки<select value={scenario} onChange={event => setScenario(event.target.value as B1Scenario)} disabled={busy || active}>
          {(Object.entries(scenarios) as [B1Scenario, typeof scenarios[B1Scenario]][]).map(([key, value]) => <option key={key} value={key}>{value.title}</option>)}
        </select></label>
        <Notice title={isFull ? "Подтверждающий протокол" : "Диагностический запуск"} tone={isFull ? "info" : "warning"}>
          {scenarios[scenario].description} {isFull ? "Полная кампания занимает не менее 5,5 часов плюс подготовка и завершение запросов." : "10 запросов проверяют поведение системы; этого недостаточно для подтверждения B1."}
        </Notice>
        <div className="b1-form-grid">
          {isFull && <label>Фоновая обработка (обязательно)<select value={backgroundId} onChange={event => setBackgroundId(event.target.value)} disabled={busy || active}>
            <option value="">Без фоновой обработки</option>
            {config.data.background_sessions.map(item => <option key={item.session_id} value={item.session_id}>{item.title}</option>)}
          </select></label>}
          <label>Название эксперимента<input value={title} maxLength={160} placeholder="Например, B1 / база 12 000 / повтор 1" onChange={event => setTitle(event.target.value)} disabled={busy || active} /></label>
        </div>
        {isFull && !backgroundId && <p className="muted">Для полного протокола выберите сессию с исходным файлом остатков и подтверждённым сопоставлением колонок.</p>}
        <div className="row wrap"><button className="btn btn-primary" disabled={!canStart} onClick={() => void start()}>{busy ? "Выполняется действие…" : "Запустить проверку B1"}</button><small>Ввод и вывод каждого запуска доступны в экспорте.</small></div>
      </>}
      {error && <ErrorBox message={error} />}
    </section>

    <section className="panel stack" aria-labelledby="b1-history"><h2 id="b1-history">Запуски B1</h2>
      {history.error && <ErrorBox message={history.error} />}
      {history.loading && !history.data && <Loading />}
      {!!history.data?.items.length ? <label>Сохранённый эксперимент<select value={selectedId} onChange={event => { setSelectedId(event.target.value); setError(""); }} disabled={busy}>
        {history.data.items.map(item => <option key={item.id} value={item.id}>{item.title || item.id} — {statuses[item.id === experiment?.id ? experiment.status : item.status] || item.status}</option>)}
      </select></label> : <p className="muted">Сохранённых запусков пока нет.</p>}
      {pollError && <ErrorBox message={`Не удалось обновить статус: ${pollError}. Повторяем запрос автоматически.`} />}
      {selectedId && !experiment && !pollError && <Loading />}
      {experiment && <>
        <div className="row between wrap"><div><h3>{experiment.title || "Эксперимент B1"}</h3><small>{experiment.id}</small></div><strong role="status">{statuses[experiment.status] || experiment.status}</strong></div>
        <Notice title={experiment.scope === "confirmation" ? "Подтверждающая кампания" : "Диагностический результат"} tone={experiment.scope === "confirmation" ? "info" : "warning"}>
          {experiment.scope === "confirmation" ? "Статус «Завершён» означает окончание измерений. Вывод по B1 делается по парным результатам и критериям протокола." : "Этот запуск не подтверждает гипотезу: он проверяет работу сценария на малой выборке."}
        </Notice>
        <Progress value={experiment.progress ?? current?.progress ?? 0} />
        {current && <div className="b1-run-facts"><p><strong>{phases[current.phase] || current.phase}</strong> · {profileLabel(current.routing_mode)} · прошло {number(current.elapsed_seconds, "с")}</p><p>Запланировано: {current.scheduled} · начато: {current.started} · завершено: {current.completed} · успешно: {current.success} · ошибок: {current.failed} · не запущено по расписанию: {current.missed}</p></div>}
        {experiment.error && <ErrorBox message={experiment.error} />}
        {experiment.status === "AWAITING_PROFILE" && <Notice title={`Нужна конфигурация: ${profileLabel(experiment.expected_routing_mode)}`} tone="warning">
          Переключите конфигурацию и перезапустите рабочие процессы по инструкции запуска. После обновления режима нажмите «Продолжить измерение». Текущая конфигурация: {profileLabel(config.data?.routing_mode)}.
        </Notice>}
        <Summary summary={summary} />
        {experiment.summary?.reason && <Notice title={experiment.summary.valid === false ? "Условия протокола не выполнены" : "Результат проверки протокола"} tone={experiment.summary.valid === false ? "warning" : "info"}>{experiment.summary.reason}</Notice>}
        {!!experiment.plan?.length && <details><summary>План и парные измерения ({experiment.plan.length})</summary><ScrollArea label="План эксперимента B1"><table><thead><tr><th>Этап</th><th>Пара</th><th>Конфигурация</th><th>Состояние</th><th>p95, с</th><th>Ошибки</th><th>Фон, строк/мин</th></tr></thead><tbody>
          {experiment.plan.map(run => <tr key={run.index}><td>{run.index + 1}</td><td>{run.pair_index}</td><td>{profileLabel(run.routing_mode)}</td><td>{statuses[run.status] || run.status}</td><td>{number(run.summary?.p95_seconds)}</td><td>{percentage(run.summary?.error_rate)}</td><td>{number(run.summary?.batch_rows_per_minute)}</td></tr>)}
        </tbody></table></ScrollArea></details>}
        <div className="row wrap">
          {canContinue && <button className="btn btn-primary" disabled={busy || config.data?.routing_mode !== pendingMode} onClick={() => void act(async () => { setExperiment(await b1Api.continue(experiment.id)); setPollVersion(value => value + 1); history.refresh(); })}>Продолжить измерение</button>}
          {(!terminal.has(experiment.status) || experiment.status === "INTERRUPTED") && <button className="btn btn-danger" disabled={busy} onClick={() => void act(async () => { setExperiment(await b1Api.cancel(experiment.id)); history.refresh(); })}>Остановить эксперимент</button>}
          {(["json", "csv", "zip"] as const).map(format => <button key={format} className="btn" disabled={busy} onClick={() => void act(() => saveDownload(b1Api.exportPath(experiment.id, format), `b1-${experiment.id}.${format}`))}>Скачать {format.toUpperCase()}</button>)}
        </div>
      </>}
    </section>
  </div>;
}
