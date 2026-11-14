import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import AgentGraph, { AgentGlyph } from '../components/AgentGraph';
import AppHeader from '../components/AppHeader';
import { getAgentPrompt, getAgentRegistry, type AgentPrompt, type AgentRecord } from '../services/agents';

function documentTypeLabel(value: string): string {
  return value.split('_').map((word) => {
    if (['fatca', 'crs', 'nric'].includes(word.toLowerCase())) return word.toUpperCase();
    return word.charAt(0).toUpperCase() + word.slice(1);
  }).join(' ');
}

function AgentModelCard({ agent }: { agent: AgentRecord }) {
  const [showPrompt, setShowPrompt] = useState(false);
  const [prompt, setPrompt] = useState<AgentPrompt | null>(null);
  const [promptLoading, setPromptLoading] = useState(false);
  const [promptError, setPromptError] = useState<string | null>(null);
  const [documentType, setDocumentType] = useState(agent.prompt_options[0] ?? '');

  async function loadPrompt(selectedDocumentType = documentType) {
    setPromptLoading(true);
    setPromptError(null);
    try {
      setPrompt(await getAgentPrompt(agent.id, selectedDocumentType || undefined));
    } catch (reason) {
      setPromptError(reason instanceof Error ? reason.message : 'Could not load this prompt.');
    } finally {
      setPromptLoading(false);
    }
  }

  function togglePrompt() {
    if (showPrompt) {
      setShowPrompt(false);
      return;
    }
    setShowPrompt(true);
    if (!prompt) void loadPrompt();
  }

  return (
    <div className="agent-directory-card">
      <p className="agent-directory-description">{agent.purpose}</p>
      <p className="agent-directory-model-card-label">MODEL CARD</p>
      <dl className="agent-directory-fields">
        <div><dt>Model</dt><dd>{agent.model}</dd></div>
        <div><dt>Prompt version</dt><dd>{agent.prompt_version ?? 'Not versioned'}</dd></div>
        <div>
          <dt>Fingerprint</dt>
          <dd title="Changes whenever this agent's prompts, model, tools or call budget change; every run records the one it used">
            <code>{agent.fingerprint ?? 'Not recorded'}</code>
          </dd>
        </div>
        <div><dt>Call budget</dt><dd>{agent.call_budget}</dd></div>
        <div><dt>Owner</dt><dd>{agent.owner ?? 'Not assigned'}</dd></div>
      </dl>
      <div className="agent-directory-access">
        <div><h4>Allowed tools</h4><p>{agent.tools.length ? agent.tools.join(', ') : 'No agent tools configured'}</p></div>
        <div><h4>Current access</h4><ul>{agent.access_summary.map((permission) => <li key={permission}>{permission}</li>)}</ul></div>
        <div><h4>Limitations</h4><p>{agent.limitations}</p></div>
      </div>
      <div className="agent-directory-prompt-control">
        <button type="button" onClick={togglePrompt} aria-expanded={showPrompt}>
          {showPrompt ? 'Hide prompt' : 'View prompt'}
        </button>
        {showPrompt && (
          <div className="agent-directory-prompt-content">
            {agent.prompt_options.length > 0 && (
              <label>
                Document type
                <select
                  value={documentType}
                  onChange={(event) => {
                    const selected = event.target.value;
                    setDocumentType(selected);
                    void loadPrompt(selected);
                  }}
                >
                  {agent.prompt_options.map((option) => (
                    <option key={option} value={option}>
                      {documentTypeLabel(option)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {promptLoading && <p>Loading prompt…</p>}
            {promptError && <p role="alert">{promptError}</p>}
            {prompt && !promptLoading && (
              <>
                <p className="agent-directory-prompt-note">{prompt.note}</p>
                {prompt.sections.map((section) => (
                  <section key={section.title}>
                    <h4>{section.title}</h4>
                    <pre>{section.text}</pre>
                  </section>
                ))}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function AgentDialog({ agent, onClose }: { agent: AgentRecord; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const returnFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    closeRef.current?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      returnFocus?.focus();
    };
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return createPortal(
    <div className="agent-dialog-backdrop" onClick={onClose}>
      <div
        className="agent-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-dialog-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="agent-dialog-header">
          <span className="agent-dialog-icon" aria-hidden="true"><AgentGlyph agent={agent} /></span>
          <div>
            <p className="agent-directory-role">{agent.group}</p>
            <h2 id="agent-dialog-title">{agent.name}</h2>
          </div>
          <button ref={closeRef} type="button" className="agent-dialog-close" onClick={onClose} aria-label="Close model card">×</button>
        </header>
        <div className="agent-dialog-body">
          <AgentModelCard agent={agent} />
        </div>
      </div>
    </div>,
    document.body,
  );
}

function WorkflowDiagram({ agents, loading, selectedId, onSelect }: {
  agents: AgentRecord[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (agentId: string) => void;
}) {
  return (
    <section className="agent-directory-workflow" aria-labelledby="agent-workflow-title">
      <div className="agent-directory-section-heading">
        <div>
          <h2 id="agent-workflow-title">Agent Registry</h2>
          <p>Document checks happen per upload. The detection lead assigns focused checks to specialists, who report back. Case analysis compares approved documents after they are available. Select an agent to open its model card.</p>
          <p>Models and pipeline call limits are read from the current backend configuration. A shared pipeline cap is not an individual agent budget.</p>
        </div>
        <span className="agent-directory-count">{loading ? 'Loading' : `${agents.length} roles`}</span>
      </div>
      {loading
        ? <p className="agent-directory-loading">Loading configured agents…</p>
        : <AgentGraph agents={agents} selectedId={selectedId} onSelect={onSelect} />}
    </section>
  );
}

export default function AgentDirectoryPage() {
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<string | null>(null);
  const openAgent = agents.find((agent) => agent.id === openId) ?? null;

  useEffect(() => {
    let active = true;
    getAgentRegistry()
      .then((records) => { if (active) setAgents(records); })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Could not load the agent registry.');
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  return (
    <main className="agent-directory-page">
      <AppHeader />
      <div className="agent-directory-content">
        <header className="agent-directory-intro">
          <p className="agent-directory-eyebrow">SYSTEM OVERSIGHT</p>
          <h1>Know Your Agents</h1>
          <p className="agent-directory-lede">A plain-language registry of the AI roles used to process documents and review loan applications.</p>
        </header>

        {error && <p className="agent-directory-error" role="alert">{error}</p>}

        {!error && <WorkflowDiagram agents={agents} loading={loading} selectedId={openId} onSelect={setOpenId} />}

        <section className="agent-directory-oversight-grid" aria-label="Governance coverage">
          <article className="agent-directory-oversight-card">
            <p className="agent-directory-eyebrow">AUDIT TRAIL</p>
            <h2>Run events are recorded; per-agent traces are incomplete</h2>
            <p>Current audit events identify the worker or pipeline that completed a run. Case-analysis records also include the prompt version, but most individual model/tool actions are not yet linked to an agent identity and version in the shared audit trail.</p>
            <span className="agent-directory-tag agent-directory-tag-partial">Partial coverage</span>
          </article>
          <article className="agent-directory-oversight-card">
            <p className="agent-directory-eyebrow">PROMPT TRANSPARENCY</p>
            <h2>View the source prompt for each role</h2>
            <p>Select an agent on the map and choose “View prompt” to inspect its instructions. Runtime document data is replaced by clear placeholders, and the extractor prompt can be previewed by document type.</p>
            <span className="agent-directory-tag agent-directory-tag-partial">Templates shown</span>
          </article>
        </section>

        <aside className="agent-directory-note">
          <span aria-hidden="true">i</span>
          <p><strong>How to read this page:</strong> “Not versioned” and “Not assigned” mark gaps in the current implementation. Tool access is described from code; it is not a separate per-agent permission policy. Pipeline caps are shared ceilings, not per-agent or dollar-cost budgets. Agent outputs support human review; they do not make the lending decision.</p>
        </aside>
      </div>
      {openAgent && <AgentDialog key={openAgent.id} agent={openAgent} onClose={() => setOpenId(null)} />}
    </main>
  );
}
