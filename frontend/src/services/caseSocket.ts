import { apiBaseUrl, getToken } from '../lib/api';
import type { CaseRequirementCoverage, DocumentRecord } from './uploadService';

export interface CaseStatusSnapshot {
  documents: DocumentRecord[];
  caseSummary: CaseRequirementCoverage;
}

const RECONNECT_DELAY_MS = 2000;

function wsUrl(caseId: string): string {
  const base = apiBaseUrl.replace(/^http/, 'ws');
  const token = getToken() ?? '';
  return `${base}/ws/cases/${encodeURIComponent(caseId)}?token=${encodeURIComponent(token)}`;
}

export interface CaseStatusSubscription {
  unsubscribe: () => void;
  // Once a case has nothing in flight, the backend slows its poll interval
  // down to 30s (see IDLE_POLL_INTERVAL_SECONDS in routers/ws.py) instead
  // of re-checking Supabase every 2s forever for a tab left open on a
  // finished case. Call this right after a successful upload so the
  // "just uploaded, show me progress" moment isn't stuck waiting out that
  // slower interval — any text frame wakes the backend's loop early,
  // content is never inspected. No-op if the socket isn't currently open.
  refresh: () => void;
}

// Replaces the old per-document poll loop (see UploadPage.tsx's former
// pollDocument) with a single WebSocket connection per case: the backend
// pushes a snapshot of every document under the case, plus the case-level
// requirement checklist, whenever anything changes — see routers/ws.py.
// Reconnects on drop since native WebSocket, unlike EventSource, doesn't do
// that itself.
export function subscribeToCaseStatus(
  caseId: string,
  onSnapshot: (snapshot: CaseStatusSnapshot) => void,
): CaseStatusSubscription {
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  function connect() {
    socket = new WebSocket(wsUrl(caseId));

    socket.onmessage = (event) => {
      let payload: { type?: string; case?: unknown; documents?: unknown };
      try {
        payload = JSON.parse(event.data as string);
      } catch {
        return; // malformed message — wait for the next one
      }

      if (payload.type === 'snapshot') {
        onSnapshot({
          documents: payload.documents as DocumentRecord[],
          caseSummary: payload.case as CaseRequirementCoverage,
        });
      }
    };

    socket.onclose = () => {
      if (closed) return;
      reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
    };
  }

  connect();

  return {
    unsubscribe: () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    },
    refresh: () => {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send('refresh');
      }
    },
  };
}
