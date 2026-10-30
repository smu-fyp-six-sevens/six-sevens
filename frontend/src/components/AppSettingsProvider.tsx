import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { AppSettingsContext } from '../lib/appSettings';
import { getAppSettings, setFindingReviewsEnabled as saveFindingReviewsEnabled } from '../services/uploadService';

// Loads the app-wide settings once per login. If they can't be loaded, finding reviews stay on, as the backend
// treats them, so an officer isn't left without the review controls the backend still expects.
export function AppSettingsProvider({ isAuthed, children }: { isAuthed: boolean; children: ReactNode }) {
  const [findingReviewsEnabled, setEnabled] = useState<boolean | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    if (!isAuthed) return;
    let cancelled = false;
    getAppSettings()
      .then((settings) => {
        if (cancelled) return;
        setEnabled(settings.finding_reviews_enabled);
        setIsAdmin(settings.is_admin);
      })
      .catch(() => {
        if (!cancelled) setEnabled(true);
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthed]);

  const setFindingReviewsEnabled = useCallback(async (enabled: boolean) => {
    const saved = await saveFindingReviewsEnabled(enabled);
    setEnabled(saved.finding_reviews_enabled);
    setIsAdmin(saved.is_admin);
  }, []);

  const value = useMemo(
    () => ({ findingReviewsEnabled, isAdmin, setFindingReviewsEnabled }),
    [findingReviewsEnabled, isAdmin, setFindingReviewsEnabled],
  );
  return <AppSettingsContext.Provider value={value}>{children}</AppSettingsContext.Provider>;
}
