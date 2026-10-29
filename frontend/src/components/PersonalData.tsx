import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { jsonRequest, request } from "../lib/api";
import Knowledge from "./Knowledge";
import type { Preference, StoredFile, Task } from "../lib/api";
import ActivityLog from "./ActivityLog";
import DataDialog from "./DataDialog";

type Props = {
  page: "preferences" | "files";
  conversationId: string | null;
  tasks: Task[];
  onAttach: (file: StoredFile) => void;
};

export default function PersonalData({
  page,
  conversationId,
  tasks,
  onAttach,
}: Props) {
  const [preferences, setPreferences] = useState<Preference[]>([]);
  const [files, setFiles] = useState<StoredFile[]>([]);
  const [workspace, setWorkspace] = useState<
    { path: string; kind: string; size_bytes?: number }[]
  >([]);
  const [directory, setDirectory] = useState(".");
  const [truncated, setTruncated] = useState(false);
  const [allFiles, setAllFiles] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [key, setKey] = useState("output_style");
  const [value, setValue] = useState("");
  const [keywords, setKeywords] = useState("");
  const [scope, setScope] = useState("");
  const [approved, setApproved] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [format, setFormat] = useState("md");
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState("");
  const [deletion, setDeletion] = useState<{
    kind: "files" | "preferences";
    id: string;
    label: string;
  } | null>(null);
  const query = conversationId ? `conversation_id=${conversationId}` : "";

  async function load() {
    setLoading(true);
    setError("");
    try {
      if (page === "preferences") {
        setPreferences(await request<Preference[]>("/preferences"));
      } else {
        const [saved, listing] = await Promise.all([
          request<StoredFile[]>(
            `/files${!allFiles && query ? `?${query}` : ""}`,
          ),
          request<{ files: typeof workspace; truncated: boolean }>(
            `/workspace/files?directory=${encodeURIComponent(directory)}&${query}`,
          ),
        ]);
        setFiles(saved);
        setWorkspace(listing.files);
        setTruncated(listing.truncated);
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not load saved data.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [page, conversationId, directory, allFiles]);

  async function action(operation: () => Promise<unknown>, message: string) {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await operation();
      setNotice(message);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operation failed.");
    } finally {
      setSaving(false);
    }
  }

  function savePreference(event: FormEvent) {
    event.preventDefault();
    void action(async () => {
      await jsonRequest(
        editing ? `/preferences/${editing}` : "/preferences",
        editing ? "PUT" : "POST",
        { key, value, keywords, conversation_id: scope || null, approved },
      );
      setValue("");
      setKeywords("");
      setApproved(false);
      setEditing(null);
    }, "Your approved preference is saved locally.");
  }

  function saveReport(event: FormEvent) {
    event.preventDefault();
    void action(async () => {
      await jsonRequest(`/reports?${query}`, "POST", {
        title: title || "Report",
        format,
        content,
      });
      setTitle("");
      setContent("");
    }, "Report saved with a new filename.");
  }

  return (
    <section
      className="data-page"
      aria-label={
        page === "preferences" ? "Preferences and memory" : "Files and reports"
      }
    >
      {deletion && (
        <DataDialog
          title={
            deletion.kind === "files" ? "Delete file?" : "Delete preference?"
          }
          message={`Permanently delete ${deletion.label}? ${deletion.kind === "files" ? "The managed file and metadata will be removed. Existing task records remain." : "Future tasks will no longer retrieve this preference. Historical task snapshots remain until their conversation is deleted."}`}
          confirmLabel="Confirm deletion"
          onCancel={() => setDeletion(null)}
          onConfirm={() => {
            const target = deletion;
            setDeletion(null);
            void action(
              () =>
                jsonRequest(
                  `/${target.kind}/${target.id}?confirm=true`,
                  "DELETE",
                ),
              "Saved item deleted.",
            );
          }}
        />
      )}
      <div className="data-page-heading">
        <div>
          <span className="eyebrow">SAVED ON YOUR LAPTOP</span>
          <h2>
            {page === "preferences"
              ? "Preferences & memory"
              : "Files & reports"}
          </h2>
        </div>
        <button disabled={loading || saving} onClick={() => void load()}>
          Refresh data
        </button>
      </div>
      {page !== "preferences" && <Knowledge />}
      {loading && <p role="status">Loading saved data…</p>}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="data-notice" role="status">
          {notice}
        </p>
      )}
      {page === "preferences" ? (
        <>
          <p>
            Only preferences you explicitly approve are saved. Conversation
            preferences take priority over global ones. Keywords optionally
            limit retrieval to matching tasks.
          </p>
          <form className="data-form" onSubmit={savePreference}>
            <label>
              Preference
              <select
                value={key}
                onChange={(event) => setKey(event.target.value)}
              >
                <option value="output_style">Output style</option>
                <option value="units">Preferred units</option>
                <option value="report_format">
                  Report format (md, txt, csv, json)
                </option>
                <option value="workspace_directory">
                  Default workspace subdirectory
                </option>
                <option value="task_instructions">
                  Frequent task instructions
                </option>
              </select>
            </label>
            <label>
              Value
              <textarea
                required
                maxLength={800}
                value={value}
                onChange={(event) => {
                  setValue(event.target.value);
                  setApproved(false);
                }}
              />
            </label>
            <label>
              Match keywords (optional)
              <input
                maxLength={200}
                value={keywords}
                onChange={(event) => setKeywords(event.target.value)}
                placeholder="sales revenue reports"
              />
            </label>
            <label>
              Scope
              <select
                value={scope}
                onChange={(event) => setScope(event.target.value)}
              >
                <option value="">All my conversations</option>
                {conversationId && (
                  <option value={conversationId}>Current conversation</option>
                )}
                {scope && scope !== conversationId && (
                  <option value={scope}>
                    Saved conversation {scope.slice(0, 8)}
                  </option>
                )}
              </select>
            </label>
            <p className="data-help">
              Workspace values are relative folders inside the configured EVOLVE
              workspace. They cannot grant access outside it. Newest applicable
              preference for each key is used.
            </p>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={approved}
                onChange={(event) => setApproved(event.target.checked)}
                required
              />{" "}
              I approve saving this preference as memory.
            </label>
            <button disabled={saving || !approved} type="submit">
              {editing ? "Update preference" : "Save preference"}
            </button>
            {editing && (
              <button
                type="button"
                onClick={() => {
                  setEditing(null);
                  setValue("");
                  setApproved(false);
                }}
              >
                Cancel edit
              </button>
            )}
          </form>
          {!loading && !preferences.length && (
            <p className="data-empty">No saved preferences yet.</p>
          )}
          <div className="data-records">
            {preferences.map((pref) => (
              <article key={pref.id}>
                <strong>{pref.key.replaceAll("_", " ")}</strong>
                <p>{pref.value}</p>
                <small>
                  {pref.conversation_id
                    ? `Conversation ${pref.conversation_id.slice(0, 8)}`
                    : "All conversations"}
                  {pref.keywords && ` · Keywords: ${pref.keywords}`}
                </small>
                <div className="record-actions">
                  <button
                    disabled={saving}
                    onClick={() => {
                      setEditing(pref.id);
                      setKey(pref.key);
                      setValue(pref.value);
                      setKeywords(pref.keywords);
                      setScope(pref.conversation_id || "");
                      setApproved(false);
                    }}
                  >
                    Edit
                  </button>
                  <button
                    disabled={saving}
                    onClick={() =>
                      setDeletion({
                        kind: "preferences",
                        id: pref.id,
                        label: pref.key.replaceAll("_", " "),
                      })
                    }
                  >
                    Delete preference
                  </button>
                </div>
              </article>
            ))}
          </div>
        </>
      ) : (
        <>
          <p>
            Uploads and reports remain until you explicitly delete them. New
            conversations do not inherit attachments. Shared workspace documents
            can be read within the configured root.
          </p>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={allFiles}
              onChange={(event) => setAllFiles(event.target.checked)}
            />{" "}
            Show files from all conversations, including detached files
          </label>
          {!conversationId && (
            <p>
              Create or select a conversation to attach files or create reports.
            </p>
          )}
          {!loading && !files.length && (
            <p className="data-empty">No registered files in this view.</p>
          )}
          <div className="data-records">
            {files.map((file) => (
              <article key={file.id}>
                <strong>{file.metadata.title || file.filename}</strong>
                <p>
                  {file.kind} · {file.size_bytes.toLocaleString()} bytes ·{" "}
                  {file.available ? "Available" : "Missing on disk"}
                </p>
                <small>
                  {file.relative_path} ·{" "}
                  {file.conversation_id
                    ? `Conversation ${file.conversation_id.slice(0, 8)}`
                    : "Detached / legacy task"}
                </small>
                <div className="record-actions">
                  {file.kind === "upload" &&
                    file.conversation_id === conversationId && (
                      <button
                        disabled={!file.available || saving}
                        onClick={() => onAttach(file)}
                      >
                        Attach saved file
                      </button>
                    )}
                  {file.kind === "report" &&
                    file.conversation_id === conversationId &&
                    file.available && (
                      <a
                        href={`/api/reports/${file.id}/download?${query}`}
                        download
                      >
                        Download report
                      </a>
                    )}
                  <button
                    disabled={saving}
                    onClick={() =>
                      setDeletion({
                        kind: "files",
                        id: file.id,
                        label: file.filename,
                      })
                    }
                  >
                    Delete file
                  </button>
                </div>
              </article>
            ))}
          </div>
          <details className="data-section">
            <summary>Create a report</summary>
            <form className="data-form" onSubmit={saveReport}>
              <label>
                Report title
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  maxLength={100}
                />
              </label>
              <label>
                Report format
                <select
                  value={format}
                  onChange={(event) => setFormat(event.target.value)}
                >
                  <option value="md">Markdown</option>
                  <option value="txt">Text</option>
                  <option value="json">JSON</option>
                  <option value="csv">CSV</option>
                </select>
              </label>
              <label>
                Report content
                <textarea
                  required
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  maxLength={24000}
                  rows={6}
                />
              </label>
              <button disabled={saving || !conversationId}>
                Create new report
              </button>
            </form>
          </details>
          <details className="data-section" open>
            <summary>Shared workspace files</summary>
            <div className="record-actions">
              <label>
                Relative directory
                <input
                  value={directory}
                  onChange={(event) => setDirectory(event.target.value)}
                  maxLength={240}
                />
              </label>
            </div>
            {!workspace.length && !loading && (
              <p>
                No supported files or folders here. Place .txt, .md, .csv or
                .json documents in your configured workspace.
              </p>
            )}
            {workspace.map((file) => (
              <div className="workspace-row" key={file.path}>
                <span>
                  {file.path}{" "}
                  {file.kind === "directory"
                    ? "/"
                    : `· ${file.size_bytes} bytes`}
                </span>
                <button
                  disabled={saving}
                  onClick={() =>
                    file.kind === "directory"
                      ? setDirectory(file.path)
                      : void action(async () => {
                          const result = await request<{ content: string }>(
                            `/workspace/read?path=${encodeURIComponent(file.path)}&${query}`,
                          );
                          setPreview(result.content);
                        }, "Workspace file read.")
                  }
                >
                  {file.kind === "directory" ? "Open folder" : "Read file"}
                </button>
              </div>
            ))}
            {truncated && <p>Listing reached the configured file limit.</p>}
            {preview && <pre className="file-preview">{preview}</pre>}
          </details>
          <details className="data-section" open>
            <summary>Current conversation task history</summary>
            {!tasks.length && <p>No recorded tasks yet.</p>}
            {tasks.map((task) => (
              <article className="task-record" key={task.id}>
                <strong>{task.status.replaceAll("_", " ")}</strong>
                <small>
                  {" "}
                  · {new Date(task.created_at).toLocaleString()} ·{" "}
                  {task.duration_ms === null
                    ? "Duration unavailable"
                    : `${(task.duration_ms / 1000).toFixed(1)}s`}
                </small>
                <p className="data-help">Task {task.id}</p>
                {task.error && <p className="upload-error">{task.error}</p>}
                {task.memory.length > 0 && (
                  <details>
                    <summary>Preferences used for this task</summary>
                    {task.memory.map((pref, index) => (
                      <p key={index}>
                        {pref.key}: {pref.value}
                      </p>
                    ))}
                  </details>
                )}
                <ActivityLog events={task.activity} />
                {task.activity.some(
                  (event) =>
                    event.kind === "tool_executed" ||
                    event.kind === "tool_failed",
                ) && (
                  <div className="record-actions">
                    {["json", "csv"].map((exportFormat) => (
                      <button
                        key={exportFormat}
                        disabled={saving}
                        onClick={() =>
                          void action(
                            () =>
                              jsonRequest(
                                `/tasks/${task.id}/export?${query}&format=${exportFormat}`,
                                "POST",
                              ),
                            `Verified results exported as ${exportFormat.toUpperCase()}.`,
                          )
                        }
                      >
                        Export results as {exportFormat.toUpperCase()}
                      </button>
                    ))}
                  </div>
                )}
              </article>
            ))}
          </details>
        </>
      )}
    </section>
  );
}
