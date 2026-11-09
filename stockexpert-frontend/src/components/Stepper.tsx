import { useWorkspace } from "../state/Workspace";
import type { NavTab } from "../types";
export function Stepper({ current }: { current: number }) {
  const w = useWorkspace();
  const steps: { title: string; tab: NavTab }[] = [
    { title: "Данные", tab: "data" },
    { title: w.data?.session.taxonomy_enabled === false ? "Параметры без H2" : "Классификатор", tab: "taxonomy" },
    { title: "Подбор", tab: "shortlist" },
    { title: "Решения", tab: "quality" },
    { title: "Отчёт", tab: "reports" },
  ];
  return (
    <nav className="stepper" aria-label="Этапы сессии">
      {steps.map((step, index) => (
        <button
          key={step.tab}
          className={
            index === current ? "active" : index < current ? "complete" : ""
          }
          onClick={() => w.setTab(step.tab)}
        >
          <span className="step-number">
            {index < current ? "✓" : index + 1}
          </span>
          {step.title}
        </button>
      ))}
    </nav>
  );
}
