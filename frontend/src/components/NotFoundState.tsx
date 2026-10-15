import { useNavigate } from 'react-router-dom';

import AppHeader from './AppHeader';

interface NotFoundStateProps {
  title: string;
  description: string;
  hints?: string[];
}

export default function NotFoundState({
  title,
  description,
  hints = [],
}: NotFoundStateProps) {
  const navigate = useNavigate();

  return (
    <main className="shell">
      <AppHeader />

      <section className="not-found" aria-labelledby="not-found-title">
        <div className="not-found__icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
            <path d="M14 3v5h5" />
            <circle cx="11.5" cy="14" r="2.5" />
            <path d="m13.5 16 2 2" />
          </svg>
        </div>

        <p className="eyebrow">Error 404</p>
        <h1 id="not-found-title" className="not-found__title">{title}</h1>
        <p className="not-found__desc">{description}</p>
        <p className="not-found__desc not-found__prompt">
          Try going back to the case queue.
        </p>

        <div className="not-found__actions">
          <button
            type="button"
            className="create-case-btn not-found__primary"
            onClick={() => navigate('/cases')}
          >
            Go to case queue
          </button>
        </div>

        {hints.length > 0 && (
          <ul className="not-found__hints">
            {hints.map((hint) => (
              <li key={hint}>{hint}</li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
