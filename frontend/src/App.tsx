import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import {
  ApiError,
  checkConnection,
  sendChat,
  uploadFile,
  request,
  jsonRequest,
} from "./lib/api";
import type {
  ActivityEvent,
  Connection,
  Message,
  ToolMode,
  Upload,
  Conversation,
  ConversationDetail,
  Task,
  StoredFile,
} from "./lib/api";
import ActivityLog from "./components/ActivityLog";
import PersonalData from "./components/PersonalData";
import DataDialog from "./components/DataDialog";
import SelfDevelopment from "./components/SelfDevelopment";

type Entry = Message & {
  id: number;
  model?: string;
  duration?: number;
  truncated?: boolean;
  activity?: ActivityEvent[];
};
const starters = [
  {
    icon: "↗",
    title: "Check the numbers",
    text: 'Calculate profit margin using {"revenue":12500,"total_cost":8750}.',
  },
  {
    icon: "◎",
    title: "Build a missing tool",
    text: 'Calculate break-even using {"fixed_cost":45000,"unit_price":600,"variable_cost":300}.',
  },
  {
    icon: "✎",
    title: "Find the right words",
    text: "Help me write a short, friendly introduction for my portfolio.",
  },
];

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Check the backend and retry.";
}

function analysisPrompt(
  file: { kind?: "csv" | "xlsx"; sheets?: { name: string }[] },
  sheet?: string,
) {
  if (file.kind !== "xlsx")
    return "Analyze the attached CSV. Report row count, missing values, and numeric summaries.";
  const target = sheet ?? file.sheets?.[0]?.name;
  return `Analyze the attached Excel workbook${target ? `, sheet "${target}"` : ""}. Report row count, missing values, and numeric summaries.`;
}

