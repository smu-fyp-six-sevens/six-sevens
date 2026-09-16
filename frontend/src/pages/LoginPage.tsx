import { useState, type FormEvent } from 'react';
import { apiBaseUrl, setToken } from '../lib/api';

interface LoginPageProps {
  onLoggedIn: () => void;
}

export default function LoginPage({ onLoggedIn }: LoginPageProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      const response = await fetch(`${apiBaseUrl}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });

      if (!response.ok) {
        throw new Error('Invalid username or password.');
      }

      const data = (await response.json()) as { token: string };
      setToken(data.token);
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed.');
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <div className="login-background-decoration login-background-decoration--top" />
      <div className="login-background-decoration login-background-decoration--bottom" />

      <section className="login-card">
       <div className="login-brand">
        <div>
          <div className="login-brand-name">GLDB</div>
          <div className="login-brand-subtitle">Fraud Review</div>
        </div>
      </div>

        <div className="login-heading">
          <div className="login-security-icon">⌁</div>

          <div>
            <h1>Welcome back</h1>
            <p>Sign in to continue to the fraud review portal.</p>
          </div>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="username">Username</label>

            <div className="login-input-wrapper">
              <span className="login-input-icon">◯</span>

              <input
                id="username"
                type="text"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoFocus
                disabled={isSubmitting}
                placeholder="Enter your username"
                autoComplete="username"
              />
            </div>
          </div>

          <div className="form-field">
            <label htmlFor="password">Password</label>

            <div className="login-input-wrapper">
              <span className="login-input-icon">●</span>

              <input
                id="password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={isSubmitting}
                placeholder="Enter your password"
                autoComplete="current-password"
              />
            </div>
          </div>

          {error && (
            <div className="login-error" role="alert">
              <span className="login-error-icon">⚠</span>
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            className="login-submit-button"
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <span className="login-spinner" />
                Signing in…
              </>
            ) : (
              <>
                Sign in
                <span className="login-submit-arrow">→</span>
              </>
            )}
          </button>
        </form>

        <div className="login-footer">
          <span className="login-footer-dot" />
          Secure internal review environment
        </div>
      </section>
    </main>
  );
}