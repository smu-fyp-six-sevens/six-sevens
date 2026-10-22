import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { arrayTableColumns, formatCellValue } from './fieldDisplay';
import { humanizeFieldKey } from '../../utils/fieldLabels';

// Matches the width at which the document page shows the preview beside the fields (document-details.css).
const SIDE_BY_SIDE_QUERY = '(min-width: 1101px)';

interface DockBox {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

// While the document page shows its preview side by side, the box the editor docks into: the fields
// column's width, and the preview's top and bottom. It then sits as a mirror of the preview instead of
// covering it, so rows can be checked against the document as they're edited. Re-measured on scroll,
// since the preview only settles at its sticky position once the page has scrolled.
function usePreviewDockBox(): DockBox | null {
  const [box, setBox] = useState<DockBox | null>(null);

  useEffect(() => {
    function measure() {
      const pane = document.querySelector('.document-preview-pane');
      const fields = document.querySelector('.document-details-main');
      if (!pane || !fields || !window.matchMedia(SIDE_BY_SIDE_QUERY).matches) {
        setBox(null);
        return;
      }
      const paneRect = pane.getBoundingClientRect();
      const fieldsRect = fields.getBoundingClientRect();
      setBox({ left: fieldsRect.left, right: fieldsRect.right, top: paneRect.top, bottom: paneRect.bottom });
    }
    measure();
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, { passive: true });
    return () => {
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure);
    };
  }, []);

  return box;
}

// Modal for viewing or editing an array of rows; portal-rendered. Beside an open document preview it
// docks over the fields column only, leaving the preview visible and scrollable.
export function ArrayFieldModal({
  title,
  columns,
  rows: initialRows,
  editable,
  onClose,
  onSave,
}: {
  title: string;
  columns: string[];
  rows: Record<string, unknown>[];
  editable: boolean;
  onClose: () => void;
  onSave?: (rows: Record<string, unknown>[]) => void;
}) {
  const [rows, setRows] = useState(initialRows);
  const dock = usePreviewDockBox();
  const docked = dock !== null;
  // The backdrop starts at the fields column (so the preview stays clickable) and runs to the viewport's
  // right edge; its padding places the editor exactly over the column, level with the preview.
  const dockStyle = dock
    ? {
        left: dock.left,
        paddingTop: dock.top,
        paddingBottom: Math.max(0, document.documentElement.clientHeight - dock.bottom),
        paddingLeft: 0,
        paddingRight: Math.max(0, document.documentElement.clientWidth - dock.right),
      }
    : undefined;

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  function updateCell(rowIndex: number, column: string, value: string) {
    setRows((current) =>
      current.map((row, i) => (i === rowIndex ? { ...row, [column]: value } : row)),
    );
  }

  function removeRow(rowIndex: number) {
    setRows((current) => current.filter((_, i) => i !== rowIndex));
  }

  function addRow() {
    const blank: Record<string, unknown> = {};
    for (const column of columns) blank[column] = '';
    setRows((current) => [...current, blank]);
  }

  return createPortal(
    <div
      className={`array-modal-backdrop${docked ? ' array-modal-backdrop--docked' : ''}`}
      style={dockStyle}
      onClick={onClose}
    >
      <div
        className="array-modal"
        role="dialog"
        // Docked, the preview beside it stays usable, so the page isn't modal.
        aria-modal={!docked}
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="array-modal-header">
          <h3>{title}</h3>
          <button type="button" className="array-modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="array-modal-body">
          <table>
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column}>{humanizeFieldKey(column)}</th>
                ))}
                {editable && <th aria-label="Row actions" />}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {columns.map((column) => (
                    <td key={column}>
                      {editable ? (
                        <input
                          className="array-modal-input"
                          value={row[column] === null || row[column] === undefined ? '' : String(row[column])}
                          onChange={(event) => updateCell(rowIndex, column, event.target.value)}
                        />
                      ) : (
                        formatCellValue(row[column])
                      )}
                    </td>
                  ))}
                  {editable && (
                    <td>
                      <button
                        type="button"
                        className="array-modal-remove-row"
                        aria-label="Remove row"
                        onClick={() => removeRow(rowIndex)}
                      >
                        ✕
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={columns.length + (editable ? 1 : 0)} className="array-modal-empty">
                    No rows.
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          {editable && (
            <button type="button" className="array-modal-add-row" onClick={addRow}>
              + Add row
            </button>
          )}
        </div>

        {editable && (
          <div className="array-modal-footer">
            <button type="button" className="array-modal-cancel" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="array-modal-save"
              onClick={() => {
                onSave?.(rows);
                onClose();
              }}
            >
              Save
            </button>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

// Read-only trigger for the extracted-information table.
export function ArrayValueCell({ label, rows }: { label: string; rows: Record<string, unknown>[] }) {
  const [open, setOpen] = useState(false);
  const columns = useMemo(() => arrayTableColumns(rows), [rows]);

  return (
    <>
      <button type="button" className="array-value-trigger" onClick={() => setOpen(true)}>
        {rows.length} item{rows.length === 1 ? '' : 's'}
        <span className="array-value-hint"> (view)</span>
      </button>
      {open && (
        <ArrayFieldModal title={label} columns={columns} rows={rows} editable={false} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
