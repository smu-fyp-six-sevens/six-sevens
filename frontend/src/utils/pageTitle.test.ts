import { describe, expect, it } from 'vitest';
import { pageTitle } from './pageTitle';

describe('pageTitle', () => {
  it('names the app on the queue and the root', () => {
    expect(pageTitle('/cases', '')).toBe('GLAD Portal');
    expect(pageTitle('/', '')).toBe('GLAD Portal');
  });

  it('names a page after the app', () => {
    expect(pageTitle('/dashboard', '')).toBe('GLAD Portal | Dashboard');
  });

  it('puts the case id first and the section after it', () => {
    expect(pageTitle('/cases/CASE-1', '')).toBe('CASE-1 | Case Summary');
    expect(pageTitle('/cases/CASE-1', '?section=documents')).toBe('CASE-1 | Documents');
  });

  it('an unknown section falls back to Case Summary', () => {
    expect(pageTitle('/cases/CASE-1', '?section=nonsense')).toBe('CASE-1 | Case Summary');
  });

  it('names a document, and decodes an encoded case id', () => {
    expect(pageTitle('/cases/CASE-1/documents/doc-9', '')).toBe('CASE-1 | Document');
    expect(pageTitle('/cases/CASE%2F7%20A', '')).toBe('CASE/7 A | Case Summary');
  });
});
