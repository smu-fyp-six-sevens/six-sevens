import { useMemo, useState } from 'react';
import { ArrayFieldModal } from './ArrayFieldModal';
import {
  arrayTableColumns,
  isArrayOfLabelValueStrings,
  isArrayOfObjects,
  isPlainKeyValueObject,
  keyValueObjectRows,
  labelValueStringRows,
  valueToString,
} from './fieldDisplay';

type Row = Record<string, unknown>;

interface EditableRowsButtonProps {
  label: string;
  draftJson: string;
  disabled: boolean;
  onChange: (json: string) => void;
  toRows: (parsed: unknown) => Row[];
  toJson: (rows: Row[]) => string;
}

// Shared "Edit N items" trigger and modal. Each field type supplies how its draft becomes rows and back.
function EditableRowsButton({ label, draftJson, disabled, onChange, toRows, toJson }: EditableRowsButtonProps) {
  const [open, setOpen] = useState(false);
  const rows = useMemo<Row[]>(() => {
    try {
      return toRows(JSON.parse(draftJson));
    } catch {
      return [];
    }
  }, [draftJson, toRows]);
  const columns = useMemo(() => arrayTableColumns(rows), [rows]);

  return (
    <>
      <button
        type="button"
        className="array-modal-trigger-button"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        Edit {rows.length} item{rows.length === 1 ? '' : 's'}
      </button>
      {open && (
        <ArrayFieldModal
          title={label}
          columns={columns}
          rows={rows}
          editable
          onClose={() => setOpen(false)}
          onSave={(newRows) => onChange(toJson(newRows))}
        />
      )}
    </>
  );
}

type EditProps = Omit<EditableRowsButtonProps, 'toRows' | 'toJson'>;

function toObjectRows(parsed: unknown): Row[] {
  return isArrayOfObjects(parsed) ? parsed : [];
}

function toKeyValueRows(parsed: unknown): Row[] {
  return isPlainKeyValueObject(parsed) ? keyValueObjectRows(parsed) : [];
}

function toLabelValueRows(parsed: unknown): Row[] {
  return isArrayOfLabelValueStrings(parsed) ? labelValueStringRows(parsed) : [];
}

function toJsonArray(rows: Row[]): string {
  return JSON.stringify(rows);
}

// Drops blank labels; a repeated label overwrites an earlier one, as with any object literal.
function toJsonObject(rows: Row[]): string {
  const rebuilt: Row = {};
  for (const row of rows) {
    const rowLabel = typeof row.label === 'string' ? row.label.trim() : '';
    if (!rowLabel) continue;
    rebuilt[rowLabel] = row.value ?? '';
  }
  return JSON.stringify(rebuilt);
}

// Keeps repeated labels, which an object would collapse.
function toJsonLabelValueArray(rows: Row[]): string {
  const rebuilt = rows
    .map((row) => {
      const rowLabel = typeof row.label === 'string' ? row.label.trim() : '';
      const rowValue =
        typeof row.value === 'string' ? row.value.trim() : valueToString(row.value);
      if (!rowLabel && !rowValue) return null;
      return `${rowLabel}: ${rowValue}`;
    })
    .filter((entry): entry is string => entry !== null);
  return JSON.stringify(rebuilt);
}

// Editable trigger for an array-of-objects field in manual review.
export function ArrayFieldEditButton(props: EditProps) {
  return <EditableRowsButton {...props} toRows={toObjectRows} toJson={toJsonArray} />;
}

// Editable trigger for a key/value object field.
export function ObjectFieldEditButton(props: EditProps) {
  return <EditableRowsButton {...props} toRows={toKeyValueRows} toJson={toJsonObject} />;
}

// Editable trigger for an array of "Label: Value" strings.
export function LabelValueArrayEditButton(props: EditProps) {
  return <EditableRowsButton {...props} toRows={toLabelValueRows} toJson={toJsonLabelValueArray} />;
}
