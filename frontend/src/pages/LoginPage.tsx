import { useState, type FormEvent } from 'react';
import { apiBaseUrl, setToken } from '../lib/api';
import { Icon } from '../components/Icon';

interface LoginPageProps {
  onLoggedIn: () => void;
}

export default function LoginPage({ onLoggedIn }: LoginPageProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      // fetch only throws when the request never got a response (server down, offline).
      const response = await fetch(`${apiBaseUrl}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      }).catch(() => {
        throw new Error("Couldn't reach the server. Check your connection and try again.");
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
      <section className="login-panel" aria-hidden="true">
        <div className="brand login-panel-brand">
          <span className="brand-text">
            GLDB <span>Anomaly Detection Portal</span>
          </span>
        </div>

        <div className="login-panel-body">
          <p className="login-panel-eyebrow">Loan document review</p>
          <h2>Review a case's documents in one place.</h2>

          <ol className="login-panel-steps">
            <li>
              <strong>Upload</strong>
              Documents are classified and their fields extracted.
            </li>
            <li>
              <strong>Check</strong>
              Each document is scanned for tampering and inconsistencies.
            </li>
            <li>
              <strong>Score</strong>
              The case analysis rates risk across every document.
            </li>
          </ol>
        </div>

        <p className="login-panel-footer">Internal use · authorised officers only</p>
      </section>

      <section className="login-form-area">
        <div className="login-card">
          <div className="brand login-card-brand">
            <span className="brand-text">
              GLDB <span>Anomaly Detection Portal</span>
            </span>
          </div>

          <div className="login-heading">
            <h1>Sign in</h1>
            <p>Use your officer account to open your case queue.</p>
          </div>

          <form className="login-form" onSubmit={handleSubmit}>
            <div className="form-field">
              <label htmlFor="username">Username</label>

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

            <div className="form-field">
              <label htmlFor="password">Password</label>

              <div className="login-password-wrapper">
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={isSubmitting}
                  placeholder="Enter your password"
                  autoComplete="current-password"
                />

                <button
                  type="button"
                  className="login-password-toggle"
                  aria-controls="password"
                  aria-pressed={showPassword}
                  onClick={() => setShowPassword((current) => !current)}
                  disabled={isSubmitting}
                >
                  {showPassword ? 'Hide' : 'Show'}
                </button>
              </div>
            </div>

            {error && (
              <div className="login-error" role="alert">
                <span className="login-error-icon"><Icon name="alert" size={16} /></span>
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
        </div>
      </section>
    </main>
  );
}
