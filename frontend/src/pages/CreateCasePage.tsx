import { useState } from 'react';
import AppHeader from '../components/AppHeader';
import { authFetch } from '../lib/api';

interface CreateCasePageProps {
  onBack: () => void;
  onCreated: () => void;
}

export default function CreateCasePage({
  onBack,
  onCreated,
}: CreateCasePageProps) {
  const [companyName, setCompanyName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const trimmedName = companyName.trim();

    // Required-field validation
    if (!trimmedName) {
      setError('Company name is required.');
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const response = await authFetch('/cases', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          company_name: trimmedName,
        }),
      });

      if (!response.ok) {
        const detail = await response.text();
        throw new Error(
          `Failed to create case (${response.status}): ${detail}`,
        );
      }

      // Case was created successfully.
      onCreated();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'Failed to create case.',
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="shell">
      <AppHeader />

      <section className="create-case-view">
        <button
          type="button"
          className="back-link"
          onClick={onBack}
          disabled={isSubmitting}
        >
          ← Back to case queue
        </button>

        <p className="eyebrow">New case</p>

        <h1>Create Case</h1>

        <p className="sub">
          Enter the company information to create a new corporate loan case.
        </p>

        <form className="create-case-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="company-name">
              Company name
            </label>

            <input
              id="company-name"
              type="text"
              value={companyName}
              onChange={(event) => {
                setCompanyName(event.target.value);

                if (error) {
                  setError(null);
                }
              }}
              placeholder="Enter company name"
              disabled={isSubmitting}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? 'company-name-error' : undefined}
            />
          </div>

          {error && (
            <div
              id="company-name-error"
              className="form-error"
              role="alert"
            >
              ⚠ {error}
            </div>
          )}

          <div className="create-case-actions">
            <button
              type="button"
              className="back-link"
              onClick={onBack}
              disabled={isSubmitting}
            >
              Cancel
            </button>

            <button
              type="submit"
              className="upload-btn"
              disabled={isSubmitting}
            >
              {isSubmitting ? 'Creating…' : 'Create Case'}
            </button>
          </div>
        </form>
      </section>
    </main>
  );
}