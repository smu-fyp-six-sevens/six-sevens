import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { AgentRecord } from '../services/agents';
import { agentGroupLabel } from '../utils/agentGroups';

/* Design-space size of the graph. Nodes are laid out in these units, then the stage is scaled to fit the canvas.
   The height grows with the longest side column (see stageHeight), so every agent stays inside the frame. */
const STAGE_W = 1000;
const MIN_STAGE_H = 600;
const HUB_X = 500;
const HINT_SPACE = 56;
// A side column's agents sit up to COLUMN_GAP apart, never under MIN_COLUMN_GAP (a node and its two-line label).
const COLUMN_GAP = 230;
const MIN_COLUMN_GAP = 140;
// Room above the first agent of a column (its column title) and below the last (its label).
const COLUMN_MARGIN = 110;

const GROUP_DOCS = 'Document processing';
const GROUP_LEAD = 'Fraud detection';
const GROUP_SPECIALISTS = 'Fraud detection specialists';
const GROUP_CASE = 'Case analysis';

type LabelSide = 'above' | 'below';
type EdgeKind = 'handoff' | 'delegate' | 'approval' | 'sendback';
type EdgeShape = 'x' | 'y' | 'bulge-left' | 'bulge-right' | 'loop-right';

interface GraphNode {
  agent: AgentRecord;
  x: number;
  y: number;
  hub: boolean;
  label: LabelSide;
  hw: number;
  hh: number;
  order: number;
}

interface GraphEdge {
  id: string;
  from: string;
  to: string;
  kind: EdgeKind;
  shape: EdgeShape;
}

interface Point { x: number; y: number }

function spread(count: number, center: number, gap: number): number[] {
  return Array.from({ length: count }, (_, index) => center + (index - (count - 1) / 2) * gap);
}

// Tall enough for the longest side column at MIN_COLUMN_GAP, and never shorter than the original 600.
function stageHeight(rows: number): number {
  return Math.max(MIN_STAGE_H, (Math.max(rows, 1) - 1) * MIN_COLUMN_GAP + 2 * COLUMN_MARGIN);
}

// The gap that spreads a column over the stage's height between its margins, at most COLUMN_GAP.
function columnGap(rows: number, height: number): number {
  return rows > 1 ? Math.min(COLUMN_GAP, (height - 2 * COLUMN_MARGIN) / (rows - 1)) : COLUMN_GAP;
}

function buildGraph(agents: AgentRecord[]): { nodes: GraphNode[]; edges: GraphEdge[]; height: number } {
  const docs = agents.filter((agent) => agent.group === GROUP_DOCS);
  const leads = agents.filter((agent) => agent.group === GROUP_LEAD);
  const cases = agents.filter((agent) => agent.group === GROUP_CASE);
  const height = stageHeight(Math.max(docs.length, cases.length));
  const HUB = { x: HUB_X, y: height / 2 };
  const known = new Set([GROUP_DOCS, GROUP_LEAD, GROUP_SPECIALISTS, GROUP_CASE]);
  const hubAgent = leads[0];
  const ring = [
    ...leads.slice(1),
    ...agents.filter((agent) => agent.group === GROUP_SPECIALISTS),
    ...agents.filter((agent) => !known.has(agent.group)),
  ];

  const nodes: GraphNode[] = [];
  let order = 0;
  const satellite = (agent: AgentRecord, x: number, y: number, label: LabelSide) => {
    nodes.push({ agent, x, y, hub: false, label, hw: 34, hh: 34, order: order++ });
  };

  spread(docs.length, HUB.y, columnGap(docs.length, height)).forEach((y, index) => satellite(docs[index], 150, y, 'below'));
  if (hubAgent) nodes.push({ agent: hubAgent, ...HUB, hub: true, label: 'below', hw: 92, hh: 78, order: order++ });

  // Specialists sit on an ellipse around the hub: the first half across the top, the rest across the bottom.
  const top = Math.ceil(ring.length / 2);
  const bottom = ring.length - top;
  ring.forEach((agent, index) => {
    const onTop = index < top;
    const slot = onTop ? index : index - top;
    const slots = onTop ? top : bottom;
    const angle = ((onTop ? -150 : 150) + (onTop ? 1 : -1) * 120 * ((slot + 0.5) / slots)) * (Math.PI / 180);
    satellite(agent, HUB.x + 270 * Math.cos(angle), HUB.y + 205 * Math.sin(angle), onTop ? 'above' : 'below');
  });

  spread(cases.length, HUB.y, columnGap(cases.length, height)).forEach((y, index) => satellite(cases[index], 850, y, 'below'));

  const edges: GraphEdge[] = [];
  const link = (from: AgentRecord, to: AgentRecord, kind: EdgeKind, shape: EdgeShape) => {
    edges.push({ id: `${from.id}->${to.id}`, from: from.id, to: to.id, kind, shape });
  };
  docs.slice(1).forEach((agent, index) => link(docs[index], agent, 'handoff', 'bulge-left'));
  // The extractor sends a document back to the classifier when its type looks wrong (graph.py's reclassify loop).
  const classifier = docs.find((agent) => agent.id === 'document_classifier');
  const extractor = docs.find((agent) => agent.id === 'field_extractor');
  if (classifier && extractor) link(extractor, classifier, 'sendback', 'loop-right');
  if (hubAgent) {
    // Detection waits for the officer to approve the extracted document; the case analysis then starts on its own.
    if (docs.length) link(docs[docs.length - 1], hubAgent, 'approval', 'x');
    ring.forEach((agent) => link(hubAgent, agent, 'delegate', 'y'));
    if (cases.length) link(hubAgent, cases[0], 'handoff', 'x');
  }
  cases.slice(1).forEach((agent, index) => link(cases[index], agent, 'handoff', 'bulge-right'));

  return { nodes, edges, height };
}

