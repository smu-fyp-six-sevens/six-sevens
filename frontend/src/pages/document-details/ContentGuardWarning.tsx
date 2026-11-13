import type { ContentGuardFinding, ContentGuardResponse } from '../../services/uploadService';
import { Icon } from '../../components/Icon';

const WHY_HIDDEN: Record<string, string> = {
  tiny: 'text too small to read',
  white_on_white: 'white text on a white background',
  off_page: 'placed outside the page',
  invisible: 'invisible text',
};

function findingLocation(finding: ContentGuardFinding): string {
  const page = finding.page != null ? ` on page ${finding.page}` : '';
  switch (finding.where) {
    case 'hidden': {
      const why = finding.why_hidden.map((reason) => WHY_HIDDEN[reason] ?? reason).join(', ');
      return `Hidden text${page}${why ? ` (${why})` : ''}`;
    }
    case 'image':
      return `Text inside an image${page}`;
    case 'metadata':
      return `The file's metadata${finding.field ? ` (${finding.field})` : ''}`;
    case 'filename':
      return 'The file name';
    default:
      return `Text in the document${page}`;
  }
}

// Shown at the top of the document page, so an officer sees what the guard found without opening the trace.
export function ContentGuardWarning({ contentGuard }: { contentGuard: ContentGuardResponse | null }) {
  if (!contentGuard) return null;

  if (!contentGuard.checked) {
    return contentGuard.error ? (
      <div className="review-message review-message--warning content-guard-warning" role="status">
        <div>
          <strong>The content guard could not check this document</strong>
          <div>
            It wasn't scanned for hidden text or instructions aimed at the AI ({contentGuard.error}). Check the
            document yourself before approving it.
          </div>
        </div>
      </div>
    ) : null;
  }

  if (contentGuard.findings.length === 0) return null;

  const aimedAtAi = contentGuard.findings.some((finding) => finding.instruction);
  const title = contentGuard.blocked
    ? 'Warning: a prompt injection attempt was detected, so this document was blocked'
    : aimedAtAi
      ? 'Warning: text aimed at the AI was detected in this document'
      : 'Warning: hidden text was detected in this document';
  const detail = contentGuard.blocked
    ? "Processing stopped before any AI model read this document, so it has no type or fields. Retrying scans the same file and will be blocked again. Reject it, ask the applicant for a clean copy, or, if you have read what was found and it is harmless, process it anyway."
    : aimedAtAi
      ? 'Someone may be trying to steer the AI checks on this document. Check it carefully against the original and be aware of this if you continue to approve it.'
      : "This text can't be seen when the document is read normally. It may be harmless, but it can also mean the document was altered. Check it carefully and be aware of this if you continue to approve it.";

  return (
    <div
      className={`review-message ${contentGuard.blocked ? 'review-message--error' : 'review-message--warning'} content-guard-warning`}
      role="alert"
    >
      <span className="content-guard-warning-icon" aria-hidden="true"><Icon name="alert" size={18} /></span>
      <div>
        <strong>{title}</strong>
        <div>{detail}</div>
        <ul className="content-guard-findings">
          {contentGuard.findings.map((finding, index) => (
            <li key={`${finding.where}:${finding.page ?? ''}:${index}`}>
              <span className="content-guard-finding-where">
                {findingLocation(finding)}
                {finding.instruction && <span className="content-guard-finding-tag">Worded as an instruction to the AI</span>}
              </span>
              <blockquote>{finding.quote}</blockquote>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
