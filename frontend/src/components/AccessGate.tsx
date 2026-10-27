import { useEffect, useState } from "react";
import type { ReactNode } from "react";

type State = "checking" | "open" | "login";

export default function AccessGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>("checking");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/auth/status")
      .then((r) => r.json())
      .then((s) => setState(s.authorized ? "open" : "login"))
      // Older or unreachable backends: let the app show its own connection error.
      .catch(() => setState("open"));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (r.ok) setState("open");
      else setError("Wrong password.");
    } catch {
      setError("Could not reach the backend. It may be waking up; try again.");
    } finally {
      setBusy(false);
    }
  }

  if (state === "open") return <>{children}</>;
  if (state === "checking") return <p className="gate-wait">Connecting…</p>;
  return (
    <form className="gate" onSubmit={submit}>
      <h1>EVOLVE Personal AI</h1>
      <label>
        Access password
        <input
          type="password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <button disabled={busy || !password}>
        {busy ? "Checking…" : "Enter"}
      </button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
