import { useEffect, useRef } from "react";
import { useWorkspace } from "../state/Workspace";
import { Brand, Icon, ThemeSwitch } from "./Icon";
import type { NavTab } from "../types";
const entries: { id: NavTab; label: string; icon: string }[] = [
  { id: "dashboard", label: "Рабочий стол", icon: "grid" },
  { id: "data", label: "Данные сессии", icon: "upload" },
  { id: "taxonomy", label: "Классификатор", icon: "layers" },
  { id: "shortlist", label: "Подбор запасов", icon: "search" },
  { id: "quality", label: "Проверка качества", icon: "check" },
  { id: "reports", label: "Отчёт комиссии", icon: "file" },
  { id: "metrics", label: "Метрики", icon: "chart" },
  { id: "audit", label: "Аудит решений", icon: "shield" },
];
export function Sidebar({ organization }: { organization: string }) {
  const w = useWorkspace();
  const navigation = useRef<HTMLElement>(null);
  useEffect(() => {
    // Reveal the active item without scrolling the page when a step changes.
    const nav = navigation.current;
    const active = nav?.querySelector<HTMLElement>(".nav-item.active");
    if (!nav || !active || nav.scrollWidth <= nav.clientWidth) return;
    const container = nav.getBoundingClientRect();
    const item = active.getBoundingClientRect();
    if (item.left < container.left)
      nav.scrollLeft += item.left - container.left;
    else if (item.right > container.right)
      nav.scrollLeft += item.right - container.right;
  }, [w.tab]);
  const model = w.data?.session.llm_config || w.config?.default_llm_config;
  const role =
    w.user?.role === "admin"
      ? "Администратор"
      : w.user?.role === "dmto_expert"
        ? "Эксперт МТО"
        : "Закупщик";
  return (
    <aside className="sidebar">
      <Brand />
      <div className="organization">
        <strong>{organization}</strong>
        <small>Рабочее пространство МТО</small>
      </div>
      <nav
        ref={navigation}
        className="nav-group"
        aria-label="Основная навигация"
      >
        <p className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</p>
        {entries.map((entry, index) => (
          <div key={entry.id} className={index === 6 ? "analytics-nav" : ""}>
            {index === 6 && (
              <>
                <div className="nav-gap" />
                <p className="nav-label">АНАЛИТИКА И КОНТРОЛЬ</p>
              </>
            )}
            <button
              className={`nav-item full-width ${w.tab === entry.id || (entry.id === "dashboard" && w.tab === "overview") || (entry.id === "data" && w.tab === "processing") ? "active" : ""}`}
              onClick={() => w.setTab(entry.id)}
              aria-current={w.tab === entry.id ? "page" : undefined}
            >
              <Icon name={entry.icon} size={18} />
              {entry.label}
            </button>
          </div>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="processing-context">
          <div className="row">
            <Icon name="shield-success" size={16} />
            <strong>
              {model?.provider === "ollama"
                ? "Локальная обработка"
                : "Внешняя обработка"}
            </strong>
          </div>
          <small>
            {model?.provider === "ollama"
              ? "Данные внутри компании"
              : "Через DeepSeek API"}
          </small>
        </div>
        <ThemeSwitch />
        <div className="profile">
          <div className="avatar">
            {w.user?.username.slice(0, 2).toUpperCase()}
          </div>
          <div className="profile-text">
            <strong>{w.user?.username}</strong>
            <small>{role}</small>
          </div>
          <button
            className="icon-btn"
            title="Выйти"
            aria-label="Выйти"
            onClick={w.logout}
          >
            ↪
          </button>
        </div>
      </div>
    </aside>
  );
}
