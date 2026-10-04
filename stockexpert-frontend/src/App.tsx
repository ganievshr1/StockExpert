import { useState } from "react";
import { WorkspaceProvider, useWorkspace } from "./state/Workspace";
import { ThemeProvider } from "./state/Theme";
import { Sidebar } from "./components/Sidebar";
import { Icon } from "./components/Icon";
import { Loading, ErrorBox } from "./components/ui";
import { useResource } from "./hooks/useResource";
import { workspaceApi } from "./services/workspace";
import { LoginPage } from "./pages/LoginPage";
import { DashboardPage } from "./pages/DashboardPage";
import { SessionOverviewPage } from "./pages/SessionOverviewPage";
import { DataPage } from "./pages/DataPage";
import { ProcessingPage } from "./pages/ProcessingPage";
import { TaxonomyPage } from "./pages/TaxonomyPage";
import { ShortlistPage } from "./pages/ShortlistPage";
import { QualityPage } from "./pages/QualityPage";
import { ReportsPage } from "./pages/ReportsPage";
import { MetricsPage } from "./pages/MetricsPage";
import { AuditPage } from "./pages/AuditPage";
import { NewSessionDialog } from "./features/session/NewSessionDialog";
function WorkspaceApp() {
  const w = useWorkspace();
  const [creating, setCreating] = useState(false);
  const organization = useResource(
    () => workspaceApi.overview(),
    [w.user?.id],
    !!w.user,
  );
  if (w.initializing) return <Loading />;
  if (!w.user) return <LoginPage />;
  const isDashboard = w.tab === "dashboard";
  const pages = {
    dashboard: <DashboardPage onCreate={() => setCreating(true)} />,
    overview: <SessionOverviewPage />,
    data: <DataPage />,
    processing: <ProcessingPage />,
    taxonomy: <TaxonomyPage />,
    shortlist: <ShortlistPage />,
    quality: <QualityPage />,
    reports: <ReportsPage />,
    metrics: <MetricsPage />,
    audit: <AuditPage />,
  };
  return (
    <div className="app-shell">
      <Sidebar
        organization={organization.data?.organization || "Рабочее пространство"}
      />
      <main className="workspace-main">
        <header className="topbar">
          <div className="topbar-context">
            {isDashboard ? (
              <span>Обзор рабочего пространства</span>
            ) : (
              <>
                <Icon name="box" />
                <select
                  aria-label="Закупочная сессия"
                  value={w.sid}
                  onChange={(event) => w.selectSession(event.target.value)}
                >
                  <option value="">Выберите сессию</option>
                  {w.sid &&
                    !w.sessions.some((session) => session.id === w.sid) && (
                      <option value={w.sid}>
                        {w.data?.session.title || w.sid}
                      </option>
                    )}
                  {w.sessions.map((session) => (
                    <option key={session.id} value={session.id}>
                      {session.title}
                    </option>
                  ))}
                </select>
                {w.sid && (
                  <button
                    className="link-button"
                    onClick={() => w.setTab("overview")}
                  >
                    Обзор
                  </button>
                )}
              </>
            )}
          </div>
          {w.active && (
            <button
              className="link-button"
              onClick={() => w.setTab("processing")}
            >
              Обработка · {w.data?.session.progress}%
            </button>
          )}
          <div className="topbar-time">
            <Icon name="clock" size={16} />
            <span>
              {w.active
                ? "Обработка продолжается"
                : w.busy
                  ? "Сохраняем…"
                  : "Изменения сохранены"}
            </span>
          </div>
          <button
            className="btn"
            disabled={!w.canEdit}
            onClick={() => setCreating(true)}
          >
            Новая сессия
          </button>
        </header>
        <div className="page-content">
          {w.error && (
            <div className="stack" style={{ marginBottom: 24 }}>
              <ErrorBox message={w.error} />
              <button className="link-button" onClick={w.clearError}>
                Скрыть сообщение
              </button>
            </div>
          )}
          {w.config &&
            (!["3.6", "3.7"].includes(w.config.api_contract_version) ||
              w.config.ui_contract_version !== "3.7") && (
              <ErrorBox message="Установите бэкэнд из парного архива: версия API не соответствует интерфейсу." />
            )}
          <div key={isDashboard ? "dashboard" : w.sid}>{pages[w.tab]}</div>
        </div>
      </main>
      {w.notice && (
        <div role="status" className="toast">
          <Icon name="check" />
          {w.notice}
        </div>
      )}
      {creating && <NewSessionDialog onClose={() => setCreating(false)} />}
    </div>
  );
}
export function App() {
  return (
    <ThemeProvider>
      <WorkspaceProvider>
        <WorkspaceApp />
      </WorkspaceProvider>
    </ThemeProvider>
  );
}
