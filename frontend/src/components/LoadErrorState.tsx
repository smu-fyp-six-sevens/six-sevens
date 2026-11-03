import { splitMessage } from '../utils/errorText';

// A page (or the page's main list) that couldn't load, styled like NotFoundState: what failed as the title and what
// to do under it, with the page's Retry button when it has one.
export function LoadErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const [title, description] = splitMessage(message);

  return (
    <section className="not-found not-found--error" role="alert" aria-labelledby="load-error-title">
      <div className="not-found__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7.5v5.5" />
          <path d="M12 16.5h.01" />
        </svg>
      </div>

      <h2 id="load-error-title" className={`not-found__title${description ? '' : ' not-found__title--alone'}`}>
        {title}
      </h2>
      {description && <p className="not-found__desc not-found__prompt">{description}</p>}

      {onRetry && (
        <div className="not-found__actions">
          <button type="button" className="create-case-btn not-found__primary" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}
    </section>
  );
}
