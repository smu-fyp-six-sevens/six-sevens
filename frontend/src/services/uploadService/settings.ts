import { assertOk } from './request';
import { authFetch } from '../../lib/api';
import type { AppSettings } from './types';

// App-wide settings, and whether the signed-in officer is an admin who can change them.
export async function getAppSettings(): Promise<AppSettings> {
  const response = await authFetch('/settings');

  await assertOk(response, 'Failed to load the settings');

  return (await response.json()) as AppSettings;
}

// Admin only: turns finding reviews (marking findings correct or wrong, and the metrics they give) on or off.
export async function setFindingReviewsEnabled(enabled: boolean): Promise<AppSettings> {
  const response = await authFetch('/settings/finding-reviews', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });

  await assertOk(response, 'Failed to save the setting');

  return (await response.json()) as AppSettings;
}
