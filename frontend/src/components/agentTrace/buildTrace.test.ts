import { describe, expect, it } from 'vitest';
import { buildTrace, type BuiltTrace, type TraceNode, type TraceStep } from './buildTrace';

const RUN: TraceStep = { step: 'span', id: 'run', name: 'langgraph.process_document', kind: 'run', at: 20, duration_s: 20 };

const group = (trace: BuiltTrace, label: string): TraceNode => {
  const found = trace.roots.flatMap((root) => root.children).find((node) => node.group && node.label === label);
  if (!found) throw new Error(`no ${label} group`);
  return found;
};

describe('buildTrace agent confidence', () => {
  it('puts the classifier’s and the extractor’s confidence on their groups', () => {
    const trace = buildTrace([
      RUN,
      { step: 'classification_finalised', at: 5, in_span: 'run', document_type: 'payslip', confidence: 0.97 },
      { step: 'extraction', at: 9, in_span: 'run', document_type: 'payslip', confidence: 0.91 },
    ]);

    expect(group(trace, 'Classifier').confidence).toBe(0.97);
    expect(group(trace, 'Extractor').confidence).toBe(0.91);
  });

  it('takes the newest decision after a reclassification', () => {
    const trace = buildTrace([
      RUN,
      { step: 'classification_finalised', at: 5, in_span: 'run', confidence: 0.6 },
      { step: 'reclassify', at: 6, in_span: 'run' },
      { step: 'classification_finalised', at: 7, in_span: 'run', confidence: 0.88 },
    ]);

    expect(group(trace, 'Classifier').confidence).toBe(0.88);
  });

  it('leaves it unset when no step reports one', () => {
    const trace = buildTrace([RUN, { step: 'rule_classify', at: 3, in_span: 'run' }]);

    expect(group(trace, 'Classifier').confidence).toBeUndefined();
  });
});

describe('buildTrace sensibility checks', () => {
  const result = (agent: string, check: string, outcome: string, extra: object = {}) => ({
    agent, check, kind: 'output', result: outcome, ...extra,
  });
  const steps: TraceStep[] = [
    RUN,
    { step: 'span', id: 'spec', parent: 'run', name: 'detection.specialist.content_validation', at: 15, duration_s: 4 },
    { step: 'classification_finalised', at: 5, in_span: 'run', confidence: 0.9 },
    { step: 'detection_specialist', at: 16, in_span: 'run', finding_count: 1 },
    {
      step: 'evals', at: 19, in_span: 'run', passed: 3, warnings: 1, failures: 1,
      results: [
        result('classifier', 'type_in_list', 'pass'),
        result('specialist:content_validation', 'cites_tool', 'warn', { span: 'spec' }),
        result('adverse_media', 'sourced', 'fail'),
        result('classifier', 'not_applicable', 'n/a'),
      ],
    },
  ];

  it('lands results on the classifier group, a specialist’s span, and the run', () => {
    const trace = buildTrace(steps);
    const run = trace.roots.find((root) => root.kind === 'run')!;
    const specialist = run.children.flatMap((node) => [node, ...node.children]).find((node) => node.key === 'spec')!;

    expect(group(trace, 'Classifier').evals?.map((r) => r.check)).toEqual(['type_in_list']);
    expect(specialist.evals?.map((r) => r.check)).toEqual(['cites_tool']);
    expect(run.evals?.map((r) => r.check)).toEqual(['sourced']);
  });

  it('leaves n/a results out, and adds the totals for the header', () => {
    const trace = buildTrace(steps);

    expect(JSON.stringify(trace.roots)).not.toContain('not_applicable');
    expect(trace.evals).toEqual({ passed: 3, warnings: 1, failures: 1 });
  });

  it('shows the evals step as no row of its own', () => {
    const trace = buildTrace(steps);
    const names = (nodes: TraceNode[]): string[] => nodes.flatMap((node) => [node.name, ...names(node.children)]);

    expect(names(trace.roots)).not.toContain('evals');
  });
});
