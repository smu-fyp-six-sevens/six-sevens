// Extracted field keys are snake_case; these short keys are shown upper-case.
export const FIELD_KEY_ACRONYMS = new Set(['id', 'uen', 'tin', 'nric']);

export function humanizeFieldKey(key: string): string {
  return key
    .split('_')
    .filter(Boolean)
    .map((word) =>
      FIELD_KEY_ACRONYMS.has(word.toLowerCase())
        ? word.toUpperCase()
        : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(' ');
}
