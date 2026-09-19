import { clearToken } from '../lib/api';
import BrandLink from './BrandLink';

export default function AppHeader() {
  return (
    <header className="app-header">
      <BrandLink />
      <div className="officer-tag">
        Loan Officer · J. Tan
        <button
          type="button"
          className="signout-btn"
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
