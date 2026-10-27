import type { Page } from '@playwright/test';
import { test, expect } from '../support/test';
import { makeCase, type MockBackend } from '../support/mockBackend';
import { browseFiles, dropFiles, jpg, oversizedPdf, pdf, png, text } from '../support/files';

/**
 * Upload Documents (SCRUM-6, Sprint 1) — test cases DI-001 to DI-016 from
 * "Test Cases - Sprint 1.xlsx", plus the unsupported-format case from
 * Generate Additional File Formats (SCRUM-115, Sprint 2), DT-031, which also
 * covers Sprint 1's DI-008 (a .txt file rejected).
 */

async function openCase(page: Page, backend: MockBackend) {
  const theCase = makeCase({ id: 'case-upload', greenlink_case_id: 'GLDB-1001' });
  backend.cases.push(theCase);
  await page.goto(`/cases/${theCase.greenlink_case_id}?section=documents`);
  await expect(page.getByRole('heading', { name: 'Upload supporting documents' })).toBeVisible();
  return theCase;
}

const fileRow = (page: Page, filename: string) =>
  page.locator('.file-row').filter({ has: page.locator('.f-name', { hasText: filename }) });

const uploadButton = (page: Page) => page.locator('.upload-btn');

test.describe('Upload documents @sprint1 @SCRUM-6', () => {
  test('DI-001 upload page displays all required upload components @DI-001', async ({ page, backend }) => {
    await openCase(page, backend);

    const dropZone = page.getByRole('button', { name: 'Upload documents' });
    await expect(dropZone).toBeVisible();
    await expect(dropZone).toContainText('Drag files here or browse');
    await expect(dropZone).toContainText('PDF · PNG · JPG — up to 25MB each');
    await expect(page.getByText('No files selected yet')).toBeVisible();

    await browseFiles(page, [pdf('payslip.pdf')]);
    await expect(page.locator('.section-label', { hasText: 'Selected files' })).toBeVisible();
    await expect(uploadButton(page)).toHaveText('Upload 1 file');
  });

  test('DI-002 – DI-006 files added by drag-and-drop and Browse, singly or together, never overwrite each other @DI-002 @DI-003 @DI-004 @DI-005 @DI-006', async ({ page, backend }) => {
    await openCase(page, backend);

    await dropFiles(page, [pdf('payslip.pdf')]);
    await expect(page.locator('.file-row')).toHaveCount(1);
    await dropFiles(page, [pdf('passport.pdf')]);
    await expect(page.locator('.file-row')).toHaveCount(2);
    await dropFiles(page, [pdf('nric-front.pdf'), pdf('nric-back.pdf')]);
    await expect(page.locator('.file-row')).toHaveCount(4);
    await browseFiles(page, [pdf('noa.pdf')]);

    await expect(page.locator('.file-row')).toHaveCount(5);
    for (const name of ['payslip.pdf', 'passport.pdf', 'nric-front.pdf', 'nric-back.pdf', 'noa.pdf']) {
      await expect(fileRow(page, name)).toBeVisible();
    }
  });

  test('DI-007 / DI-011 / DI-015 a PDF uploads without a format error, shows its completed status and the completion message @DI-007 @DI-011 @DI-015', async ({ page, backend }) => {
    await openCase(page, backend);

    await browseFiles(page, [pdf('payslip.pdf')]);
    const row = fileRow(page, 'payslip.pdf');
    await expect(row.locator('.badge')).toHaveText('Ready');
    await uploadButton(page).click();

    await expect(row.locator('.badge')).toHaveText('Uploaded');
    await expect(row).toContainText(/Document ID: doc-/);
    await expect(row).not.toContainText('does not match a supported type');
    await expect(page.locator('.toast.all-ok')).toContainText(
      '1 of 1 documents uploaded. All documents uploaded successfully.',
    );
  });

  test('DI-009 a file over 25MB is rejected with a file-size error @DI-009', async ({ page, backend }) => {
    await openCase(page, backend);

    await browseFiles(page, [oversizedPdf('large-statement.pdf')]);
    await uploadButton(page).click();

    const row = fileRow(page, 'large-statement.pdf');
    await expect(row.locator('.badge')).toHaveText('Rejected');
    await expect(row).toContainText('File exceeds the 25 MB limit.');
    expect(backend.requestsTo('POST', '/documents/upload-batch')).toHaveLength(0);
  });

  test('DI-010 a file shows an uploading state while the upload is in progress @DI-010', async ({ page, backend }) => {
    await openCase(page, backend);
    let release!: () => void;
    backend.uploadGate = new Promise<void>((resolve) => (release = resolve));

    await browseFiles(page, [pdf('payslip.pdf')]);
    await uploadButton(page).click();

    const row = fileRow(page, 'payslip.pdf');
    await expect(row.locator('.badge')).toHaveText('Uploading');
    await expect(row.getByLabel('Upload in progress')).toBeVisible();
    await expect(uploadButton(page)).toHaveText('Uploading…');

    release();
    await expect(row.locator('.badge')).toHaveText('Uploaded');
  });

  test('DI-012 a failed upload shows a FAILED status and an error @DI-012', async ({ page, backend }) => {
    await openCase(page, backend);
    backend.uploadOutcomes['payslip.pdf'] = { success: false, error: 'Storage upload failed' };

    await browseFiles(page, [pdf('payslip.pdf')]);
    await uploadButton(page).click();

    const row = fileRow(page, 'payslip.pdf');
    await expect(row.locator('.badge')).toHaveText('Failed');
    await expect(row).toContainText('Storage upload failed');
  });

  test('DI-013 each file keeps its own upload status @DI-013', async ({ page, backend }) => {
    await openCase(page, backend);
    backend.uploadOutcomes['passport.pdf'] = { success: false, error: 'Storage upload failed' };

    await browseFiles(page, [pdf('payslip.pdf'), pdf('passport.pdf')]);
    await uploadButton(page).click();

    await expect(fileRow(page, 'payslip.pdf').locator('.badge')).toHaveText('Uploaded');
    await expect(fileRow(page, 'passport.pdf').locator('.badge')).toHaveText('Failed');
  });

  test('DI-014 a failed file can be uploaded again @DI-014', async ({ page, backend }) => {
    await openCase(page, backend);
    backend.uploadOutcomes['payslip.pdf'] = { success: false, error: 'Storage upload failed' };

    await browseFiles(page, [pdf('payslip.pdf')]);
    await uploadButton(page).click();
    await expect(fileRow(page, 'payslip.pdf').locator('.badge')).toHaveText('Failed');
    await expect(uploadButton(page)).toHaveText('Upload again');

    delete backend.uploadOutcomes['payslip.pdf'];
    await uploadButton(page).click();

    await expect(fileRow(page, 'payslip.pdf').locator('.badge')).toHaveText('Uploaded');
    expect(backend.requestsTo('POST', '/documents/upload-batch')).toHaveLength(2);
  });

  test('DI-016 complete document upload workflow @DI-016', async ({ page, backend }) => {
    const theCase = await openCase(page, backend);

    await dropFiles(page, [pdf('payslip.pdf')]);
    await browseFiles(page, [pdf('passport.pdf')]);
    await expect(page.locator('.file-row')).toHaveCount(2);

    await uploadButton(page).click();

    await expect(fileRow(page, 'payslip.pdf').locator('.badge')).toHaveText('Uploaded');
    await expect(fileRow(page, 'passport.pdf').locator('.badge')).toHaveText('Uploaded');
    await expect(page.locator('.toast.all-ok')).toContainText('2 of 2 documents uploaded.');

    // The uploaded documents join the case's document list via the live
    // case snapshot, and finish processing there.
    for (const document of backend.documents) {
      document.status = 'analyzed';
      document.routing_status = 'accepted';
      document.classification_confidence = 0.95;
    }
    backend.push(theCase.id);
    const table = page.locator('.documents-table');
    await expect(table.getByText('payslip.pdf')).toBeVisible();
    await expect(table.getByText('passport.pdf')).toBeVisible();
  });

  test('DI-others-01 an empty (0 byte) file is rejected @DI-others', async ({ page, backend }) => {
    await openCase(page, backend);

    await browseFiles(page, [{ name: 'empty.pdf', mimeType: 'application/pdf', buffer: Buffer.alloc(0) }]);
    await uploadButton(page).click();

    const row = fileRow(page, 'empty.pdf');
    await expect(row.locator('.badge')).toHaveText('Rejected');
    await expect(row).toContainText('File is empty (0 bytes).');
    expect(backend.requestsTo('POST', '/documents/upload-batch')).toHaveLength(0);
  });

  test('DI-others-02 a filename that cleans up to nothing is rejected @DI-others', async ({ page, backend }) => {
    await openCase(page, backend);

    await browseFiles(page, [pdf('...')]);
    await uploadButton(page).click();

    const row = fileRow(page, '...');
    await expect(row.locator('.badge')).toHaveText('Rejected');
    await expect(row).toContainText('Filename is invalid or unsafe.');
    expect(backend.requestsTo('POST', '/documents/upload-batch')).toHaveLength(0);
  });

  test('DI-others-03 a file already uploaded to this case is rejected as a duplicate @DI-others', async ({ page, backend }) => {
    await openCase(page, backend);

    await browseFiles(page, [pdf('payslip.pdf')]);
    await uploadButton(page).click();
    await expect(fileRow(page, 'payslip.pdf').locator('.badge')).toHaveText('Uploaded');

    await browseFiles(page, [pdf('payslip.pdf')]);
    await uploadButton(page).click();

    await expect(page.getByText('This exact file has already been uploaded for this case.').first()).toBeVisible();
    expect(backend.requestsTo('POST', '/documents/upload-batch')).toHaveLength(1);
  });

  test('DI-others-03b a file can be uploaded again once its document is deleted from the case @DI-others', async ({ page, backend }) => {
    const theCase = await openCase(page, backend);

    await browseFiles(page, [pdf('payslip.pdf')]);
    await uploadButton(page).click();
    await expect(fileRow(page, 'payslip.pdf').locator('.badge')).toHaveText('Uploaded');

    const [uploaded] = backend.documents;
    uploaded.status = 'analyzed';
    await page.goto(`/cases/${theCase.greenlink_case_id}/documents/${uploaded.id}`);
    await page.getByRole('button', { name: 'Document actions' }).click();
    await page.getByRole('menuitem', { name: 'Delete document' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
    await expect(page).toHaveURL(`/cases/${theCase.greenlink_case_id}?section=documents`);

    await browseFiles(page, [pdf('payslip.pdf')]);
    await uploadButton(page).click();

    await expect(fileRow(page, 'payslip.pdf').locator('.badge')).toHaveText('Uploaded');
    expect(backend.requestsTo('POST', '/documents/upload-batch')).toHaveLength(2);
  });

  test('DI-others-03c a file can be uploaded again once its document is deleted from another browser @DI-others', async ({ page, backend }) => {
    const theCase = await openCase(page, backend);

    await browseFiles(page, [pdf('payslip.pdf')]);
    await uploadButton(page).click();
    await expect(fileRow(page, 'payslip.pdf').locator('.badge')).toHaveText('Uploaded');
    const table = page.locator('.documents-table');
    await expect(table.getByText('payslip.pdf')).toBeVisible();

    // Deleted elsewhere: this page only hears about it through the next snapshot.
    backend.documents = [];
    backend.push(theCase.id);
    await expect(table.getByText('payslip.pdf')).toHaveCount(0);

    await browseFiles(page, [pdf('payslip.pdf')]);
    await uploadButton(page).click();

    await expect(table.getByText('payslip.pdf')).toBeVisible();
    expect(backend.requestsTo('POST', '/documents/upload-batch')).toHaveLength(2);
  });

  test('DI-others-04 more than 20 files at once is refused and only the first 20 are kept @DI-others', async ({ page, backend }) => {
    await openCase(page, backend);

    const twentyOne = Array.from({ length: 21 }, (_, index) => pdf(`statement-${index + 1}.pdf`));
    await browseFiles(page, twentyOne);

    await expect(page.locator('.toast.mixed')).toContainText('Too many files selected (21). Maximum is 20 per upload.');
    await expect(page.locator('.file-row')).toHaveCount(20);
  });
});

test.describe('Generate additional file formats @sprint2 @SCRUM-115', () => {
  test('DT-031 / DI-008 DOCX, XLSX and TXT files are rejected with a clear error, and nothing is uploaded @DT-031 @DI-008 @sprint1 @SCRUM-6', async ({ page, backend }) => {
    await openCase(page, backend);

    await browseFiles(page, [
      { name: 'report.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer: Buffer.from('PK\u0003\u0004 docx') },
      { name: 'figures.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('PK\u0003\u0004 xlsx') },
      text('notes.txt'),
    ]);
    await uploadButton(page).click();

    for (const name of ['report.docx', 'figures.xlsx', 'notes.txt']) {
      const row = fileRow(page, name);
      await expect(row.locator('.badge')).toHaveText('Rejected');
      await expect(row).toContainText('File content does not match a supported type.');
    }
    expect(backend.requestsTo('POST', '/documents/upload-batch')).toHaveLength(0);
  });

  test('DT-029 PDF, PNG, JPG and JPEG files are accepted for upload @DT-029', async ({ page, backend }) => {
    await openCase(page, backend);

    await browseFiles(page, [
      pdf('C014_CertIncorp.pdf'),
      png('NRIC_6554_back.png'),
      jpg('clear_front.jpg'),
      jpg('IMG_5099_front.jpeg'),
    ]);
    await uploadButton(page).click();

    for (const name of ['C014_CertIncorp.pdf', 'NRIC_6554_back.png', 'clear_front.jpg', 'IMG_5099_front.jpeg']) {
      await expect(fileRow(page, name).locator('.badge')).toHaveText('Uploaded');
    }
  });
});