function edgeGeometry(a: GraphNode, b: GraphNode, shape: EdgeShape) {
  let start: Point;
  let end: Point;
  let c1: Point;
  let c2: Point;
  if (shape === 'x') {
    const sign = Math.sign(b.x - a.x) || 1;
    start = { x: a.x + sign * a.hw, y: a.y };
    end = { x: b.x - sign * b.hw, y: b.y };
    const k = (end.x - start.x) * 0.5;
    c1 = { x: start.x + k, y: start.y };
    c2 = { x: end.x - k, y: end.y };
  } else if (shape === 'y') {
    const sign = Math.sign(b.y - a.y) || 1;
    const offset = Math.max(-a.hw * 0.6, Math.min(a.hw * 0.6, (b.x - a.x) * 0.3));
    start = { x: a.x + offset, y: a.y + sign * a.hh };
    end = { x: b.x, y: b.y - sign * b.hh };
    const k = (end.y - start.y) * 0.55;
    c1 = { x: start.x, y: start.y + k };
    c2 = { x: end.x, y: end.y - k };
  } else if (shape === 'loop-right') {
    // On the right, away from the forward link on the left, and above the port the next link leaves from.
    start = { x: a.x + a.hw, y: a.y - 18 };
    end = { x: b.x + b.hw, y: b.y + 18 };
    c1 = { x: start.x + 110, y: start.y };
    c2 = { x: end.x + 110, y: end.y };
  } else {
    const sign = shape === 'bulge-left' ? -1 : 1;
    start = { x: a.x + sign * a.hw, y: a.y };
    end = { x: b.x + sign * b.hw, y: b.y };
    const k = sign * 70;
    c1 = { x: start.x + k, y: start.y };
    c2 = { x: end.x + k, y: end.y };
  }
  const d = `M ${start.x} ${start.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${end.x} ${end.y}`;
  return { d, start, end };
}

const EDGE_TITLES: Record<EdgeKind, string> = {
  handoff: 'Hands work on',
  delegate: 'Assigns checks · reports back',
  approval: 'After officer approval',
  sendback: 'Sends the document back to reclassify when its type looks wrong (a bounded number of times)',
};

