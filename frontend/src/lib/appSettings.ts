import { createContext, useContext } from 'react';

export interface AppSettingsState {
  // Whether officers review detection findings (and see the precision and recall they give). Null while loading.
  findingReviewsEnabled: boolean | null;
  // Whether the signed-in officer can change the settings (backend ADMIN_USERS).
  isAdmin: boolean;
  // Admin only. Resolves once saved; rejects with the backend's reason.
  setFindingReviewsEnabled: (enabled: boolean) => Promise<void>;
}

export const AppSettingsContext = createContext<AppSettingsState>({
  findingReviewsEnabled: null,
  isAdmin: false,
  setFindingReviewsEnabled: async () => {},
});

// The app-wide settings (components/AppSettingsProvider.tsx loads them once per login).
export function useAppSettings(): AppSettingsState {
  return useContext(AppSettingsContext);
}

// True only once the setting has loaded as on: review controls and metrics stay hidden while it loads, rather
// than flashing up and disappearing.
export function useFindingReviewsOn(): boolean {
  return useAppSettings().findingReviewsEnabled === true;
}
