import type { ReviewReason } from '../services/uploadService';

// The one-click reasons offered when an officer says the agents got a finding wrong, in the order shown. On a finding the
// agents kept, it is a false alarm: why. On one the specialists set aside, it is real after all: why they were wrong.
export const FALSE_ALARM_REASONS: { value: ReviewReason; label: string }[] = [
  { value: 'normal_for_document', label: 'Normal for this document' },
  { value: 'within_tolerance', label: 'Within tolerance' },
  { value: 'tool_misread', label: 'Tool misread it' },
  { value: 'explained_elsewhere', label: 'Explained elsewhere' },
  { value: 'not_relevant', label: 'Not relevant' },
  { value: 'other', label: 'Other' },
];

export const SET_ASIDE_REASONS: { value: ReviewReason; label: string }[] = [
  { value: 'explanation_not_valid', label: "The explanation doesn't hold" },
  { value: 'part_of_a_pattern', label: 'Part of a wider pattern' },
  { value: 'other', label: 'Other' },
];

export function reviewReasonLabel(reason: ReviewReason | string | null | undefined): string | null {
  return [...FALSE_ALARM_REASONS, ...SET_ASIDE_REASONS].find((entry) => entry.value === reason)?.label ?? null;
}
