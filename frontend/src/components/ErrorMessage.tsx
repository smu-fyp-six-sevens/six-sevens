import { splitMessage } from '../utils/errorText';

// An error the officer can act on: what failed in bold, what to do under it. `inline` sits beside a field or a
// button, without the box. A whole page that couldn't load uses LoadErrorState instead.
export function ErrorMessage({
  message,
  inline = false,
  className,
  id,
}: {
  message: string;
  inline?: boolean;
  // Placement only (margins); the look is .error-message's.
  className?: string;
  id?: string;
}) {
  const [title, hint] = splitMessage(message);
  const classes = ['error-message', inline && 'error-message--inline', className].filter(Boolean).join(' ');
  return (
    <div id={id} className={classes} role="alert">
      <svg className="error-message-icon" viewBox="0 0 20 20" aria-hidden="true">
        <circle cx="10" cy="10" r="8.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10 5.75v5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        <circle cx="10" cy="13.9" r="1.05" fill="currentColor" />
      </svg>
      <div className="error-message-body">
        <strong className="error-message-title">{title}</strong>
        {/* The space keeps the two sentences apart when the message is read or copied as text. */}
        {hint && ' '}
        {hint && <span className="error-message-hint">{hint}</span>}
      </div>
    </div>
  );
}
