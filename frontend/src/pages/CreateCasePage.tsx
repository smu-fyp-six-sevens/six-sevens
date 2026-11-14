import { useState } from 'react';
import AppHeader from '../components/AppHeader';
import { authFetch } from '../lib/api';
import { assertOk } from '../services/uploadService';
import {
  MAX_COMPANY_NAME_LENGTH,
  validateCompanyName,
} from '../utils/caseValidation';
import { ErrorMessage } from '../components/ErrorMessage';
import { CharacterCount } from '../components/CharacterCount';

// The case's path after it is created, as the case summary's progress stages show it.
const NEXT_STEPS = [
  { title: 'Upload documents', detail: 'Each file is classified and its fields extracted.' },
  { title: 'Approve documents', detail: 'Check the extracted fields, then approve each document.' },
  { title: 'Anomaly detection', detail: 'Approved documents are scanned for signs of tampering.' },
  { title: 'Case analysis', detail: 'Documents are cross-checked and the case is given a risk score.' },
];

interface CreateCasePageProps {
  onBack: () => void;
  onCreated: () => void;
}

export default function CreateCasePage({
  onBack,
  onCreated,
}: CreateCasePageProps) {
  const [companyName, setCompanyName] = useState('');
  // Fixed once the case is created: whether its findings are reviewed (precision and recall) before finalising.
  const [reviewsRequired, setReviewsRequired] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const trimmedName = companyName.trim();

    const validationError = validateCompanyName(companyName);
    if (validationError) {
      setError(validationError);
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
          reviews_required: reviewsRequired,
        }),
      });

      await assertOk(response, 'Failed to create case');

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
    <main className="shell shell--create">
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

        <div className="create-case-card">
          <div className="create-case-intro">
            <p className="eyebrow">New case</p>

            <h1>Create Case</h1>

            <p className="sub">
              Enter the company information to create a new corporate loan case.
            </p>
          </div>

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
                placeholder="e.g. Harbor Light Shipping Pte Ltd"
                maxLength={MAX_COMPANY_NAME_LENGTH}
                disabled={isSubmitting}
                autoFocus
                aria-invalid={Boolean(error)}
                aria-describedby={error ? 'company-name-error' : 'company-name-hint'}
              />

              <CharacterCount length={companyName.length} />
              <div className="form-field-hint">
                <span id="company-name-hint">
                  Use the registered name. You can rename the case later.
                </span>
              </div>
            </div>

            <div className="form-field">
              <label className="switch">
                <span>
                  <span className="switch-name">Review findings</span>
                  <span className="switch-hint">
                    Officers mark each finding correct or wrong (precision and recall) before the case can be
                    finalised. This can't be changed after the case is created.
                  </span>
                </span>
                <span className="switch-control">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={reviewsRequired}
                    disabled={isSubmitting}
                    onChange={(event) => setReviewsRequired(event.target.checked)}
                  />
                  <span className="switch-track" aria-hidden="true" />
                </span>
              </label>
            </div>

            {error && <ErrorMessage id="company-name-error" className="form-error" message={error} />}

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
        </div>

        <section className="create-case-next" aria-labelledby="create-case-next-title">
          <h2 id="create-case-next-title">What happens next</h2>
          <ol>
            {NEXT_STEPS.map((step) => (
              <li key={step.title}>
                <strong>{step.title}</strong>
                <span>{step.detail}</span>
              </li>
            ))}
          </ol>
          <p>A GreenLink case ID is assigned when the case is created.</p>
        </section>
      </section>
    </main>
  );
}