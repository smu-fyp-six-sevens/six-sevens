// Shared bearer-token auth (see backend/app/auth.py) — every backend route
// except /health and /auth/login requires this. authFetch is a drop-in
// replacement for fetch() that attaches the token and, on a 401 (missing,
// expired, or never-logged-in), clears it and bounces to /login.

export const apiBaseUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

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

// Who the backend issued the token to (the "sub" in its payload), or null when there is no
// token or it isn't one of ours. Display only: the backend reads the token itself.
export function getUsername(): string | null {
  try {
    const payload = getToken()?.split('.')[0];
    if (!payload) return null;

    const { sub } = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));

    return typeof sub === 'string' && sub ? sub : null;
  } catch {
    return null;
  }
}

export async function authFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  // fetch only throws when the request never got a response (server down, offline) or was aborted. No response is
  // handed on as a failed response (status 0), so the caller's assertOk words it like a 5xx; an abort still throws.
  const response = await fetch(`${apiBaseUrl}${path}`, { ...options, headers }).catch((error: unknown) => {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    console.error(`No response from ${path}:`, error);
    return Response.error();
  });

  if (response.status === 401) {
    clearToken();
    if (window.location.pathname !== '/login') {
      window.location.assign('/login');
    }
  }

  return response;
}
