import type { AdditionalDocument } from '../../services/uploadService';
import { DOCUMENT_TYPE_LABELS } from '../../services/uploadService';

// How a document the case analysis requests is named in the Documents tab's Requested group. The requests' titles are
// the model's own sentences ("Recent utility bill for Lim Hui Ling at current residential address"), so the document's
// name is read from them.

// Known kinds first, in order: a certified certificate of incorporation from ACRA is a certificate, not an ACRA letter.
// A kind the app already has a type for is named as the rest of the Documents table names it (DOCUMENT_TYPE_LABELS).
const KNOWN_KINDS: [RegExp, string][] = [
  [/utility bill/i, 'Utility Bill'],
  [/loan statement|repayment schedule/i, 'Loan Statement'],
  [/bank[- ]?(issued |certified )?statement/i, DOCUMENT_TYPE_LABELS.bank_statement],
  [/certificate of incorporation/i, DOCUMENT_TYPE_LABELS.certificate_of_incorporation],
  [/certificate of incumbency/i, DOCUMENT_TYPE_LABELS.certificate_of_incumbency],
  [/fatca|\bcrs\b/i, DOCUMENT_TYPE_LABELS.fatca_crs_declaration],
  [/constitution|memorandum (and|&) articles/i, DOCUMENT_TYPE_LABELS.company_constitution],
  [/notice of assessment/i, DOCUMENT_TYPE_LABELS.notice_of_assessment],
  [/payslip|pay slip/i, DOCUMENT_TYPE_LABELS.payslip],
  [/purchase order/i, 'Purchase Order'],
  [/invoice/i, 'Invoice'],
  [/tenancy|lease agreement/i, 'Tenancy Agreement'],
  [/credit report/i, DOCUMENT_TYPE_LABELS.credit_report],
  [/list of loans/i, DOCUMENT_TYPE_LABELS.list_of_loans],
  [/financial statement|audited accounts/i, 'Financial Statements'],
  [/tax return/i, 'Tax Return'],
  [/source of (personal )?wealth/i, DOCUMENT_TYPE_LABELS.source_of_wealth],
  [/proof of address/i, DOCUMENT_TYPE_LABELS.proof_of_address],
  [/passport/i, DOCUMENT_TYPE_LABELS.passport],
  [/nric|identity card|\bid document/i, 'ID Document'],
  [/\bacra\b|bizfile|business profile/i, 'ACRA'],
];

// Words that describe the copy rather than the kind ("Recent", "Original certified copy of").
const QUALIFIERS = /^((recent|latest|original|certified|corrected|updated|signed|re-signed|official|new|and|a|an|the)\s+|copy of\s+)+/i;
// Where the kind ends and the detail about it begins.
const DETAIL = /\s(for|from|with|at|showing|confirming|covering|to|of the|on)\s|[,(:;–—]\s/i;

// The document's name: a known kind, else the title's opening noun phrase, else the title as it is.
export function requestedDocumentName(title: string): string {
  const known = KNOWN_KINDS.find(([pattern]) => pattern.test(title));
  if (known) return known[1];

  const head = title.split(DETAIL)[0].replace(QUALIFIERS, '').trim();
  return titleCase(head || title);
}

// "shareholder register of members" -> "Shareholder Register of Members"; a word already capitalised (UEN) is kept.
const SMALL_WORDS = new Set(['a', 'an', 'and', 'as', 'at', 'by', 'for', 'from', 'in', 'of', 'on', 'or', 'the', 'to', 'with']);
function titleCase(text: string): string {
  return text
    .split(' ')
    .map((word, index) =>
      index > 0 && SMALL_WORDS.has(word.toLowerCase()) ? word.toLowerCase() : word[0]?.toUpperCase() + word.slice(1),
    )
    .join(' ');
}

// One request's key, the same on both tabs: the Documents tab links to it, the Additional tab highlights it.
export function requestKey(item: Pick<AdditionalDocument, 'source' | 'title'>): string {
  return `${item.source}-${item.title}`;
}
