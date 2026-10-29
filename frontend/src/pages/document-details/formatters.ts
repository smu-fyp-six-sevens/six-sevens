// Failures in the file itself (unreadable, no text, not downloadable): nothing was read from it, and choosing another
// type re-runs extraction on the same file, so only a new upload can help.
const FILE_PROBLEMS = /^(Text extraction failed:|Extraction produced no usable text$|Failed to download document for extraction:)/;

// A backend failure message (documents.failure_reason) as a short title and a plain sentence for the page, and
// whether the file itself is the problem. The message itself can carry an exception's text, so it only goes to the
// console (DocumentDetailsPage).
export function describeFailureReason(reason: string): { title: string; detail: string; fileProblem: boolean } {
  const fileProblem = FILE_PROBLEMS.test(reason);
  const retry = 'Retry processing; if it fails again, re-upload the file.';
  const patterns: [RegExp, string, string][] = [
    [/^Intake Coordinator invocation failed:/, 'Classification service error', `The document couldn't be classified. ${retry}`],
    [/^Re-classification invocation failed:/, 'Reclassification service error', `The document couldn't be classified again. ${retry}`],
    [/^Extraction invocation failed:/, 'Data extraction service error', `The fields couldn't be extracted. ${retry}`],
    [/^Extraction output could not be used:/, 'Data extraction service error', `The fields couldn't be extracted. ${retry}`],
    [/^Unresolved after \d+ reclassification attempt\(s\):/, 'Document type could not be confirmed', "The document didn't match any type it was classified as."],
    [/^Extraction produced no usable text$/, 'No readable text found in this document', 'Check that the file is not blank or an unreadable scan, then re-upload it.'],
    [/^Extraction could not process this as/, "Document didn't match the expected type", "The fields couldn't be read as the classified document type."],
    [/^Failed to download document for extraction:/, "The file couldn't be opened", `The stored file couldn't be downloaded. ${retry}`],
    [/^Text extraction failed:/, "The file's text couldn't be read", 'The file may be damaged or password-protected. Re-upload a readable copy.'],
    [/^Stuck while /, 'Processing stopped responding', "Processing stalled and couldn't recover on its own. Retry processing."],
    [/^Unhandled (langgraph )?orchestration error:|^Unhandled processing error:/, 'Unexpected system error', `Something went wrong while processing this document. ${retry}`],
  ];

  for (const [pattern, title, detail] of patterns) {
    if (pattern.test(reason)) {
      return { title, detail, fileProblem };
    }
  }

  return { title: 'Processing failed', detail: `Something went wrong while processing this document. ${retry}`, fileProblem };
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
