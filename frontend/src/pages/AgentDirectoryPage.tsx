import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import AgentGraph, { AgentGlyph } from '../components/AgentGraph';
import { agentGroupLabel } from '../utils/agentGroups';
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
      <section className="agent-tools" aria-labelledby={`agent-tools-${agent.id}`}>
        <h4 id={`agent-tools-${agent.id}`}>
          Tools <span className="agent-tools-count">{agent.tool_details.length}</span>
        </h4>
        {agent.tool_details.length ? (
          <ul className="agent-tool-list">
            {agent.tool_details.map((tool) => (
              <li key={tool.name}>
                <details className="agent-tool">
                  <summary>
                    <svg className="group-chevron" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M9 6l6 6-6 6" />
                    </svg>
                    <code>{tool.name}</code>
                  </summary>
                  <p>{tool.description}</p>
                </details>
              </li>
            ))}
          </ul>
        ) : (
          <p className="agent-tools-empty">No agent tools configured</p>
        )}
      </section>
      <div className="agent-directory-access">
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
            <p className="agent-directory-role">{agentGroupLabel(agent.group)}</p>
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
          <p>Each upload is read and checked first. Once you approve a document, the detection lead assigns focused checks to specialists, who report back. When every approved document is checked, the case analysis compares them. Select an agent to open its model card.</p>
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
          <p className="agent-directory-lede">
            The AI assistants that read, check and summarise each case. They prepare the work; you make every decision.
            Open an agent to see what it does, the tools it uses and its instructions. Every run records which version of
            each agent did the work.
          </p>
        </header>

        {error && <p className="agent-directory-error" role="alert">{error}</p>}

        {!error && <WorkflowDiagram agents={agents} loading={loading} selectedId={openId} onSelect={setOpenId} />}
      </div>
      {openAgent && <AgentDialog key={openAgent.id} agent={openAgent} onClose={() => setOpenId(null)} />}
    </main>
  );
}