export function AgentGlyph({ agent }: { agent: AgentRecord }) {
  const paths: Record<string, ReactNode> = {
    document_classifier: <><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v4h4" /><path d="M10 13h5M10 17h3" /></>,
    field_extractor: <><path d="M5 4h14v16H5z" /><path d="M8 8h4M8 12h8M8 16h6" /></>,
    detection_lead: <><path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5V6z" /><path d="m9 12 2.2 2.2L15.5 10" /></>,
    layout_rendering: <><path d="M4 4h16v16H4z" /><path d="M4 9h16M10 9v11" /></>,
    content_validation: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="m3.5 6 1.2 1.2L6.8 5M3.5 12l1.2 1.2 2.1-2.2M3.5 18l1.2 1.2 2.1-2.2" /></>,
    digital_authenticity: <><path d="M12 4a6 6 0 0 0-6 6c0 3 .7 5.8 2 8" /><path d="M12 8a2 2 0 0 0-2 2c0 3.4.6 6.4 2 10" /><path d="M12 4a6 6 0 0 1 6 6c0 2.5-.3 4.6-1 6.5" /><path d="M14 10c0 2.4-.2 4.6-.8 6.6" /></>,
    hypothesis_planner: <><path d="M9 18h6M10 21h4" /><path d="M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.3 1 2.2h5.2c0-.9.4-1.7 1-2.2A6 6 0 0 0 12 3z" /></>,
    hypothesis_judge: <><path d="M12 4v16M7 20h10M5 7h14" /><path d="m5 7-3 6a3 3 0 0 0 6 0zM19 7l-3 6a3 3 0 0 0 6 0z" /></>,
    // A document with a plus: one more to ask for.
    evidence_requests: <><path d="M13 3H6v18h12V8z" /><path d="M13 3v5h5" /><path d="M12 11v6M9 14h6" /></>,
    // Lines of a short write-up.
    case_summary: <><path d="M4 6h16M4 10h16M4 14h10M4 18h7" /></>,
    // A magnifier over the news: a web search.
    adverse_media: <><circle cx="10.5" cy="10.5" r="6" /><path d="m15 15 5 5" /><path d="M8 9h5M8 12h3.5" /></>,
  };
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="agent-graph-glyph">
      {paths[agent.id] ?? <><circle cx="12" cy="12" r="3" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /></>}
    </svg>
  );
}

