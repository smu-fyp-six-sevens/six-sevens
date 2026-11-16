import { useEffect, useRef, useState } from 'react';
import type { AdditionalDocument } from '../../services/uploadService';
import { ADDITIONAL_DOCUMENT_TYPES, REQUIRED_DOCUMENT_TYPES } from '../../services/uploadService';
import { missingAdditional, missingRequirements, type Coverage } from './documentChecklist';
import { requestKey, requestedDocumentName } from './requestedDocument';
import { Icon } from '../../components/Icon';

// Lets the pointer cross the gap between the button and the panel without closing it.
const CLOSE_DELAY_MS = 150;

// The case's checklist in its header: every required document, missing or received, and what the case analysis asked
// for, so the documents table lists only real files.
export function CaseChecklistButton({
  fulfilledRequirements,
  coverage,
  requestedDocuments,
  onOpenRequest,
  defaultOpen = false,
}: {
  fulfilledRequirements: Set<string>;
  coverage: Coverage;
  requestedDocuments: AdditionalDocument[];
  // Goes to one request on the Additional Documents tab (by requestKey), where it says why it is asked for.
  onOpenRequest?: (key: string) => void;
  // Open on arrival: the officer came from the Additional Documents tab to upload what it asks for.
  defaultOpen?: boolean;
}) {
  // Hover shows it; a click keeps it open until a click outside or Escape.
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(defaultOpen);
  const open = hovered || pinned;
  const closeTimer = useRef<number | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const missing = missingRequirements(fulfilledRequirements, coverage);
  const missingValues = new Set(missing.map((requirement) => requirement.value));
  // Received in full: one met only in part (an ID missing a side, an address with no matching ID) is listed as missing.
  const received = REQUIRED_DOCUMENT_TYPES.filter((r) => fulfilledRequirements.has(r.value) && !missingValues.has(r.value));
  const optionalMissing = new Set(missingAdditional(fulfilledRequirements));
  const requiredTotal = REQUIRED_DOCUMENT_TYPES.length;
  const requiredReceived = REQUIRED_DOCUMENT_TYPES.filter((r) => fulfilledRequirements.has(r.value)).length;

  const cancelClose = () => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };
  const show = () => {
    cancelClose();
    setHovered(true);
  };
  const hideSoon = () => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => setHovered(false), CLOSE_DELAY_MS);
  };
  useEffect(() => cancelClose, []);

  useEffect(() => {
    if (!open) return;
    const close = () => {
      setPinned(false);
      setHovered(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && close();
    const onPointer = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) close();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointer);
    };
  }, [open]);

  const complete = missing.length === 0 && requestedDocuments.length === 0;

  return (
    <div ref={rootRef} className="doc-checklist" data-testid="document-checklist" onMouseEnter={show} onMouseLeave={hideSoon}>
      <button
        type="button"
        className={`doc-checklist-button${complete ? ' doc-checklist-button--complete' : ''}`}
        aria-expanded={open}
        aria-controls="doc-checklist-panel"
        onClick={() => {
          // A click on a pinned checklist closes it outright, even with the pointer still over it.
          if (pinned) {
            setPinned(false);
            setHovered(false);
          } else {
            setPinned(true);
          }
        }}
      >
        <Icon name={complete ? 'check' : 'documents'} size={16} />
        <span>Checklist</span>
        {missing.length > 0 && <span className="doc-checklist-badge doc-checklist-badge--missing">{missing.length} missing</span>}
        {requestedDocuments.length > 0 && (
          <span className="doc-checklist-badge doc-checklist-badge--requested">{requestedDocuments.length} requested</span>
        )}
        {complete && <span className="doc-checklist-badge doc-checklist-badge--complete">Complete</span>}
      </button>

      {open && (
        <div id="doc-checklist-panel" className="doc-checklist-panel" role="dialog" aria-label="Document checklist">
          <div className="doc-checklist-head">
            <strong>Checklist</strong>
            <span>{requiredReceived} of {requiredTotal} required</span>
          </div>
          <span className="doc-checklist-bar" aria-hidden="true">
            <i style={{ width: `${(requiredReceived / requiredTotal) * 100}%` }} />
          </span>

          {missing.length > 0 && (
            <section className="doc-checklist-group doc-checklist-group--missing">
              <h3>Missing ({missing.length})</h3>
              <ul>
                {missing.map((requirement) => (
                  <li key={requirement.value} data-testid="missing-document">
                    <span className="doc-checklist-dot" aria-hidden="true" />
                    <span>
                      <span className="doc-checklist-name">{requirement.label}</span>
                      <span className="doc-checklist-hint">{requirement.hint}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {requestedDocuments.length > 0 && (
            <details className="doc-checklist-group doc-checklist-group--requested" open>
              <summary>
                <svg className="group-chevron" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M9 6l6 6-6 6" />
                </svg>
                Requested by the case analysis ({requestedDocuments.length})
              </summary>
              <ul>
                {requestedDocuments.map((item) => (
                  <li key={requestKey(item)} data-testid="requested-document">
                    <span className="doc-checklist-dot" aria-hidden="true" />
                    <span>
                      <span className="doc-checklist-name">{requestedDocumentName(item.title)}</span>
                      {onOpenRequest ? (
                        <button type="button" className="doc-checklist-link" onClick={() => onOpenRequest(requestKey(item))}>
                          {item.title} <span aria-hidden="true">→</span>
                        </button>
                      ) : (
                        <span className="doc-checklist-hint">{item.title}</span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {received.length > 0 && (
            <section className="doc-checklist-group doc-checklist-group--received">
              <h3>Uploaded ({received.length})</h3>
              <ul>
                {received.map((requirement) => (
                  <li key={requirement.value} data-testid="received-document">
                    <span className="doc-checklist-icon doc-checklist-tick" aria-hidden="true"><Icon name="check" size={12} /></span>
                    <span className="doc-checklist-name">{requirement.label}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <details className="doc-checklist-optional">
            <summary>
              <svg className="group-chevron" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 6l6 6-6 6" />
              </svg>
              Optional ({ADDITIONAL_DOCUMENT_TYPES.length - optionalMissing.size} of {ADDITIONAL_DOCUMENT_TYPES.length} uploaded)
            </summary>
            <ul>
              {/* Received first, ticked; the rest after, waiting. */}
              {[...ADDITIONAL_DOCUMENT_TYPES]
                .sort((a, b) => Number(optionalMissing.has(a.label)) - Number(optionalMissing.has(b.label)))
                .map(({ value, label }) => {
                  const isReceived = !optionalMissing.has(label);
                  return (
                    <li key={value} data-testid="optional-document">
                      {isReceived ? (
                        <span className="doc-checklist-icon doc-checklist-tick" title="Uploaded"><Icon name="check" size={12} /></span>
                      ) : (
                        <span className="doc-checklist-icon doc-checklist-pending" title="Not uploaded">
                          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <circle cx="12" cy="12" r="9" />
                            <path d="M12 7v5l3 2" />
                          </svg>
                        </span>
                      )}
                      <span className={isReceived ? 'doc-checklist-name' : ''}>{label}</span>
                      <span className="sr-only">{isReceived ? 'uploaded' : 'not uploaded'}</span>
                    </li>
                  );
                })}
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}
