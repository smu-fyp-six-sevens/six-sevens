// The session lives in an HttpOnly cookie the backend sets at login (backend/app/auth.py), so no script on the page
// can read it. authFetch is a drop-in replacement for fetch() that sends the cookie and the X-Requested-With header
// the backend requires on a write (its CSRF guard), and on a 401 (expired or never signed in) forgets the officer and
// bounces to /login. Every request renews the session, so an officer at work is never signed out.

export const apiBaseUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// Who is signed in, for display only (the header, note authorship): never a credential.
const USERNAME_KEY = 'authUser';

// A token an earlier build kept in localStorage: dropped, so it can't be read from there any more.
try {
  localStorage.removeItem('authToken');
} catch {
  // storage unavailable (private mode): nothing stored to drop
}

export function getUsername(): string | null {
  try {
    return localStorage.getItem(USERNAME_KEY) || null;
  } catch {
    return null;
  }
}

export function isSignedIn(): boolean {
  return getUsername() !== null;
}

export function rememberSignIn(username: string): void {
  localStorage.setItem(USERNAME_KEY, username);
}

export function forgetSignIn(): void {
  localStorage.removeItem(USERNAME_KEY);
}

/** Every call to the backend: the session cookie and the CSRF header travel with it. */
export function apiInit(options: RequestInit = {}): RequestInit {
  const headers = new Headers(options.headers);
  headers.set('X-Requested-With', 'fetch');
  return { ...options, headers, credentials: 'include' };
}

/** Clears the session cookie (the backend's job: the page can't touch an HttpOnly cookie), then forgets the officer. */
export async function signOut(): Promise<void> {
  try {
    await fetch(`${apiBaseUrl}/auth/logout`, apiInit({ method: 'POST' }));
  } catch (error) {
    console.error('Sign-out request failed:', error);
  }
  forgetSignIn();
}

export async function authFetch(path: string, options: RequestInit = {}): Promise<Response> {
  // fetch only throws when the request never got a response (server down, offline) or was aborted. No response is
  // handed on as a failed response (status 0), so the caller's assertOk words it like a 5xx; an abort still throws.
  const response = await fetch(`${apiBaseUrl}${path}`, apiInit(options)).catch((error: unknown) => {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    console.error('No response from %s:', path, error);
    return Response.error();
  });

  if (response.status === 401) {
    forgetSignIn();
    if (window.location.pathname !== '/login') {
      window.location.assign('/login');
    }
  }

  return response;
}
