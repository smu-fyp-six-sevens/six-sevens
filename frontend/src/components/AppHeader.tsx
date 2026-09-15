import { clearToken } from '../lib/api';

export default function AppHeader() {
  return (
    <header className="app-header">
      <div className="brand">
        GLDB <span>Fraud Review</span>
      </div>
      <div className="officer-tag">
        Loan Officer · J. Tan
        <button
          type="button"
          className="back-link"
          onClick={() => {
            clearToken();
            window.location.assign('/login');
          }}
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
