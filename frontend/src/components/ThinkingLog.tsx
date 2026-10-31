// Debug aid: the agents' reasoning summaries, from every trace step that recorded some (backend llm_io.thinking_summaries).
// The models think adaptively, so a step with no thinking simply isn't listed.
interface TraceStep {
  step: string;
  [key: string]: unknown;
}

function label(step: TraceStep): string {
  const who = step.specialist ?? step.agent ?? step.call;
  return who ? `${step.step} · ${String(who)}` : step.step;
}

export function ThinkingLog({ steps }: { steps: TraceStep[] }) {
  const thought = steps.filter(
    (step): step is TraceStep & { thinking: string[] } =>
      Array.isArray(step.thinking) && step.thinking.some((text) => typeof text === 'string' && text.trim() !== ''),
  );
  return (
    <details className="debug-section">
      <summary>Agent thinking ({thought.length} {thought.length === 1 ? 'turn' : 'turns'})</summary>
      {thought.length === 0 ? (
        <p>No reasoning recorded: the models didn't think on these turns, or the run predates thinking capture.</p>
      ) : (
        thought.map((step, index) => (
          <details key={`${step.step}-${index}`}>
            <summary>{label(step)}</summary>
            {step.thinking.map((text, part) => (
              <pre key={part} className="debug-json">
                {text}
              </pre>
            ))}
          </details>
        ))
      )}
    </details>
  );
}
