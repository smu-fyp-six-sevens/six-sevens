import { useEffect, useRef, useState } from 'react';
import {
  groupKeys,
  rootOf,
  visibleRows,
  type BuiltTrace,
  type TraceRow,
} from './buildTrace';
import { Chevron } from './Chevron';
import { KindIcon } from './KindIcon';
import { TraceDetails } from './TraceDetails';
import { durationTone, formatCost, formatSeconds, formatTokens } from './format';

function Row({
  row, selected, expanded, expandable, onSelect, onToggle,
}: {
  row: TraceRow;
  selected: boolean;
  expanded: boolean;
  expandable: boolean;
  onSelect: () => void;
  onToggle: () => void;
}) {
  const { node } = row;
  const { cost } = node.totals;
  const seconds = node.seconds ?? node.end - node.start;
  const tokens = node.totals.inputTokens + node.totals.outputTokens;
  // A run counts its audit's failed validations; any other span, the ones that happened in it.
  const failures = node.kind === 'run' || node.group ? node.totals.failures : node.kind === 'event' ? 0 : node.failures.length;
  return (
    <div
      role="treeitem"
      aria-level={row.depth + 1}
      aria-selected={selected}
      aria-expanded={expandable ? expanded : undefined}
      className={`trace-row${selected ? ' trace-row--selected' : ''}${node.error ? ' trace-row--error' : ''}${node.kind === 'event' ? ' trace-row--step' : ''}${node.group ? ' trace-row--group' : ''}`}
    >
      {row.guides.map((more, index) => (
        <span key={index} className={`trace-guide${more ? ' trace-guide--line' : ''}`} aria-hidden="true" />
      ))}
      {row.depth > 0 && <span className={`trace-elbow${row.last ? ' trace-elbow--last' : ''}`} aria-hidden="true" />}
      <button type="button" className="trace-row-main" onClick={onSelect}>
        <KindIcon kind={node.kind} />
        <span className="trace-row-label">
          {node.label}
          {node.subtitle && <span className="trace-row-subtitle">{node.subtitle}</span>}
        </span>
        <span className="trace-row-figures">
          {failures > 0 && <span className="trace-chip trace-chip--warn" title="Failed validations">⚠ {failures}</span>}
          {tokens > 0 && (
            <span className="trace-chip" title={`${node.totals.inputTokens} in, ${node.totals.outputTokens} out`}>
              {formatTokens(tokens)} tokens{cost > 0 ? ` · ${formatCost(cost)}` : ''}
            </span>
          )}
          {node.kind !== 'event' && node.seconds !== 0 && <span className={`trace-duration trace-duration--${durationTone(seconds)}`}>{formatSeconds(seconds)}</span>}
          {node.elapsed !== undefined && (
            <span className={`trace-duration trace-duration--${durationTone(node.elapsed)}`} title="Since the step before">{formatSeconds(node.elapsed)}</span>
          )}
        </span>
      </button>
      {expandable ? (
        <button
          type="button"
          className={`document-row-action-icon collapse-toggle trace-caret${expanded ? '' : ' is-collapsed'}`}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} ${node.label}`}
          onClick={onToggle}
        >
          <Chevron />
        </button>
      ) : (
        <span className="trace-caret" aria-hidden="true" />
      )}
    </div>
  );
}

export function TraceDrawer({
  title, trace, initialKey, onClose,
}: {
  title: string;
  trace: BuiltTrace;
  initialKey: string;
  onClose: () => void;
}) {
  const [search, setSearch] = useState('');
  // The groups start folded, so a run reads as its stages.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(groupKeys(trace.roots)));
  const [selectedKey, setSelectedKey] = useState(initialKey);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const rows = visibleRows(trace.roots, { collapsed, search });
  const all = visibleRows(trace.roots, { collapsed: new Set(), search: '' });
  const selected = all.find((row) => row.node.key === selectedKey)?.node ?? trace.roots[0];
  const toggle = (key: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const totals = trace.totals;
  // Claude calls are recorded on spans, so a trace with none has no figures: not the same as a run that used none.
  const unrecorded = (
    <dd className="trace-muted" title="This trace has no spans, so its Claude usage wasn't recorded.">Not recorded</dd>
  );

  return (
    <div className="trace-overlay" onClick={onClose}>
      <div
        className="trace-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="trace-drawer-head">
          <h2>{title}</h2>
          <dl className="trace-stats">
            <div>
              <dt>Status</dt>
              <dd className={totals.errors ? 'trace-error-text' : 'trace-ok-text'}>
                {totals.errors ? `✕ ${totals.errors} error${totals.errors === 1 ? '' : 's'}` : '✓ OK'}
              </dd>
            </div>
            <div><dt>Duration</dt><dd>{formatSeconds(trace.seconds)}</dd></div>
            <div><dt>Claude calls</dt>{trace.hasSpans ? <dd>{totals.calls}</dd> : unrecorded}</div>
            <div><dt>Tokens</dt>{trace.hasSpans ? <dd>{formatTokens(totals.inputTokens)} in · {formatTokens(totals.outputTokens)} out</dd> : unrecorded}</div>
            <div><dt>Cost</dt>{trace.hasSpans ? <dd>{formatCost(totals.cost)}</dd> : unrecorded}</div>
            <div>
              <dt>Failed validations</dt>
              <dd className={totals.failures ? 'trace-warn-text' : undefined}>{totals.failures}</dd>
            </div>
          </dl>
          <button ref={closeRef} type="button" className="trace-close" aria-label="Close trace" onClick={onClose}>✕</button>
        </header>
        {!trace.hasSpans && (
          <p className="trace-notice">
            This run has no per-step Claude figures, because it was processed before they were recorded. Its cost is on
            the case's Claude Cost tab. Runs from now on show tokens and cost on each step.
          </p>
        )}

        <div className="trace-drawer-body">
          <div className="trace-tree-pane">
            <div className="trace-tree-tools">
              <input
                type="search"
                className="trace-search"
                placeholder="Search steps"
                aria-label="Search steps"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <div className="trace-tree" role="tree" aria-label={`${title} steps`}>
              {rows.length === 0 && <p className="trace-muted trace-empty">No step matches “{search}”.</p>}
              {rows.map((row) => (
                <Row
                  key={row.node.key}
                  row={row}
                  selected={selected?.key === row.node.key}
                  expanded={!collapsed.has(row.node.key)}
                  expandable={row.node.children.length > 0}
                  onSelect={() => {
                    setSelectedKey(row.node.key);
                    if (row.node.group) toggle(row.node.key);
                  }}
                  onToggle={() => toggle(row.node.key)}
                />
              ))}
            </div>
          </div>
          {selected && <TraceDetails node={selected} root={rootOf(selected, trace.roots)} />}
        </div>
      </div>
    </div>
  );
}
