// One colour per case status label (see mapStatus in App.tsx); anything else falls back to grey.
const STATUS_TONE: Record<string, string> = {
  'Ready for review': 'green',
  'In progress': 'blue',
  'Documents failed': 'red',
  'Processing stuck': 'amber',
  'Documents pending': 'grey',
};

export function caseStatusTone(status: string): string {
  return STATUS_TONE[status] ?? 'grey';
}
