import { apiBaseUrl, authFetch } from '../lib/api';
import type { CaseRequirementCoverage, DocumentRecord } from './uploadService';

export interface CaseStatusSnapshot {
  documents: DocumentRecord[];
  caseSummary: CaseRequirementCoverage;
}

const RECONNECT_DELAY_MS = 2000;

// A browser's native WebSocket can't set an Authorization header on the
// handshake, so the long-lived bearer token can't safely ride in the
// connection URL — Cloud Run's default access logging captures full
// request URLs, which would put that credential in plaintext logs on every
// connection attempt. Instead, mint a short-lived, single-use ticket via
// the normal (header-authenticated) POST /ws/tickets first, and only put
// that in the WS URL. See backend/app/auth.py's WS_TICKET_TTL_SECONDS.
async function fetchWsTicket(): Promise<string> {
  const response = await authFetch('/ws/tickets', { method: 'POST' });
  if (!response.ok) {
    throw new Error('Failed to obtain a WebSocket ticket');
  }
  const body = (await response.json()) as { ticket: string };
  return body.ticket;
}

function wsUrl(caseId: string, ticket: string): string {
  const base = apiBaseUrl.replace(/^http/, 'ws');
  return `${base}/ws/cases/${encodeURIComponent(caseId)}?ticket=${encodeURIComponent(ticket)}`;
}

export interface CaseStatusSubscription {
  unsubscribe: () => void;
  // Independent of whatever the Realtime-driven backend push is doing —
  // sends a bare text frame the backend treats as "build and send a
  // snapshot right now" (see routers/ws.py), useful for the moment right
  // after an upload, before the new row's insert has necessarily
  // propagated through Supabase Realtime yet. Content is never inspected.
  // No-op if the socket isn't currently open.
  refresh: () => void;
}

// One WebSocket connection per case: the backend pushes a snapshot of
// every document under the case, plus the case-level requirement
// checklist, whenever anything changes — fed by a Supabase Realtime
// subscription on the backend, not by polling (see WS.MD). Reconnects on
// drop since native WebSocket, unlike EventSource, doesn't do that itself;
// every (re)connection gets a fresh ticket (tickets are single-use) and an
// immediate snapshot from the backend, which is what closes the
// missed-event gap a dropped connection would otherwise leave.
//
// onConnectionChange is optional and purely cosmetic (e.g. a LIVE/OFFLINE
// badge) — reconnection itself doesn't depend on it, so a caller that
// doesn't care about connection state can omit it entirely.
export function subscribeToCaseStatus(
  caseId: string,
  onSnapshot: (snapshot: CaseStatusSnapshot) => void,
  onConnectionChange?: (connected: boolean) => void,
): CaseStatusSubscription {
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  function scheduleReconnect() {
    onConnectionChange?.(false);
    if (closed) return;
    reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
  }

  function connect() {
    fetchWsTicket()
      .then((ticket) => {
        if (closed) return;

        socket = new WebSocket(wsUrl(caseId, ticket));

        socket.onopen = () => {
          onConnectionChange?.(true);
        };

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

        socket.onclose = scheduleReconnect;
      })
      .catch(scheduleReconnect);
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
