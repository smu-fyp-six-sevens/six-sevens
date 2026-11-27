import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DetectionFinding, DetectionReport } from '../../services/uploadService';
import { DetectionPanel } from './DetectionPanel';

function finding(overrides: Partial<DetectionFinding> = {}): DetectionFinding {
  return {
    id: 'f1', tool: 'metadata_tool', summary: 'Edited after creation', severity: 'medium', rationale: 'Dates differ.',
    specialists: ['digital_authenticity'], via_second_look: false, ...overrides,
  };
}

function panel(findings: DetectionFinding[]) {
  const report = {
    verdict: 'flagged', summary: 'Flagged.', findings, consulted: [], unable_to_assess: [], nothing_to_check: [], second_look: null, last_run_error: null,
  } as unknown as DetectionReport;
  render(
    <DetectionPanel
      status="detected" detectionError={null} detectionReport={report} detectionSkipped={false} isApproved
      isRetryingDetection={false} isCancellingDetection={false} cancelError={null} isRefreshingDetection={false}
      refreshDetectionReport={vi.fn()} handleRetryDetection={vi.fn()} handleCancelDetection={vi.fn()}
      runningCheckId={null} individualCheckError={null} individualCheckResults={{}} handleRunIndividualCheck={vi.fn()}
    />,
  );
}

describe('DetectionPanel severity floor', () => {
  it('says a rule raised the finding, with the reason as its title', () => {
    panel([finding({ agent_severity: 'low', severity_floor_reason: 'Metadata edits are at least medium.' })]);

    const note = screen.getByText(/raised from low by rule/);
    expect(note.getAttribute('title')).toBe('Metadata edits are at least medium.');
  });

  it('says a rule kept a finding the specialist dismissed', () => {
    panel([finding({ agent_severity: 'dismissed', severity_floor_reason: 'Always kept.' })]);

    expect(screen.getByText(/kept by rule/).getAttribute('title')).toBe('Always kept.');
  });

  it('says neither when the specialist’s rating stood', () => {
    panel([finding()]);

    expect(screen.queryByText(/by rule/)).toBeNull();
  });
});