export default function App() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [connectionError, setConnectionError] = useState("");
  const [checking, setChecking] = useState(false);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [failed, setFailed] = useState(false);
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const [mode, setMode] = useState<ToolMode>("agentic");
  const [demoTools, setDemoTools] = useState(false);
  const [runningTask, setRunningTask] = useState<string | null>(null);
  const [liveActivity, setLiveActivity] = useState<ActivityEvent[]>([]);
  const [liveError, setLiveError] = useState("");
  const [attachment, setAttachment] = useState<Upload | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [errorActivity, setErrorActivity] = useState<ActivityEvent[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversationRef = useRef<string | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [page, setPage] = useState<
    "chat" | "preferences" | "files" | "development"
  >("chat");
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState("");
  const [dialog, setDialog] = useState<"rename" | "delete" | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const taskId = useRef(crypto.randomUUID());
  const failedTask = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const counter = useRef(0);
  const mounted = useRef(true);
  const inFlight = useRef(false);

  function chooseConversation(id: string) {
    conversationRef.current = id;
    setConversationId(id);
    localStorage.setItem("evolve.conversation", id);
  }

  async function refreshHistory() {
    const saved = await request<Conversation[]>("/conversations");
    if (mounted.current) {
      setConversations(saved);
      setHistoryError("");
    }
    return saved;
  }

  async function loadMessages(id: string) {
    const detail = await request<ConversationDetail>(`/conversations/${id}`);
    if (!mounted.current) return;
    setTasks(detail.tasks);
    setEntries(
      detail.messages.map((message) => ({
        id: ++counter.current,
        role: message.role,
        content: message.content,
        model: message.metadata.model,
        duration: message.metadata.duration_ms,
        truncated: message.metadata.truncated,
        activity:
          message.role === "assistant"
            ? detail.tasks.find((task) => task.id === message.task_id)?.activity
            : undefined,
      })),
    );
  }

  async function openConversation(id: string) {
    if (busy || uploading || inFlight.current) return;
    setHistoryLoading(true);
    setHistoryError("");
    try {
      await loadMessages(id);
      chooseConversation(id);
      setAttachment(null);
      setMode("agentic");
      setDemoTools(false);
      setDraft("");
      setError("");
      setFailed(false);
      setErrorActivity([]);
      setUploadError("");
      taskId.current = crypto.randomUUID();
      setPage("chat");
      setMobileSidebar(false);
    } catch (err) {
      setHistoryError(errorMessage(err));
    } finally {
      setHistoryLoading(false);
    }
  }

  async function ensureConversation() {
    if (conversationRef.current) return conversationRef.current;
    const created = await jsonRequest<Conversation>(
      "/conversations",
      "POST",
      {},
    );
    chooseConversation(created.id);
    await refreshHistory();
    return created.id;
  }

  async function initializeHistory() {
    setHistoryLoading(true);
    try {
      const saved = await refreshHistory();
      const previous = localStorage.getItem("evolve.conversation");
      if (previous && saved.some((item) => item.id === previous))
        await openConversation(previous);
    } catch (err) {
      if (mounted.current) setHistoryError(errorMessage(err));
    } finally {
      if (mounted.current) setHistoryLoading(false);
    }
  }

  async function refresh() {
    setChecking(true);
    try {
      const next = await checkConnection();
      if (mounted.current) {
        setConnection(next);
        setConnectionError("");
      }
    } catch (err) {
      if (mounted.current) {
        setConnection(null);
        setConnectionError(errorMessage(err));
      }
    } finally {
      if (mounted.current) setChecking(false);
    }
  }

  useEffect(() => {
    mounted.current = true;
    void refresh();
    void initializeHistory();
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries, busy, error]);

  useEffect(() => {
    if (!runningTask) return;
    let stopped = false;
    let timer: number;
    async function poll() {
      try {
        const task = await request<Task>(`/tasks/${runningTask}`);
        if (!stopped) {
          setLiveActivity(task.activity);
          setLiveError("");
        }
      } catch (err) {
        // A task can briefly be absent before /chat creates it.
        if (
          !stopped &&
          !(err instanceof ApiError && err.message.includes("not found"))
        )
          setLiveError(`Live activity unavailable: ${errorMessage(err)}`);
      }
      if (!stopped) timer = window.setTimeout(() => void poll(), 1500);
    }
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [runningTask]);

  const ready = connection?.health && connection.model.status === "ready";
  const modelName = connection?.model.model ?? "Checking model…";
  const stateLabel = checking
    ? "Checking connection"
    : ready
      ? `${connection.model.provider} API ready`
      : connection
        ? connection.model.status === "not_configured"
          ? "API key needed"
          : "Model unavailable"
        : "Backend unavailable";

  async function generate(messages: Entry[], retry = false) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setErrorActivity([]);
    setFailed(false);
    setLiveActivity([]);
    setLiveError("");
    try {
      const savedId = await ensureConversation();
      setRunningTask(taskId.current);
      const result = await sendChat(
        messages.map(({ role, content }) => ({ role, content })),
        {
          taskId: taskId.current,
          mode,
          upload: attachment,
          conversationId: savedId,
          retryOf: retry ? failedTask.current : null,
          demoTools,
        },
      );
      if (!mounted.current) return;
      failedTask.current = null;
      setEntries([
        ...messages,
        {
          id: ++counter.current,
          role: "assistant",
          content: result.message,
          model: result.model,
          duration: result.duration_ms,
          truncated: result.truncated,
          activity: result.activity,
        },
      ]);
    } catch (err) {
      failedTask.current = err instanceof ApiError ? err.taskId : null;
      if (mounted.current) {
        setError(errorMessage(err));
        setErrorActivity(err instanceof ApiError ? err.activity : []);
        setFailed(true);
      }
    } finally {
      setRunningTask(null);
      taskId.current = crypto.randomUUID();
      try {
        await refreshHistory();
        if (conversationRef.current)
          await loadMessages(conversationRef.current);
      } catch (err) {
        if (mounted.current) setHistoryError(errorMessage(err));
      }
      inFlight.current = false;
      if (mounted.current) {
        setBusy(false);
        void refresh();
        window.setTimeout(() => composer.current?.focus(), 0);
      }
    }
  }

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (
      !draft.trim() ||
      inFlight.current ||
      busy ||
      uploading ||
      failed ||
      historyLoading ||
      !ready
    )
      return;
    const messages: Entry[] = [
      ...entries,
      { id: ++counter.current, role: "user", content: draft.trim() },
    ];
    setEntries(messages);
    setDraft("");
    void generate(messages);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  }

  async function newChat() {
    if (inFlight.current || busy || uploading) return;
    setHistoryLoading(true);
    try {
      const created = await jsonRequest<Conversation>(
        "/conversations",
        "POST",
        {},
      );
      chooseConversation(created.id);
      await refreshHistory();
    } catch (err) {
      setHistoryError(errorMessage(err));
      setHistoryLoading(false);
      return;
    }
    setEntries([]);
    setError("");
    setFailed(false);
    setDraft("");
    setMobileSidebar(false);
    setAttachment(null);
    setMode("agentic");
    setDemoTools(false);
    setUploadError("");
    setErrorActivity([]);
    taskId.current = crypto.randomUUID();
    setTasks([]);
    setPage("chat");
    setHistoryLoading(false);
    composer.current?.focus();
  }

  async function attachFile(file: File) {
    setUploadError("");
    if (!/\.(csv|xlsx)$/i.test(file.name)) {
      setUploadError(
        "Select a UTF-8 .csv file or a plain .xlsx workbook (no macros; legacy .xls is not supported).",
      );
      return;
    }
    if (connection && file.size > connection.tools.upload_max_bytes) {
      setUploadError(
        `The file exceeds the ${(connection.tools.upload_max_bytes / 1024 / 1024).toFixed(1)} MB upload limit.`,
      );
      return;
    }
    setUploading(true);
    try {
      const savedId = await ensureConversation();
      const uploaded = await uploadFile(file, taskId.current, savedId);
      if (!mounted.current) return;
      setAttachment(uploaded);
      setMode("analyze_csv");
      if (!draft.trim()) setDraft(analysisPrompt(uploaded));
    } catch (err) {
      if (mounted.current) setUploadError(errorMessage(err));
    } finally {
      if (mounted.current) setUploading(false);
    }
  }

  async function renameConversation() {
    if (!conversationId) return;
    const title = renameTitle;
    if (!title.trim()) return;
    setDialog(null);
    try {
      await jsonRequest(`/conversations/${conversationId}`, "PATCH", {
        title: title.trim(),
      });
      await refreshHistory();
    } catch (err) {
      setHistoryError(errorMessage(err));
    }
  }

  async function deleteConversation() {
    if (!conversationId) return;
    setDialog(null);
    try {
      await jsonRequest(
        `/conversations/${conversationId}?confirm=true`,
        "DELETE",
      );
      conversationRef.current = null;
      setConversationId(null);
      localStorage.removeItem("evolve.conversation");
      setEntries([]);
      setTasks([]);
      setAttachment(null);
      setMode("auto");
      setDraft("");
      setError("");
      setFailed(false);
      await refreshHistory();
    } catch (err) {
      setHistoryError(errorMessage(err));
    }
  }

  function attachSaved(file: StoredFile) {
    if (file.conversation_id !== conversationId || !file.metadata.columns)
      return;
    setAttachment({
      task_id: taskId.current,
      upload_id: file.id,
      filename: file.filename,
      size_bytes: file.size_bytes,
      row_count: file.metadata.row_count || 0,
      columns: file.metadata.columns,
      conversation_id: conversationId,
      kind: file.metadata.sheets?.length ? "xlsx" : "csv",
      sheets: file.metadata.sheets ?? [],
    });
    setMode("analyze_csv");
    setPage("chat");
    setDraft(
      analysisPrompt({
        kind: file.metadata.sheets?.length ? "xlsx" : "csv",
        sheets: file.metadata.sheets ?? [],
      }),
    );
  }

  return (
    <div className="app-shell">
      {dialog && (
        <DataDialog
          title={
            dialog === "rename" ? "Rename conversation" : "Delete conversation?"
          }
          message={
            dialog === "rename"
              ? "Choose a title for this saved conversation."
              : "This permanently deletes its messages, tasks and scoped preferences. Uploaded files and reports remain for separate cleanup."
          }
          confirmLabel={
            dialog === "rename" ? "Save title" : "Delete conversation"
          }
          onCancel={() => setDialog(null)}
          onConfirm={() =>
            void (dialog === "rename"
              ? renameConversation()
              : deleteConversation())
          }
          text={
            dialog === "rename"
              ? { value: renameTitle, onChange: setRenameTitle }
              : undefined
          }
        />
      )}
      {mobileSidebar && (
        <button
          className="sidebar-scrim"
          aria-label="Close navigation"
          onClick={() => setMobileSidebar(false)}
        />
      )}
      <aside className={`sidebar ${mobileSidebar ? "is-open" : ""}`}>
        <a className="brand" href="/" aria-label="EVOLVE home">
          <span className="brand-mark">E</span>
          <span>
            EVOLVE<span className="brand-subtitle">PERSONAL AI</span>
          </span>
        </a>
        <button
          className="new-chat"
          onClick={() => void newChat()}
          disabled={busy || uploading || historyLoading}
        >
          <span>＋</span> New chat <span className="new-chat-arrow">↗</span>
        </button>
        <div className="nav-label">WORKSPACE</div>
        <button
          className={`nav-item ${page === "chat" ? "active" : ""}`}
          disabled={busy || uploading || historyLoading}
          onClick={() => {
            setPage("chat");
            setMobileSidebar(false);
          }}
        >
          <span>▤</span> Chat <span className="phase-pill">04</span>
        </button>
        <button
          className={`nav-item ${page === "preferences" ? "active" : ""}`}
          disabled={busy || uploading || historyLoading}
          onClick={() => {
            setPage("preferences");
            setMobileSidebar(false);
          }}
        >
          <span>◎</span> Preferences & memory
        </button>
        <button
          className={`nav-item ${page === "files" ? "active" : ""}`}
          disabled={busy || uploading || historyLoading}
          onClick={() => {
            setPage("files");
            setMobileSidebar(false);
          }}
        >
          <span>▱</span> Files & reports
        </button>
        <button
          className={`nav-item ${page === "development" ? "active" : ""}`}
          disabled={busy || uploading || historyLoading}
          onClick={() => {
            setPage("development");
            setMobileSidebar(false);
          }}
        >
          <span>↗</span> Self-Development
        </button>
        <div className="session-section">
          <div className="nav-label">SAVED CONVERSATIONS</div>
          {historyLoading && (
            <p className="session-note" role="status">
              Loading history…
            </p>
          )}
          {historyError && (
            <div className="session-note" role="alert">
              {historyError}
              <button onClick={() => void initializeHistory()}>
                Retry history
              </button>
            </div>
          )}
          {!historyLoading && !conversations.length && (
            <p className="session-empty">
              Your saved conversations will appear here.
            </p>
          )}
          <div className="conversation-history">
            {conversations.map((item) => (
              <button
                key={item.id}
                className={item.id === conversationId ? "selected" : ""}
                disabled={busy || uploading || historyLoading}
                onClick={() => void openConversation(item.id)}
                title={item.title}
              >
                {item.title}
                <small>{new Date(item.updated_at).toLocaleDateString()}</small>
              </button>
            ))}
          </div>
          {conversationId && (
            <div className="history-actions">
              <button
                disabled={busy || uploading || historyLoading}
                onClick={() => {
                  setRenameTitle(
                    conversations.find((item) => item.id === conversationId)
                      ?.title || "",
                  );
                  setDialog("rename");
                }}
              >
                Rename
              </button>
              <button
                disabled={busy || uploading || historyLoading}
                onClick={() => setDialog("delete")}
              >
                Delete chat
              </button>
            </div>
          )}
          <p className="session-note">
            History and approved preferences are saved locally in SQLite.
          </p>
        </div>
        <div className="sidebar-bottom">
          <div className="local-card">
            <span className="local-card-icon">☁</span>
            <strong>Free hosted model.</strong>
            <p>
              {connection
                ? `${connection.model.provider} · ${connection.model.model}`
                : "Connecting…"}
              <br />
              Prompts go to the model API; your data stays in local SQLite.
            </p>
          </div>
          <div className="phase-footer">
            <span className="tiny-dot" /> Phase 4 · Controlled evolution{" "}
            <span>v0.4</span>
          </div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="menu-button"
              onClick={() => setMobileSidebar(!mobileSidebar)}
              aria-label="Toggle navigation"
              aria-expanded={mobileSidebar}
            >
              ☰
            </button>
            <span>Workspace</span>
            <span className="breadcrumb-slash">/</span>
            <strong>
              {page === "chat"
                ? "Chat"
                : page === "preferences"
                  ? "Memory"
                  : page === "files"
                    ? "Files"
                    : "Self-Development"}
            </strong>
          </div>
          <div
            className={`connection-badge ${ready ? "ready" : ""}`}
            role="status"
          >
            <span className="status-dot" />
            {stateLabel}
          </div>
        </header>

        <div className="workspace-heading">
          <div>
            <span className="eyebrow">YOUR PERSONAL WORKSPACE</span>
            <h1>A place to think forward.</h1>
          </div>
          <span className="local-label">⌁ LOCAL-FIRST</span>
        </div>

        <section className="runtime-strip" aria-label="Runtime status">
          <div>
            <span className="runtime-icon">◈</span>
            <span>
              <span className="runtime-label">MODEL</span>
              <strong>{modelName}</strong>
            </span>
          </div>
          <div>
            <span
              className={`runtime-icon ${connection?.health ? "online" : ""}`}
            >
              ↔
            </span>
            <span>
              <span className="runtime-label">BACKEND</span>
              <strong>
                {connection?.health
                  ? "Connected"
                  : checking
                    ? "Checking…"
                    : "Unavailable"}
              </strong>
            </span>
          </div>
          <div className="runtime-session">
            <span className="runtime-icon">◷</span>
            <span>
              <span className="runtime-label">STORAGE</span>
              <strong>Local SQLite</strong>
            </span>
          </div>
          <button
            className="refresh-button"
            onClick={() => void refresh()}
            disabled={checking}
            aria-label="Refresh connection"
          >
            ↻<span>{checking ? "Checking" : "Refresh"}</span>
          </button>
        </section>

        {!checking && !ready && (
          <div className="connection-error" role="alert">
            <strong>
              {connection?.model.status === "not_configured"
                ? "Add your free API key to start."
                : "Let’s get connected."}
            </strong>
            <p>{connectionError || connection?.model.message}</p>
            {connection?.model.status === "not_configured" && (
              <p>
                Put <code>LLM_API_KEY=…</code> in the project’s <code>.env</code>{" "}
                file (free keys: console.groq.com/keys or
                aistudio.google.com/apikey), then restart the backend.
              </p>
            )}
            <button onClick={() => void refresh()}>Check again</button>
          </div>
        )}

        {page === "development" ? (
          <SelfDevelopment />
        ) : page !== "chat" ? (
          <PersonalData
            key={`${page}-${conversationId}`}
            page={page}
            conversationId={conversationId}
            tasks={tasks}
            onAttach={attachSaved}
          />
        ) : (
          <>
            <section
              className={`conversation ${entries.length ? "has-messages" : ""}`}
              aria-label="Conversation"
            >
              {!entries.length ? (
                <div className="welcome">
                  <div className="welcome-symbol" aria-hidden="true">
                    <span>✳</span>
                  </div>
                  <div className="eyebrow">SMALL IDEAS. NEW POSSIBILITIES.</div>
                  <h2>What’s on your mind?</h2>
                  <p>
                    Untangle an idea, learn something new, or find your next
                    step.
                    <br />
                    Your local AI is here to help you get started.
                  </p>
                  <div className="starter-grid">
                    {starters.map((starter) => (
                      <button
                        className="starter-card"
                        key={starter.title}
                        onClick={() => {
                          setDraft(starter.text);
                          composer.current?.focus();
                        }}
                      >
                        <span className="starter-icon">{starter.icon}</span>
                        <strong>{starter.title}</strong>
                        <span className="starter-text">{starter.text}</span>
                        <span className="starter-arrow">↗</span>
                      </button>
                    ))}
                  </div>
                  <div className="capability-note">
                    <span>ⓘ</span> Saved history, approved preferences, and
                    workspace tools are available.
                  </div>
                </div>
              ) : (
                <div
                  className="message-list"
                  role="log"
                  aria-label="Chat messages"
                  aria-live="polite"
                  aria-relevant="additions"
                >
                  {entries.map((entry) => (
                    <article className={`message ${entry.role}`} key={entry.id}>
                      <div className="avatar" aria-hidden="true">
                        {entry.role === "assistant" ? "E" : "Y"}
                      </div>
                      <div className="message-body">
                        <div className="message-byline">
                          <strong>
                            {entry.role === "assistant" ? "EVOLVE" : "You"}
                          </strong>
                          {entry.model && (
                            <span>
                              {entry.model} ·{" "}
                              {((entry.duration ?? 0) / 1000).toFixed(1)}s
                            </span>
                          )}
                        </div>
                        <div className="message-content">{entry.content}</div>
                        {entry.activity && (
                          <ActivityLog events={entry.activity} />
                        )}
                        {entry.truncated && (
                          <p className="truncation-note">
                            Response reached the generation limit. Ask for a
                            shorter answer or a continuation.
                          </p>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
              )}
              {busy && (
                <div className="working" role="status">
                  <span className="working-dot" />
                  Working on your request…{" "}
                  <span>
                    The workflow below updates live from saved backend events.
                  </span>
                </div>
              )}
              {busy && <ActivityLog events={liveActivity} live />}
              {busy && liveError && <p role="status">{liveError}</p>}
              {error && (
                <div className="chat-error" role="alert">
                  <strong>Response failed</strong>
                  <p>{error}</p>
                  <ActivityLog events={errorActivity} />
                  <button
                    onClick={() => void generate(entries, true)}
                    disabled={busy}
                  >
                    Retry message
                  </button>
                  <button
                    onClick={() => {
                      const last = entries.at(-1);
                      setDraft(last?.content ?? "");
                      setEntries(entries.slice(0, -1));
                      setFailed(false);
                      setError("");
                      setErrorActivity([]);
                      composer.current?.focus();
                    }}
                  >
                    Edit message
                  </button>
                </div>
              )}
              <div ref={end} />
            </section>

            <footer className="composer-area">
              <div className="tool-controls">
                <label htmlFor="tool-mode">Tools</label>
                <select
                  id="tool-mode"
                  value={mode}
                  disabled={busy || uploading || failed || historyLoading}
                  onChange={(event) => {
                    const next = event.target.value as ToolMode;
                    setMode(next);
                    if (next === "chat") setAttachment(null);
                  }}
                >
                  <option value="agentic">Agentic · plan and recover</option>
                  <option value="auto">Auto tools</option>
                  <option value="calculate">Calculator required</option>
                  <option value="analyze_csv" disabled={!attachment}>
                    CSV / Excel analysis required
                  </option>
                  <option value="chat">Chat only</option>
                  <option value="list_workspace_files">
                    Workspace listing required
                  </option>
                  <option value="read_workspace_file">
                    Workspace reading required
                  </option>
                  <option value="create_report">
                    Report creation required
                  </option>
                </select>
                <button
                  type="button"
                  className="attach-button"
                  disabled={busy || uploading || failed || !ready}
                  onClick={() => fileInput.current?.click()}
                >
                  {uploading ? "Uploading file…" : "＋ Attach CSV / Excel"}
                </button>
                <input
                  className="sr-only"
                  type="file"
                  accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  aria-label="Upload CSV or Excel file"
                  ref={fileInput}
                  disabled={busy || uploading || failed}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = "";
                    if (file) void attachFile(file);
                  }}
                />
              </div>
              {mode === "agentic" && (
                <div className="demo-controls">
                  <label>
                    <input
                      type="checkbox"
                      checked={demoTools}
                      disabled={
                        busy ||
                        uploading ||
                        failed ||
                        !connection?.tools.demo?.enabled
                      }
                      onChange={(event) => setDemoTools(event.target.checked)}
                    />
                    Enable local DEMO tool generation and execution for this
                    task
                  </label>
                  <p>
                    Demo code runs in a host Python subprocess, which is not a
                    secure sandbox.
                    {!connection?.tools.demo?.enabled &&
                      " Backend demo execution is disabled; set DEMO_MODE=true and restart to enable this option."}
                    {demoTools &&
                      " Candidates activate automatically only after independent tests pass. This opt-in also permits reuse of active demo tools."}
                  </p>
                </div>
              )}
              {attachment && (
                <div className="attachment">
                  <span>
                    <strong>{attachment.filename}</strong> ·{" "}
                    {attachment.row_count} rows · {attachment.columns.length}{" "}
                    columns
                    {attachment.kind === "xlsx" &&
                      ` · first of ${attachment.sheets?.length ?? 1} sheet(s)`}
                  </span>
                  {attachment.kind === "xlsx" &&
                    (attachment.sheets?.length ?? 0) > 1 && (
                      <label className="sheet-picker">
                        Sheet
                        <select
                          value=""
                          disabled={busy || uploading || failed}
                          onChange={(event) => {
                            if (event.target.value)
                              setDraft(
                                analysisPrompt(attachment, event.target.value),
                              );
                          }}
                        >
                          <option value="">Choose to write a prompt…</option>
                          {attachment.sheets!.map((sheet) => (
                            <option key={sheet.name} value={sheet.name}>
                              {sheet.name} ({sheet.row_count} rows)
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                  <button
                    type="button"
                    aria-label="Remove file attachment"
                    disabled={busy || uploading || failed}
                    onClick={() => {
                      setAttachment(null);
                      if (mode === "analyze_csv") setMode("auto");
                    }}
                  >
                    ×
                  </button>
                </div>
              )}
              {uploadError && (
                <p className="upload-error" role="alert">
                  {uploadError}
                </p>
              )}
              <form
                className={`composer ${busy ? "is-busy" : ""}`}
                onSubmit={submit}
              >
                <label className="sr-only" htmlFor="message">
                  Message EVOLVE
                </label>
                <textarea
                  id="message"
                  ref={composer}
                  placeholder="Ask a question. Explore an idea."
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={onKeyDown}
                  maxLength={2000}
                  rows={2}
                  disabled={busy || uploading || failed}
                />
                <div className="composer-toolbar">
                  <span>
                    <span className="composer-dot" /> Local chat{" "}
                    <span className="character-count">{draft.length}/2000</span>
                  </span>
                  <button
                    className="send-button"
                    type="submit"
                    disabled={
                      !draft.trim() ||
                      busy ||
                      uploading ||
                      failed ||
                      historyLoading ||
                      !ready
                    }
                    aria-label="Send message"
                  >
                    {busy ? "…" : "↑"}
                  </button>
                </div>
              </form>
              <div className="composer-caption">
                <span>Enter to send · Shift + Enter for a new line</span>
                <span>AI can make mistakes. Check important details.</span>
              </div>
            </footer>
          </>
        )}
      </main>
    </div>
  );
}
