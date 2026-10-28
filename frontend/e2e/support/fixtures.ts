import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import type { CaseRow, ChecklistVariant, DocumentRow, MockBackend } from './mockBackend';

/**
 * Real cases captured from Supabase by backend/scripts/capture_regression_fixture.py,
 * exactly as the API returned them. Shared with the backend regression tests,
 * so both suites exercise the same real-shaped data.
 *
 *   anchorpoint   GLDB-02 — a complete case: every required and optional
 *                 document, two identities with NRIC front + back.
 *   harbor-light  GLDB-16 — the C014 set: partial case (7 requirements still
 *                 missing), array fields, a null field, a verified incumbency.
 */
export type FixtureName = 'anchorpoint' | 'harbor-light';

interface Fixture {
  case: CaseRow;
  snapshot: { case_match: unknown };
  documents: Record<string, DocumentRow>;
}

const FIXTURE_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../../backend/tests/fixtures/regression',
);

/** Any JSON file in backend/tests/fixtures/regression, as the backend suite wrote it. */
export function readRegressionFixture<T>(filename: string): T {
  return JSON.parse(readFileSync(path.join(FIXTURE_DIR, filename), 'utf8')) as T;
}

export function readFixture(name: FixtureName): Fixture {
  return readRegressionFixture<Fixture>(`${name}.json`);
}

/** Loads a captured case into the mock backend; returns it for the test to use. */
export function loadFixture(backend: MockBackend, name: FixtureName) {
  const fixture = readFixture(name);
  const theCase = structuredClone(fixture.case);
  const documents = Object.values(structuredClone(fixture.documents));
  backend.cases.push(theCase);
  backend.documents.push(...documents);
  backend.caseMatch[theCase.id] = structuredClone(fixture.snapshot.case_match);
  backend.checklists.push(
    ...readRegressionFixture<ChecklistVariant[]>('checklist-variants.json').filter((c) => c.case_id === theCase.id),
  );

  return {
    case: theCase,
    documents,
    document(filenamePart: string): DocumentRow {
      const found = documents.find((d) => d.filename.includes(filenamePart));
      if (!found) throw new Error(`No document matching "${filenamePart}" in fixture ${name}`);
      return found;
    },
    caseUrl: `/cases/${theCase.greenlink_case_id}`,
    documentUrl(document: DocumentRow) {
      return `/cases/${theCase.greenlink_case_id}/documents/${document.id}`;
    },
  };
}
