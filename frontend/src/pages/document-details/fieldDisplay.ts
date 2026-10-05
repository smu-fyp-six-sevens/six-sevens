export interface ExtractedField {
  id?: string;
  value: unknown;
  confidence: number | null;
}

export function displayableFieldEntries(
  fields: Record<string, unknown> | null,
): [string, ExtractedField][] {
  if (!fields) {
    return [];
  }

  return Object.entries(fields)
    .map(([key, rawField]) => {
      if (
        rawField &&
        typeof rawField === 'object' &&
        'value' in rawField
      ) {
        const field = rawField as {
          id?: unknown;
          value?: unknown;
          confidence?: unknown;
        };

        return [
          key,
          {
            id: typeof field.id === 'string' ? field.id : undefined,
            value: field.value ?? null,
            confidence:
              typeof field.confidence === 'number'
                ? field.confidence
                : null,
          },
        ];
      }

      // Fallback for older/simple extracted-field data.
      return [
        key,
        {
          value: rawField,
          confidence: null,
        },
      ];
    });
}

export function valueToString(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }

  if (typeof value === 'object') {
    return JSON.stringify(value);
  }

  return String(value);
}

export function buildFieldDraft(
  fields: Record<string, unknown> | null,
): Record<string, string> {
  const draft: Record<string, string> = {};

  for (const [key, field] of displayableFieldEntries(fields)) {
    draft[key] = valueToString(field.value);
  }

  return draft;
}

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

// Array of objects: rendered as a table in a modal.
export function isArrayOfObjects(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.length > 0 && value.every(isPlainObject);
}

// Flat array of primitives: shown as a comma-separated list.
export function isArrayOfPrimitives(value: unknown): value is (string | number | boolean)[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean')
  );
}

// Array of "Label: Value" strings: rendered as a label/value table.
export function isArrayOfLabelValueStrings(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => typeof item === 'string' && item.includes(': '))
  );
}

export function labelValueStringRows(items: string[]): Record<string, unknown>[] {
  return items.map((item) => {
    const separatorIndex = item.indexOf(': ');
    return {
      label: item.slice(0, separatorIndex),
      value: item.slice(separatorIndex + 2),
    };
  });
}

// Plain key/value object: rendered as a label/value table.
export function isPlainKeyValueObject(value: unknown): value is Record<string, unknown> {
  return isPlainObject(value) && Object.keys(value).length > 0;
}

// Turns each key into a label/value row.
export function keyValueObjectRows(value: Record<string, unknown>): Record<string, unknown>[] {
  return Object.entries(value).map(([label, entryValue]) => ({ label, value: entryValue }));
}

// Columns: every key seen across rows, in first-seen order.
export function arrayTableColumns(rows: Record<string, unknown>[]): string[] {
  const seen = new Set<string>();
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key);
        columns.push(key);
      }
    }
  }
  return columns;
}

// Groups thousands for numbers only; numeric strings stay verbatim.
export function formatNumberWithCommas(value: number): string {
  return value.toLocaleString('en-US', { maximumFractionDigits: 20 });
}

export function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') return formatNumberWithCommas(value);
  if (typeof value === 'object') return valueToString(value);
  return String(value);
}

// Display text for a scalar value, with thousands grouping.
export function formatFieldDisplayValue(value: unknown): string {
  if (typeof value === 'number') return formatNumberWithCommas(value);
  return valueToString(value);
}
