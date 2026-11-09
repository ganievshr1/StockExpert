import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import {
  api,
  ApiError,
  getToken,
  setToken,
  type SessionDeletionResult,
} from "../services/api";
import { streamProgress } from "../services/sseClient";
import type {
  User,
  SystemConfig,
  ProcurementSession,
  Snapshot,
  NavTab,
} from "../types";
interface WorkspaceValue {
  user: User | null;
  config: SystemConfig | null;
  initializing: boolean;
  error: string;
  notice: string;
  busy: boolean;
  sessions: ProcurementSession[];
  sid: string;
  data: Snapshot | null;
  tab: NavTab;
  setTab: (tab: NavTab) => void;
  selectSession: (sid: string) => void;
  deleteSessions: (sid?: string) => Promise<SessionDeletionResult>;
  login: (
    username: string,
    password: string,
    remember?: boolean,
  ) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
  reloadSessions: (offset?: number) => Promise<void>;
  act: (work: () => Promise<unknown>, message?: string) => Promise<boolean>;
  create: (
    title: string,
    baseSessionId?: string,
    llmProfileId?: string,
    normalizationMethod?: "llm" | "regex",
    taxonomyEnabled?: boolean,
  ) => Promise<void>;
  clearError: () => void;
  canEdit: boolean;
  canExpert: boolean;
  active: boolean;
  streamFallback: boolean;
}
const Context = createContext<WorkspaceValue | null>(null);
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null),
    [config, setConfig] = useState<SystemConfig | null>(null),
    [initializing, setInitializing] = useState(true),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [sessions, setSessions] = useState<ProcurementSession[]>([]),
    [sid, setSid] = useState(sessionStorage.getItem("stockexpert.sid") || ""),
    [data, setData] = useState<Snapshot | null>(null),
    [tab, setTab] = useState<NavTab>("dashboard"),
    [streamFallback, setFallback] = useState(false);
  const sidRef = useRef(sid);
  sidRef.current = sid;
  const operation = useRef(false);
  const sessionListRequest = useRef(0);
  const logout = useCallback(() => {
    setToken("");
    setUser(null);
    setConfig(null);
    setData(null);
    setSessions([]);
    setSid("");
    sidRef.current = "";
    sessionListRequest.current++;
    sessionStorage.removeItem("stockexpert.sid");
  }, []);
  const selectSession = useCallback((id: string) => {
    if (sidRef.current !== id) setData(null);
    sidRef.current = id;
    setSid(id);
    setError("");
    setTab("overview");
    sessionStorage.setItem("stockexpert.sid", id);
  }, []);
  const reloadSessions = useCallback(async (offset = 0) => {
    const request = ++sessionListRequest.current;
    const list = await api.sessions(offset);
    if (request === sessionListRequest.current && getToken()) setSessions(list);
  }, []);
  const initialize = useCallback(async () => {
    const [u, c, list] = await Promise.all([
      api.me(),
      api.config(),
      api.sessions(),
    ]);
    if (!getToken()) return;
    setUser(u);
    setConfig(c);
    setSessions(list);
  }, []);
  const login = async (
    username: string,
    password: string,
    remember = false,
  ) => {
    const result = await api.login(username, password);
    setToken(result.access_token, remember);
    try {
      await initialize();
      setError("");
    } catch (e) {
      setToken("");
      throw e;
    }
  };
  useEffect(() => {
    let alive = true;
    const unauthorized = () => logout();
    window.addEventListener("stockexpert:unauthorized", unauthorized);
    if (getToken())
      initialize()
        .catch((e) => {
          if (alive) setError(String(e.message));
        })
        .finally(() => {
          if (alive) setInitializing(false);
        });
    else setInitializing(false);
    return () => {
      alive = false;
      window.removeEventListener("stockexpert:unauthorized", unauthorized);
    };
  }, [initialize, logout]);
  const refresh = useCallback(async () => {
    if (!sid) return;
    const [session, documents, metrics, job] = await Promise.all([
      api.session(sid),
      api.documents(sid),
      api.metrics(sid),
      api.status(sid),
    ]);
    if (sidRef.current === sid && getToken())
      setData({ session, documents, metrics, job });
  }, [sid]);
  useEffect(() => {
    if (user && sid)
      refresh().catch((e) => {
        if (sidRef.current === sid) setError(e.message);
      });
  }, [user, sid, refresh]);
  const active =
    !!data &&
    ["QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(data.job.status);
  useEffect(() => {
    if (!user || !sid || !active) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setFallback(false);
    const update = (event: {
      status: string;
      progress: number;
      job: Snapshot["job"];
    }) => {
      if (controller.signal.aborted || sidRef.current !== sid) return;
      setData((prev) =>
        prev
          ? {
              ...prev,
              session: {
                ...prev.session,
                status: event.status,
                progress: event.progress,
              },
              job: { ...event.job, progress: event.progress },
            }
          : prev,
      );
      if (!["QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(event.job.status))
        void refresh().catch((e) => {
          if (sidRef.current === sid) setError(e.message);
        });
    };
    const poll = async () => {
      if (controller.signal.aborted) return;
      try {
        const job = await api.status(sid);
        update({
          status: job.session_status || "",
          progress: job.progress,
          job,
        });
        if (["QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(job.status))
          timer = setTimeout(poll, 3000);
      } catch (e) {
        if (!controller.signal.aborted && sidRef.current === sid) {
          setError((e as Error).message);
          timer = setTimeout(poll, 5000);
        }
      }
    };
    streamProgress(sid, controller.signal, update)
      .then(() => {
        if (!controller.signal.aborted) void poll();
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setFallback(true);
          void poll();
        }
      });
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [sid, user, active, refresh]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 5000);
    return () => clearTimeout(timer);
  }, [notice]);
  const act = async (work: () => Promise<unknown>, message = "Сохранено") => {
    if (operation.current) return false;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      await work();
      await refresh();
      setNotice(message);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      operation.current = false;
      setBusy(false);
    }
  };
  const create = async (
    title: string,
    baseSessionId?: string,
    llmProfileId?: string,
    normalizationMethod?: "llm" | "regex",
    taxonomyEnabled?: boolean,
  ) => {
    const s = await api.create(
      title,
      baseSessionId,
      llmProfileId,
      normalizationMethod,
      taxonomyEnabled,
    );
    await reloadSessions();
    selectSession(s.id);
  };
  const deleteSessions = async (
    id?: string,
  ): Promise<SessionDeletionResult> => {
    if (operation.current)
      throw new Error("Дождитесь завершения текущей операции.");
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      let result: SessionDeletionResult;
      if (id) {
        try {
          await api.deleteSession(id);
        } catch (e) {
          // A second tab/user may already have removed the same session.
          if (!(e instanceof ApiError) || e.status !== 404) throw e;
        }
        result = { deleted_session_ids: [id], failed: [] };
      } else {
        result = await api.deleteAllSessions();
      }
      const deleted = new Set(result.deleted_session_ids);
      sessionListRequest.current++;
      setSessions((previous) =>
        previous.filter((session) => !deleted.has(session.id)),
      );
      if (deleted.has(sidRef.current) || (!id && !result.failed.length)) {
        sidRef.current = "";
        setSid("");
        setData(null);
        setFallback(false);
        sessionStorage.removeItem("stockexpert.sid");
        setTab("dashboard");
      }
      setNotice(
        result.failed.length
          ? `Удалено сессий: ${deleted.size}. Остались сессии, которые не удалось удалить.`
          : id
            ? "Сессия удалена"
            : `Удалено сессий: ${deleted.size}`,
      );
      // Removal is already committed. A refresh failure must not imply that
      // deletion failed or trigger another destructive request automatically.
      try {
        await reloadSessions();
      } catch {
        setError(
          "Сессии удалены, но список не удалось обновить. Нажмите «Обновить».",
        );
      }
      return result;
    } finally {
      operation.current = false;
      setBusy(false);
    }
  };
  return (
    <Context.Provider
      value={{
        user,
        config,
        initializing,
        error,
        notice,
        busy,
        sessions,
        sid,
        data,
        tab,
        setTab,
        selectSession,
        deleteSessions,
        login,
        logout,
        refresh,
        reloadSessions,
        act,
        create,
        clearError: () => setError(""),
        canEdit:
          !!user && ["admin", "dmto_expert", "buyer"].includes(user.role),
        canExpert: !!user && ["admin", "dmto_expert"].includes(user.role),
        active,
        streamFallback,
      }}
    >
      {children}
    </Context.Provider>
  );
}
export function useWorkspace() {
  const ctx = useContext(Context);
  if (!ctx) throw new Error("WorkspaceProvider missing");
  return ctx;
}
