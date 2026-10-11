// Session state for deployments with GEO_AUTH_REQUIRED=true. In open mode (the
// public demo) there is no token and every helper here is a no-op.

const TOKEN_KEY = "traverse.token";
let token: string | null = null;
let project: string | null = null;

try {
  token = localStorage.getItem(TOKEN_KEY);
} catch {
  /* storage unavailable */
}

export const getToken = () => token;

export function setToken(value: string | null): void {
  token = value;
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

export const getProject = () => project;
export const setProject = (id: string | null) => {
  project = id;
};

export function authHeaders(): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** fetch() with the bearer token attached. */
export function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(input, { ...init, headers: { ...authHeaders(), ...(init.headers ?? {}) } });
}

/**
 * Map tiles and download links can't carry headers, so the API also accepts
 * ?access_token= on GET endpoints.
 */
export function withToken(url: string): string {
  if (!token) return url;
  return `${url}${url.includes("?") ? "&" : "?"}access_token=${encodeURIComponent(token)}`;
}

/** Query suffix restricting lists to the current project. */
export const projectQuery = () => (project ? `&project_id=${encodeURIComponent(project)}` : "");
