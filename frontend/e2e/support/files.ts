import type { Page } from '@playwright/test';

/**
 * Small in-memory test files. The frontend decides a file's type from its
 * first bytes (utils/fileValidation.ts's magic-number sniffing), not its
 * extension, so each helper writes the real signature for its format. The
 * `label` is baked into the content so two files never share a hash — the
 * upload page rejects an exact duplicate of a file it has already seen.
 */

export interface TestFile {
  name: string;
  mimeType: string;
  buffer: Buffer;
}

export function pdf(name: string, label = name): TestFile {
  return { name, mimeType: 'application/pdf', buffer: Buffer.from(`%PDF-1.4\n% ${label}\n%%EOF\n`) };
}

export function png(name: string, label = name): TestFile {
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  return { name, mimeType: 'image/png', buffer: Buffer.concat([signature, Buffer.from(label)]) };
}

export function jpg(name: string, label = name): TestFile {
  const signature = Buffer.from([0xff, 0xd8, 0xff, 0xe0]);
  return { name, mimeType: 'image/jpeg', buffer: Buffer.concat([signature, Buffer.from(label)]) };
}

export function text(name: string, content = 'plain text, not a document'): TestFile {
  return { name, mimeType: 'text/plain', buffer: Buffer.from(content) };
}

/**
 * Adds, through Browse, a PDF one byte over the 25MB limit (MAX_FILE_SIZE_BYTES). Built in the page: sending
 * 25MB from the test runner with setInputFiles took about 10 seconds.
 */
export async function browseOversizedPdf(page: Page, name: string): Promise<void> {
  await page.locator('.dropzone input[type="file"]').evaluate((input: HTMLInputElement, fileName) => {
    const header = '%PDF-1.4\n';
    const padding = new Uint8Array(25 * 1024 * 1024 + 1 - header.length);
    const transfer = new DataTransfer();
    transfer.items.add(new File([header, padding], fileName, { type: 'application/pdf' }));
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }, name);
}

/** Adds files through the drop zone's Browse (file input). */
export async function browseFiles(page: Page, files: TestFile[]): Promise<void> {
  await page.locator('.dropzone input[type="file"]').setInputFiles(files);
}

/**
 * Adds files by dispatching a real `drop` event on the drop zone, the same
 * event a user's drag-and-drop produces.
 */
export async function dropFiles(page: Page, files: TestFile[]): Promise<void> {
  const payload = files.map((f) => ({ name: f.name, mimeType: f.mimeType, bytes: [...f.buffer] }));
  const dataTransfer = await page.evaluateHandle((items) => {
    const transfer = new DataTransfer();
    for (const item of items) {
      transfer.items.add(new File([new Uint8Array(item.bytes)], item.name, { type: item.mimeType }));
    }
    return transfer;
  }, payload);
  await page.getByRole('button', { name: 'Upload documents' }).dispatchEvent('drop', { dataTransfer });
}
