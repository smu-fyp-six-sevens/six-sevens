import { useState } from 'react';
import AppHeader from '../components/AppHeader';
import { ErrorMessage } from '../components/ErrorMessage';
import { useAppSettings } from '../lib/appSettings';

// App-wide settings. Every officer sees them; only an admin can change them (the switch is disabled otherwise).
export default function SettingsPage() {
  const { findingReviewsEnabled, isAdmin, setFindingReviewsEnabled } = useAppSettings();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle(enabled: boolean) {
    setSaving(true);
    setError(null);
    try {
      await setFindingReviewsEnabled(enabled);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save the setting');
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="shell">
      <AppHeader />
      <h1>Settings</h1>

      <section className="settings-card">
        <label className="settings-switch">
          <span className="settings-switch-name">Review findings</span>
          <span className="settings-switch-control">
            <input
              type="checkbox"
              role="switch"
              checked={findingReviewsEnabled ?? false}
              disabled={!isAdmin || saving || findingReviewsEnabled === null}
              onChange={(event) => void toggle(event.target.checked)}
            />
            <span className="settings-switch-track" aria-hidden="true" />
          </span>
        </label>
        {error && <ErrorMessage className="settings-error" message={error} />}
      </section>
    </main>
  );
}
