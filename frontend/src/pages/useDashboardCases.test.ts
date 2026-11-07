import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useDashboardCases } from './useDashboardCases';

const api = vi.hoisted(() => ({ listCasesPage: vi.fn() }));

vi.mock('../services/uploadService', () => api);
// The records are passed through as they come: this is about loading and failing, not their shape.
vi.mock('../utils/caseRecord', () => ({ toCaseRecord: (item: unknown) => item }));

const page = (ids: string[]) => ({ items: ids.map((id) => ({ id })), total: ids.length });

describe('useDashboardCases', () => {
  beforeEach(() => {
    api.listCasesPage.mockReset();
  });

  it('loads every case and has no error', async () => {
    api.listCasesPage.mockResolvedValue(page(['case-a', 'case-b']));

    const { result } = renderHook(() => useDashboardCases());

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.cases).toHaveLength(2);
    expect(result.current.error).toBeNull();
  });

  it('says so when the first load fails, instead of showing zero cases as if there were none', async () => {
    api.listCasesPage.mockRejectedValue(new Error('Failed to load cases. Check your connection.'));

    const { result } = renderHook(() => useDashboardCases());

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toBe('Failed to load cases. Check your connection.');
    expect(result.current.cases).toEqual([]);
  });

  it('keeps the cases already shown when a refresh fails, and clears the error when the next one works', async () => {
    api.listCasesPage.mockResolvedValueOnce(page(['case-a']));
    const { result } = renderHook(() => useDashboardCases());
    await waitFor(() => expect(result.current.cases).toHaveLength(1));

    api.listCasesPage.mockRejectedValueOnce(new Error('Failed to load cases.'));
    await act(() => result.current.refresh());
    expect(result.current.error).toBe('Failed to load cases.');
    expect(result.current.cases).toHaveLength(1);

    api.listCasesPage.mockResolvedValueOnce(page(['case-a', 'case-b']));
    await act(() => result.current.refresh());
    expect(result.current.error).toBeNull();
    expect(result.current.cases).toHaveLength(2);
  });
});
