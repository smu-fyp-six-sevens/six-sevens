import { useEffect, useRef, useState } from 'react';
import {
  hasVisibleChildren,
  rootOf,
  visibleRows,
  type BuiltTrace,
  type TraceNode,
  type TraceRow,
} from './buildTrace';
import { KindIcon } from './KindIcon';
import { TraceDetails } from './TraceDetails';
import { durationTone, formatCost, formatSeconds, formatTokens } from './format';

type View = 'tree' | 'timeline';

function TimelineBar({ node, root }: { node: TraceNode; root: TraceNode }) {
  const span = Math.max(root.end - root.start, 0.001);
  const left = Math.min(Math.max(((node.start - root.start) / span) * 100, 0), 100);
  if (node.kind === 'event') {
    return (
      <span className="trace-timeline" aria-hidden="true">
        <span className="trace-timeline-dot" style={{ left: `${left}%` }} />
      </span>
    );
  }
  const width = Math.max(((node.end - node.start) / span) * 100, 0.8);
  return (
    <span className="trace-timeline" aria-hidden="true">
      <span className={`trace-timeline-bar trace-timeline-bar--${node.kind}`} style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }} />
    </span>
  );
}

function Row({
  row, view, selected, expanded, expandable, root, onSelect, onToggle,
}: {
  row: TraceRow;
  view: View;
  selected: boolean;
  expanded: boolean;
  expandable: boolean;
  root: TraceNode;
  onSelect: () => void;
  onToggle: () => void;
}) {
  const { node } = row;
  const seconds = node.end - node.start;
  const tokens = node.totals.inputTokens + node.totals.outputTokens;
  // A run counts its audit's failed validations; any other span, the ones that happened in it.
  const failures = node.kind === 'run' ? node.totals.failures : node.kind === 'event' ? 0 : node.failures.length;
  return (
    <div
      role="treeitem"
      aria-level={row.depth + 1}
      aria-selected={selected}
      aria-expanded={expandable ? expanded : undefined}
      className={`trace-row${selected ? ' trace-row--selected' : ''}${node.error ? ' trace-row--error' : ''}${node.kind === 'event' ? ' trace-row--step' : ''}`}
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
        {view === 'tree' ? (
          <span className="trace-row-figures">
            {failures > 0 && <span className="trace-chip trace-chip--warn" title="Failed validations">⚠ {failures}</span>}
            {tokens > 0 && node.kind !== 'run' && (
              <span className="trace-chip" title={`${node.totals.inputTokens} in, ${node.totals.outputTokens} out`}>◎ {formatTokens(tokens)}</span>
            )}
            {node.kind !== 'event' && <span className={`trace-duration trace-duration--${durationTone(seconds)}`}>{formatSeconds(seconds)}</span>}
          </span>
        ) : (
          <TimelineBar node={node} root={root} />
        )}
      </button>
      {expandable ? (
        <button
          type="button"
          className="trace-caret"
          aria-label={`${expanded ? 'Collapse' : 'Expand'} ${node.label}`}
          onClick={onToggle}
        >
          {expanded ? '▾' : '▸'}
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
  const [view, setView] = useState<View>('tree');
  const [search, setSearch] = useState('');
  // Steps (ingest, scoring, ...) are what an old trace is made of, so they show when there are no spans.
  const [showSteps, setShowSteps] = useState(!trace.hasSpans);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
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

  const rows = visibleRows(trace.roots, { collapsed, showSteps, search });
  const all = visibleRows(trace.roots, { collapsed: new Set(), showSteps: true, search: '' });
  const selected = all.find((row) => row.node.key === selectedKey)?.node ?? trace.roots[0];
  const toggle = (key: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const totals = trace.totals;
  const spanCount = all.filter((row) => row.node.kind !== 'event').length;

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
            <div><dt>Duration</dt><dd>{formatSeconds(trace.end - trace.start)}</dd></div>
            <div><dt>Claude calls</dt><dd>{totals.calls}</dd></div>
            <div><dt>Tokens</dt><dd>{formatTokens(totals.inputTokens)} in · {formatTokens(totals.outputTokens)} out</dd></div>
            <div><dt>Cost</dt><dd>{formatCost(totals.cost)}</dd></div>
            <div>
              <dt>Failed validations</dt>
              <dd className={totals.failures ? 'trace-warn-text' : undefined}>{totals.failures}</dd>
            </div>
          </dl>
          <button ref={closeRef} type="button" className="trace-close" aria-label="Close trace" onClick={onClose}>✕</button>
        </header>

        <div className="trace-drawer-body">
          <div className="trace-tree-pane">
            <div className="trace-tabs" role="tablist" aria-label="Trace views">
              {(['tree', 'timeline'] as View[]).map((v) => (
                <button
                  key={v}
                  type="button"
                  role="tab"
                  aria-selected={view === v}
                  className={`trace-tab${view === v ? ' trace-tab--active' : ''}`}
                  onClick={() => setView(v)}
                >
                  {v === 'tree' ? 'Trace tree' : 'Timeline'}
                  {v === 'tree' && <span className="trace-tab-count">{spanCount}</span>}
                </button>
              ))}
            </div>
            <div className="trace-tree-tools">
              <input
                type="search"
                className="trace-search"
                placeholder="Search by span name"
                aria-label="Search by span name"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <label className="trace-toggle">
                <input type="checkbox" checked={showSteps} onChange={(event) => setShowSteps(event.target.checked)} />
                Show steps
              </label>
            </div>
            <div className="trace-tree" role="tree" aria-label={`${title} spans`}>
              {rows.length === 0 && <p className="trace-muted trace-empty">No span matches “{search}”.</p>}
              {rows.map((row) => (
                <Row
                  key={row.node.key}
                  row={row}
                  view={view}
                  selected={selected?.key === row.node.key}
                  expanded={!collapsed.has(row.node.key)}
                  expandable={hasVisibleChildren(row.node, showSteps)}
                  root={rootOf(row.node, trace.roots)}
                  onSelect={() => setSelectedKey(row.node.key)}
                  onToggle={() => toggle(row.node.key)}
                />
              ))}
            </div>
          </div>
          {selected && <TraceDetails key={selected.key} node={selected} root={rootOf(selected, trace.roots)} />}
        </div>
      </div>
    </div>
  );
}
