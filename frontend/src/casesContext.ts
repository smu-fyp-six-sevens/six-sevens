import { createContext, useContext } from 'react';
import type { CaseRecord } from './App';

// The officer's loaded cases, for components that sit outside a route's props (the header search).
export const CasesContext = createContext<CaseRecord[]>([]);

export function useCases(): CaseRecord[] {
  return useContext(CasesContext);
}
