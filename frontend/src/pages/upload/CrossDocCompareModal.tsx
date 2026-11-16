import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import XrayView from '../../components/XrayView';
import type { FindingHighlight } from '../../components/findingHighlights';
import { useDocumentPreviewUrl } from '../../components/useDocumentPreviewUrl';
import { locateValues } from '../../services/uploadService';
import type { CaseRiskFinding, DetectionSeverity, DocumentRecord, LocatedValue } from '../../services/uploadService';

// A cross-document finding with two of its documents open side by side, each with the value it was compared on boxed,
// so the officer can see the disagreement on the documents themselves. A finding resting on more than two documents
// lets the officer pick which two.

const ACRONYMS = new Set(['uen', 'nric', 'id', 'noa', 'iban', 'swift', 'fatca', 'crs']);

// "registered_address" -> "Registered address", "uen" -> "UEN".
function fieldLabel(field: string): string {
  const words = field.split('_').filter(Boolean);
  return words
    .map((word, index) =>
      ACRONYMS.has(word.toLowerCase()) ? word.toUpperCase() : index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word,
    )
    .join(' ');
}

// Quoted values in the finding's sentence ('The UEN is "201812345A" on ..., but "201634564N" on ...'), for a finding
// that cites no plain value (only tables or computed results). A masked identifier (S****123A) can't be found on a page.
function quotedValues(summary: string): string[] {
  return [...summary.matchAll(/"([^"]+)"/g)].map((match) => match[1].trim()).filter((value) => value && !value.includes('*'));
}

interface ValueToFind {
  // Prefixes the value's box ids, so the list above the document can pick them.
  id: string;
  // The field it was read from: the backend looks beside its label on the page first.
  field: string;
  label: string;
  value: string;
}

// The boxes of each value found, one highlight per value.
function highlightsFrom(located: LocatedValue[], values: ValueToFind[], severity: DetectionSeverity): FindingHighlight[] {
  return located.flatMap((place) => {
    const value = values.find((candidate) => candidate.id === place.id);
    if (!value || place.locations.length === 0) return [];
    const [width, height] = place.page_size ?? [];
    return [{
      id: `${value.id}@1`,
      areas: place.locations.map((location) => ({
        page: location.page,
        bbox: location.bbox as [number, number, number, number],
      })),
      units: place.units ?? 'pt',
      pageSize: width && height ? [width, height] : null,
      severity,
      label: value.label,
      summary: value.value,
    }];
  });
}

// What to look for on one document: the values the finding cited from it, or (when it cites no plain value) every
// quoted value, of which only this document's will be found.
function valuesFor(finding: CaseRiskFinding, documentId: string): { values: ValueToFind[]; fromSummary: boolean } {
  const cited = finding.cited_values ?? [];
  if (cited.length > 0) {
    return {
      values: cited
        .filter((value) => value.document_id === documentId)
        .map((value, index) => ({ id: `v${index}`, field: value.field, label: fieldLabel(value.field), value: value.value })),
      fromSummary: false,
    };
  }
  return {
    values: quotedValues(finding.summary).map((value, index) => ({ id: `q${index}`, field: 'value', label: 'From the finding', value })),
    fromSummary: true,
  };
}

