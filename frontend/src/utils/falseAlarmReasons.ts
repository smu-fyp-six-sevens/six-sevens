import type { FalseAlarmReason } from '../services/uploadService';

// The one-click reasons offered when an officer marks a finding a false alarm, in the order shown.
export const FALSE_ALARM_REASONS: { value: FalseAlarmReason; label: string }[] = [
  { value: 'normal_for_document', label: 'Normal for this document' },
  { value: 'within_tolerance', label: 'Within tolerance' },
  { value: 'tool_misread', label: 'Tool misread it' },
  { value: 'explained_elsewhere', label: 'Explained elsewhere' },
  { value: 'not_relevant', label: 'Not relevant' },
  { value: 'other', label: 'Other' },
];

export function falseAlarmReasonLabel(reason: FalseAlarmReason | null | undefined): string | null {
  return FALSE_ALARM_REASONS.find((entry) => entry.value === reason)?.label ?? null;
}
