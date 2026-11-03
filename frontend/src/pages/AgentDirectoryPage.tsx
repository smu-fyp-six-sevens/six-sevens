import AppHeader from '../components/AppHeader';

type AgentCard = {
  name: string;
  role: string;
  description: string;
  checks: string[];
};

const processingAgents: AgentCard[] = [
  {
    name: 'Document classifier',
    role: 'Document processing',
    description: 'Identifies what kind of document was uploaded so the right fields and checks can be used.',
    checks: ['Reads document text', 'Can inspect the page image when needed'],
  },
  {
    name: 'Field extractor',
    role: 'Document processing',
    description: 'Turns the document into structured details, such as names, dates, account figures and addresses.',
    checks: ['Uses the schema for the identified document type', 'Marks uncertain or unreadable values for review'],
  },
];

const fraudSpecialists: AgentCard[] = [
  {
    name: 'Layout & Rendering Analyst',
    role: 'Fraud detection specialist',
    description: 'Looks for visual inconsistencies that may need a closer look.',
    checks: ['Fonts, spacing and alignment', 'Overlaid or hidden text', 'Image manipulation'],
  },
  {
    name: 'Content Validation Analyst',
    role: 'Fraud detection specialist',
    description: 'Checks whether the document’s details make sense and can be verified.',
    checks: ['Dates, spelling and page consistency', 'Figures and calculations', 'Relevant public records and reference data'],
  },
  {
    name: 'Digital Authenticity Analyst',
    role: 'Fraud detection specialist',
    description: 'Reviews file-level clues that can help explain how a document was created or changed.',
    checks: ['File metadata and timestamps', 'Possible AI-generated or altered images', 'Instructions embedded in document content'],
  },
];

const caseAgents: AgentCard[] = [
  {
    name: 'Hypothesis planner',
    role: 'Case analysis',
    description: 'Looks across the case documents and proposes specific inconsistencies that can be tested.',
    checks: ['Uses available document facts', 'Plans checks that could support or disprove each idea'],
  },
  {
    name: 'Hypothesis judge',
    role: 'Case analysis',
    description: 'Reviews the check results and decides whether the evidence is strong enough to support a finding.',
    checks: ['Considers evidence and counter-evidence', 'Can leave a hypothesis inconclusive when evidence is insufficient'],
  },
];

function AgentCards({ agents }: { agents: AgentCard[] }) {
  return (
    <div className="agent-directory-grid">
      {agents.map((agent) => (
        <article className="agent-directory-card" key={agent.name}>
          <div className="agent-directory-card-heading">
            <span className="agent-directory-mark" aria-hidden="true">AI</span>
            <div>
              <p className="agent-directory-role">{agent.role}</p>
              <h3>{agent.name}</h3>
            </div>
          </div>
          <p className="agent-directory-description">{agent.description}</p>
          <ul>
            {agent.checks.map((check) => <li key={check}>{check}</li>)}
          </ul>
        </article>
      ))}
    </div>
  );
}

function AgentGroup({ title, intro, agents }: { title: string; intro: string; agents: AgentCard[] }) {
  return (
    <section className="agent-directory-section">
      <div className="agent-directory-section-heading">
        <div>
          <h2>{title}</h2>
          <p>{intro}</p>
        </div>
        <span className="agent-directory-count">{agents.length} {agents.length === 1 ? 'role' : 'roles'}</span>
      </div>
      <AgentCards agents={agents} />
    </section>
  );
}

export default function AgentDirectoryPage() {
  return (
    <main className="agent-directory-page">
      <AppHeader />
      <div className="agent-directory-content">
        <header className="agent-directory-intro">
          <p className="agent-directory-eyebrow">SYSTEM GUIDE</p>
          <h1>Know Your Agents</h1>
          <p className="agent-directory-lede">
            A plain-language guide to the AI roles that help process documents and review loan applications.
          </p>
        </header>

        <section className="agent-directory-flow" aria-labelledby="agent-flow-title">
          <div className="agent-directory-flow-heading">
            <div>
              <h2 id="agent-flow-title">How They Work Together</h2>
              <p>Each stage passes its results to the next, with checks tailored to the document and case.</p>
            </div>
            <span className="agent-directory-count">PROCESS OVERVIEW</span>
          </div>
          <ol>
            <li><span>01</span><div><strong>Identify</strong><p>The system determines the document type.</p></div></li>
            <li><span>02</span><div><strong>Extract</strong><p>Printed details are organised into fields.</p></div></li>
            <li><span>03</span><div><strong>Review</strong><p>A lead coordinates specialist checks for potential issues.</p></div></li>
            <li><span>04</span><div><strong>Compare</strong><p>Case analysis tests relevant facts across documents.</p></div></li>
          </ol>
        </section>

        <AgentGroup
          title="Document Processing"
          intro="These roles help the system understand each upload before fraud checks begin."
          agents={processingAgents}
        />
        <AgentGroup
          title="Detection Coordination"
          intro="The lead routes a document to the relevant specialists and can request another review when a second opinion is useful."
          agents={[{
            name: 'Lead investigator',
            role: 'Detection coordination',
            description: 'Plans the document review, gives specialists focused instructions, and brings their reports together.',
            checks: ['Coordinates the three specialist roles', 'May request a targeted second look', 'Reports checks that could not be completed'],
          }]}
        />
        <AgentGroup
          title="Fraud Detection Specialists"
          intro="Each specialist focuses on a different kind of evidence. Checks that do not apply, or cannot run, are reported separately from clean results."
          agents={fraudSpecialists}
        />
        <AgentGroup
          title="Case Analysis"
          intro="These roles look for testable relationships across documents, then assess what the evidence supports."
          agents={caseAgents}
        />

        <aside className="agent-directory-note">
          <span aria-hidden="true">i</span>
          <p><strong>What this guide means:</strong> Agents assist the loan officer by reviewing information and surfacing evidence. Their output can be incomplete or inconclusive and should be considered alongside the source documents and human review.</p>
        </aside>
      </div>
    </main>
  );
}