function usePrefersReducedMotion(): boolean {
  const query = '(prefers-reduced-motion: reduce)';
  const [reduced, setReduced] = useState(() => typeof window !== 'undefined' && window.matchMedia?.(query).matches);
  useEffect(() => {
    const media = window.matchMedia?.(query);
    if (!media) return undefined;
    const update = () => setReduced(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return reduced;
}

interface View { s: number; x: number; y: number }

interface AgentGraphProps {
  agents: AgentRecord[];
  // The agent whose model card is open, if any; its links stay highlighted behind the dialog.
  selectedId: string | null;
  onSelect: (agentId: string) => void;
}

export default function AgentGraph({ agents, selectedId, onSelect }: AgentGraphProps) {
  const { nodes, edges, height: STAGE_H } = useMemo(() => buildGraph(agents), [agents]);
  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.agent.id, node])), [nodes]);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [view, setView] = useState<View>({ s: 1, x: 0, y: 0 });
  const canvasRef = useRef<HTMLDivElement>(null);
  const reducedMotion = usePrefersReducedMotion();

  const focusId = selectedId ?? hoveredId;
  const neighbours = useMemo(() => {
    if (!focusId) return null;
    const ids = new Set([focusId]);
    edges.forEach((edge) => {
      if (edge.from === focusId) ids.add(edge.to);
      if (edge.to === focusId) ids.add(edge.from);
    });
    return ids;
  }, [edges, focusId]);

  const fit = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { width, height } = canvas.getBoundingClientRect();
    const usable = height - HINT_SPACE;
    const s = Math.max(0.25, Math.min(width / STAGE_W, usable / STAGE_H) * 0.95);
    setView({ s, x: (width - STAGE_W * s) / 2, y: Math.max(8, (usable - STAGE_H * s) / 2) });
  }, [STAGE_H]);

  useLayoutEffect(() => {
    fit();
    const canvas = canvasRef.current;
    if (!canvas || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(() => fit());
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [fit]);

  const hub = nodes.find((node) => node.hub);
  const specialistCount = hub ? edges.filter((edge) => edge.from === hub.agent.id && edge.kind === 'delegate').length : 0;

  return (
    <div
      ref={canvasRef}
      className={`agent-graph${focusId ? ' has-focus' : ''}`}
      style={{
        backgroundPosition: `${view.x}px ${view.y}px`,
        backgroundSize: `${22 * view.s}px ${22 * view.s}px`,
      }}
    >
      <div
        className="agent-graph-stage"
        style={{ width: STAGE_W, height: STAGE_H, transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})` }}
      >
        <svg className="agent-graph-edges" viewBox={`0 0 ${STAGE_W} ${STAGE_H}`} width={STAGE_W} height={STAGE_H} aria-hidden="true">
          <text className="agent-graph-column" x={150} y={36} textAnchor="middle">01 · DOCUMENT PROCESSING</text>
          <text className="agent-graph-column" x={850} y={36} textAnchor="middle">03 · CASE ANALYSIS</text>

          {edges.map((edge) => {
            const a = nodeById.get(edge.from);
            const b = nodeById.get(edge.to);
            if (!a || !b) return null;
            const main = edgeGeometry(a, b, edge.shape);
            const active = !!focusId && (edge.from === focusId || edge.to === focusId);
            const dim = !!focusId && !active;
            const duration = `${edge.kind === 'delegate' ? 3.2 : 2.6}s`;
            return (
              <g key={edge.id} className={`agent-graph-edge is-${edge.kind}${active ? ' is-active' : ''}${dim ? ' is-dim' : ''}`}>
                <title>{`${a.agent.name} → ${b.agent.name}: ${EDGE_TITLES[edge.kind]}`}</title>
                <path className="agent-graph-edge-line" d={main.d} />
                {/* Motion only on the connections of the agent in focus, so the map stays still at rest. */}
                {active && !reducedMotion && edge.kind !== 'sendback' && (
                  <circle className="agent-graph-pulse" r="3.5">
                    <animateMotion dur={duration} repeatCount="indefinite" path={main.d} />
                  </circle>
                )}
                {/* A send-back is the exception: the dot crosses at the usual speed, then waits before the next one. */}
                {active && !reducedMotion && edge.kind === 'sendback' && (
                  <circle className="agent-graph-pulse" r="3.5">
                    <animateMotion dur="8s" repeatCount="indefinite" path={main.d} keyPoints="0;1;1" keyTimes="0;0.33;1" calcMode="linear" />
                    <animate attributeName="opacity" dur="8s" repeatCount="indefinite" values="1;1;0;0" keyTimes="0;0.33;0.34;1" />
                  </circle>
                )}
                {active && !reducedMotion && edge.kind === 'delegate' && (
                  <circle className="agent-graph-pulse is-return" r="3">
                    <animateMotion dur={duration} begin="-1.6s" repeatCount="indefinite" path={main.d} keyPoints="1;0" keyTimes="0;1" calcMode="linear" />
                  </circle>
                )}
                {[a.hub ? main.start : null, b.hub ? main.end : null].map((point, index) => point && (
                  <circle key={index} className="agent-graph-port" cx={point.x} cy={point.y} r="3.5" />
                ))}
              </g>
            );
          })}
        </svg>

        {nodes.map((node) => {
          const { agent } = node;
          const isSelected = agent.id === selectedId;
          const dim = !!neighbours && !neighbours.has(agent.id);
          const gap = agent.prompt_version == null;
          const classes = [
            'agent-graph-node',
            node.hub ? 'is-hub' : `is-label-${node.label}`,
            isSelected ? 'is-selected' : '',
            dim ? 'is-dim' : '',
          ].filter(Boolean).join(' ');
          return (
            <button
              key={agent.id}
              type="button"
              className={classes}
              style={{ left: node.x, top: node.y, ['--i' as string]: node.order }}
              aria-haspopup="dialog"
              aria-label={`${agent.name}, ${agentGroupLabel(agent.group)}${gap ? ', prompt not versioned' : ''}`}
              onClick={() => onSelect(agent.id)}
              onMouseEnter={() => setHoveredId(agent.id)}
              onMouseLeave={() => setHoveredId((current) => (current === agent.id ? null : current))}
              onFocus={() => setHoveredId(agent.id)}
              onBlur={() => setHoveredId((current) => (current === agent.id ? null : current))}
            >
              {node.hub ? (
                <span className="agent-graph-hub">
                  <span className="agent-graph-hub-icon"><AgentGlyph agent={agent} /></span>
                  <strong>{agent.name}</strong>
                  <span className="agent-graph-chip">02 · Lead · {specialistCount} specialists</span>
                  {gap && <span className="agent-graph-badge" title="Prompt not versioned">!</span>}
                </span>
              ) : (
                <>
                  <span className="agent-graph-tile">
                    <AgentGlyph agent={agent} />
                    {gap && <span className="agent-graph-badge" title="Prompt not versioned">!</span>}
                  </span>
                  <span className="agent-graph-label">
                    <strong>{agent.name}</strong>
                    <small>{agent.model}</small>
                  </span>
                </>
              )}
            </button>
          );
        })}
      </div>

      <ul className="agent-graph-legend" aria-label="Connection types">
        <li><span className="is-handoff" />Hands work on</li>
        <li><span className="is-approval" />After officer approval</li>
        <li><span className="is-sendback" />Sent back to reclassify</li>
        <li><span className="is-active" />Selected agent&apos;s links</li>
        {agents.some((agent) => agent.prompt_version == null) && (
          <li><span className="agent-graph-legend-badge">!</span>Prompt not versioned</li>
        )}
      </ul>


      <p className="agent-graph-hint">Select an agent to open its model card.</p>
    </div>
  );
}
