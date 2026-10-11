import { useState } from "react";
import { account, ApiError, type Project, type User } from "../api";
import { setToken } from "../auth";
import { Logo } from "./Sidebar";

interface LoginProps {
  onSignedIn: (user: User) => void;
}

/** Shown only when the deployment sets GEO_AUTH_REQUIRED=true. */
export function LoginScreen({ onSignedIn }: LoginProps) {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") await account.register(email, password);
      const t = await account.login(email, password);
      setToken(t.access_token);
      onSignedIn(await account.me());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="overlay login" onSubmit={submit}>
        <div className="brand login-brand">
          <Logo />
          <div>
            <h1>
              Traverse<em>.</em>
            </h1>
            <p>Survey-grade measurement</p>
          </div>
        </div>
        <h2>{mode === "signin" ? "Sign in" : "Create an account"}</h2>
        <label className="inline-field">
          Email
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="inline-field">
          Password
          <input
            type="password"
            autoComplete={mode === "signin" ? "current-password" : "new-password"}
            minLength={8}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <div className="alert">{error}</div>}
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "…" : mode === "signin" ? "Sign in" : "Create account"}
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => {
            setMode(mode === "signin" ? "signup" : "signin");
            setError(null);
          }}
        >
          {mode === "signin" ? "No account? Create one" : "Have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}

interface ProjectBarProps {
  user: User;
  projects: Project[];
  current: string | null;
  onSelect: (id: string | null) => void;
  onCreate: (name: string) => void;
  onSignOut: () => void;
}

export function ProjectBar({ user, projects, current, onSelect, onCreate, onSignOut }: ProjectBarProps) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  return (
    <div className="section project-bar">
      <div className="who">
        <span className="mono" title={user.email}>
          {user.email}
        </span>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onSignOut}>
          Sign out
        </button>
      </div>
      {adding ? (
        <form
          className="new-project"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) onCreate(name.trim());
            setAdding(false);
            setName("");
          }}
        >
          <input autoFocus placeholder="Project name, e.g. North pit" value={name} onChange={(e) => setName(e.target.value)} />
          <button type="submit" className="btn btn-sm btn-primary">
            Add
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setAdding(false)}>
            Cancel
          </button>
        </form>
      ) : (
        <div className="project-select">
          <select value={current ?? ""} onChange={(e) => onSelect(e.target.value || null)} aria-label="Project">
            <option value="">All my files</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.files + p.rasters})
              </option>
            ))}
          </select>
          <button type="button" className="btn btn-sm" onClick={() => setAdding(true)}>
            New project
          </button>
        </div>
      )}
    </div>
  );
}
