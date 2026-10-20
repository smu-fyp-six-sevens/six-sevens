import { clearToken, getUsername } from '../lib/api';
import { officerLabel } from '../lib/officers';
import BrandLink from './BrandLink';

export default function AppHeader() {
  return (
    <header className="app-header">
      <BrandLink />
      <div className="officer-tag">
        {officerLabel(getUsername())}
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
