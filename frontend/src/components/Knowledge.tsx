import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { jsonRequest } from "../lib/api";

type Note = { id: string; title: string; chars: number };

export default function Knowledge() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [message, setMessage] = useState("");

  async function load() {
    try {
      setNotes(await jsonRequest<Note[]>("/knowledge", "GET"));
    } catch {
      setNotes([]);
    }
  }
  useEffect(() => {
    void load();
  }, []);

  async function add(event: FormEvent) {
    event.preventDefault();
    try {
      await jsonRequest("/knowledge", "POST", { title, content });
      setTitle("");
      setContent("");
      setMessage("Saved. The agent can now search it.");
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not save the note.");
    }
  }

  async function remove(id: string) {
    await jsonRequest(`/knowledge/${id}`, "DELETE");
    await load();
  }

  async function readFile(file: File | undefined) {
    if (!file) return;
    setContent((await file.text()).slice(0, 20000));
    if (!title) setTitle(file.name.replace(/\.[^.]+$/, ""));
  }

  return (
    <section className="knowledge" aria-label="Knowledge base">
      <h3>Knowledge base (documents the agent can search)</h3>
      <p>
        Add company policies, SOPs or notes. Ask things like “According to our
        refund policy, how long do customers have?”
      </p>
      <form onSubmit={add}>
        <input
          placeholder="Title"
          value={title}
          maxLength={100}
          onChange={(e) => setTitle(e.target.value)}
        />
        <textarea
          rows={4}
          placeholder="Paste text here, or load a .txt / .md file below"
          value={content}
          maxLength={20000}
          onChange={(e) => setContent(e.target.value)}
        />
        <input
          type="file"
          accept=".txt,.md,text/plain,text/markdown"
          onChange={(e) => void readFile(e.target.files?.[0])}
        />
        <button disabled={!title.trim() || !content.trim()}>Save note</button>
      </form>
      {message && <p role="status">{message}</p>}
      <ul>
        {notes.map((n) => (
          <li key={n.id}>
            <span>
              {n.title} <small>({n.chars} chars)</small>
            </span>
            <button type="button" onClick={() => void remove(n.id)}>
              Delete
            </button>
          </li>
        ))}
        {!notes.length && <li>No notes yet.</li>}
      </ul>
    </section>
  );
}
