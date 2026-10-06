// Maps known backend failure messages to a short title; raw text kept as detail.
export function describeFailureReason(reason: string): { title: string; detail: string } {
  const patterns: [RegExp, string][] = [
    [/^Intake Coordinator invocation failed:/, 'Classification service error'],
    [/^Re-classification invocation failed:/, 'Reclassification service error'],
    [/^Extraction invocation failed:/, 'Data extraction service error'],
    [/^Unresolved after \d+ reclassification attempt\(s\):/, 'Document type could not be confirmed'],
    [/^Extraction produced no usable text$/, 'No readable text found in this document'],
    [/^Extraction could not process this as/, "Document didn't match the expected type"],
    [/^Unhandled orchestration error:/, 'Unexpected system error'],
  ];

  for (const [pattern, title] of patterns) {
    if (pattern.test(reason)) {
      return { title, detail: reason };
    }
  }

  return { title: 'Processing failed', detail: reason };
}

export function formatDate(value: string): string {
  return new Date(value).toLocaleString('en-SG', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}