function ComparePane({
  document,
  finding,
  severity,
}: {
  document: DocumentRecord;
  finding: CaseRiskFinding;
  severity: DetectionSeverity;
}) {
  const { previewKind, previewUrl, error } = useDocumentPreviewUrl(document.id, document.filename);
  const [located, setLocated] = useState<LocatedValue[] | null>(null);
  const [locateError, setLocateError] = useState<string | null>(null);
  const [active, setActive] = useState<{ id: string; area: number } | null>(null);
  // Rebuilt each render from the same finding and document; the effect and memo below key on those instead.
  const { values, fromSummary } = valuesFor(finding, document.id);

  useEffect(() => {
    let cancelled = false;
    setLocated(null);
    setLocateError(null);
    setActive(null);
    if (values.length === 0) {
      setLocated([]);
      return;
    }
    locateValues(document.id, values.map(({ id, field, value }) => ({ id, field, value })))
      .then((result) => {
        if (!cancelled) setLocated(result);
      })
      .catch(() => {
        if (!cancelled) setLocateError("Couldn't read this document to box the values on it.");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document.id, finding]);

  const highlights = useMemo(
    () => (located ? highlightsFrom(located, values, severity) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [located, severity],
  );
  const notFound = new Set(values.filter((value) => !highlights.some((h) => h.id === `${value.id}@1`)).map((v) => v.id));
  // The boxes of each value, in the order the values are listed, so the bar steps through them the same way.
  const order = values.map((value) => highlights.filter((h) => h.id.startsWith(`${value.id}@`)).map((h) => h.id));
  // From the sentence, every quoted value is tried on each document: only list the ones this document has.
  const listed = fromSummary && located ? values.filter((value) => !notFound.has(value.id)) : values;

  return (
    <section className="compare-pane" aria-label={document.filename} data-testid="compare-pane">
      <header className="compare-pane-header">
        <strong className="compare-pane-filename" title={document.filename}>{document.filename}</strong>
        <ul className="compare-values">
          {listed.map((value) => {
            const boxes = order[values.indexOf(value)];
            const missing = located !== null && notFound.has(value.id);
            const isActive = active !== null && boxes.includes(active.id);
            return (
              <li key={value.id}>
                <button
                  type="button"
                  className={`compare-value${isActive ? ' compare-value--active' : ''}${missing ? ' compare-value--missing' : ''}`}
                  disabled={boxes.length === 0}
                  onClick={() => setActive({ id: boxes[0], area: 0 })}
                  title={
                    missing
                      ? 'This value was read from this document, but it is printed in a different form, or in too many places, to box one'
                      : 'Show on the document'
                  }
                >
                  <span className="compare-value-label">{value.label}</span>
                  <span className="compare-value-text">{value.value}</span>
                  {missing && <span className="compare-value-note">in this document, but couldn't pinpoint it on the page</span>}
                </button>
              </li>
            );
          })}
          {located === null && !locateError && values.length > 0 && <li className="compare-values-note">Finding the values…</li>}
          {locateError && <li className="compare-values-note">{locateError}</li>}
          {located !== null && listed.length === 0 && (
            <li className="compare-values-note">
              {fromSummary || values.length === 0
                ? 'No value from this finding was found on this document.'
                : 'Nothing from this document can be shown on the page.'}
            </li>
          )}
        </ul>
      </header>
      <div className="compare-pane-document">
        <XrayView
          filename={document.filename}
          previewKind={previewKind}
          previewUrl={previewUrl}
          error={error}
          highlights={highlights}
          order={order}
          activeId={active?.id ?? null}
          activeArea={active?.area ?? 0}
          onSelect={(id, area = 0) => setActive(id ? { id, area } : null)}
        />
      </div>
    </section>
  );
}

export default function CrossDocCompareModal({
  finding,
  documents,
  onClose,
}: {
  finding: CaseRiskFinding;
  // The case's documents; the finding's are picked out by id.
  documents: DocumentRecord[];
  onClose: () => void;
}) {
  const involved = (finding.document_ids ?? [])
    .map((id) => documents.find((document) => document.id === id))
    .filter((document): document is DocumentRecord => document !== undefined);
  const [left, setLeft] = useState(involved[0]?.id ?? '');
  const [right, setRight] = useState(involved[1]?.id ?? '');
  const leftDocument = involved.find((document) => document.id === left);
  const rightDocument = involved.find((document) => document.id === right);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const picker = (value: string, other: string, onChange: (id: string) => void, label: string) => (
    <select className="compare-picker-select" aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      {involved
        .filter((document) => document.id !== other)
        .map((document) => (
          <option key={document.id} value={document.id}>
            {document.filename}
          </option>
        ))}
    </select>
  );

  return createPortal(
    <div className="preview-modal-backdrop" onClick={onClose}>
      <div
        className="review-modal compare-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Compare documents"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="preview-modal-header">
          <div className="compare-modal-heading">
            <span className={`risk-severity risk-severity--${finding.severity}`}>{finding.severity}</span>
            <span className="compare-modal-summary">{finding.summary}</span>
          </div>
          <button type="button" className="preview-modal-close" onClick={onClose} aria-label="Close comparison" autoFocus>
            ×
          </button>
        </div>
        {involved.length > 2 && (
          <div className="compare-picker">
            <span>
              This finding rests on {involved.length} documents. Compare
            </span>
            {picker(left, right, setLeft, 'First document')}
            <span>with</span>
            {picker(right, left, setRight, 'Second document')}
          </div>
        )}
        <div className="compare-modal-body">
          {leftDocument && rightDocument ? (
            <>
              <ComparePane key={leftDocument.id} document={leftDocument} finding={finding} severity={finding.severity} />
              <ComparePane key={rightDocument.id} document={rightDocument} finding={finding} severity={finding.severity} />
            </>
          ) : (
            <p className="compare-modal-empty">The documents this finding rests on are no longer in the case.</p>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
