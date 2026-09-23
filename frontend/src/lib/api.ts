// Shared bearer-token auth (see backend/app/auth.py) — every backend route
// except /health and /auth/login requires this. authFetch is a drop-in
// replacement for fetch() that attaches the token and, on a 401 (missing,
// expired, or never-logged-in), clears it and bounces to /login.

export const apiBaseUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// Hides internal dev-only tools (e.g. the spelling-anomaly test link) from
// the sponsor-facing cloud build — see frontend/Dockerfile and
// .github/workflows/deploy.yml. False (tools shown) unless explicitly baked
// in as "true" at build time.
export const hideDevFeatures = import.meta.env.VITE_HIDE_DEV_FEATURES === 'true';

const TOKEN_KEY = 'authToken';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export async function authFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(`${apiBaseUrl}${path}`, { ...options, headers });

  if (response.status === 401) {
    clearToken();
    if (window.location.pathname !== '/login') {
      window.location.assign('/login');
    }
  }

  return response;
}
