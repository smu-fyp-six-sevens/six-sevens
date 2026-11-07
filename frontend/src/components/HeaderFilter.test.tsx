import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import HeaderFilter from './HeaderFilter';
import type { FilterOption } from './HeaderFilter';

const STATUSES: FilterOption[] = ['Awaiting Documents', 'Pending Review', 'Finalised'].map((text) => ({
  value: text, label: text, text,
}));

// The filter as the dashboard and case queue use it: the page holds the selection, the filter reports each change.
function StatusFilter({ onChange }: { onChange: (next: Set<string>) => void }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  return (
    <HeaderFilter
      name="status"
      options={STATUSES}
      selected={selected}
      onChange={(next) => {
        setSelected(next);
        onChange(next);
      }}
    />
  );
}

function open(onChange = vi.fn()) {
  render(<StatusFilter onChange={onChange} />);
  fireEvent.click(screen.getByRole('button', { name: 'Filter by status' }));
  return onChange;
}

const box = (name: string) => screen.getByRole('checkbox', { name }) as HTMLInputElement;
const filteredTo = (onChange: ReturnType<typeof vi.fn>) => [...(onChange.mock.lastCall?.[0] as Set<string>)].sort();

describe('the status filter', () => {
  it('starts unfiltered, shown as every box ticked', () => {
    open();

    expect(STATUSES.map((s) => box(s.text).checked)).toEqual([true, true, true]);
  });

  it('unticking one status keeps all the others', () => {
    const onChange = open();

    fireEvent.click(box('Finalised'));

    expect(filteredTo(onChange)).toEqual(['Awaiting Documents', 'Pending Review']);
    expect(screen.queryByRole('button', { name: 'Filter by status (2 selected)' })).not.toBeNull();
  });

  it('ticking every status again is the same as no filter', () => {
    const onChange = open();
    fireEvent.click(box('Finalised'));

    fireEvent.click(box('Finalised'));

    expect(filteredTo(onChange)).toEqual([]);
    expect(screen.queryByRole('button', { name: 'Filter by status' })).not.toBeNull();
  });

  it('after Unselect all, the statuses ticked are the only ones kept', () => {
    const onChange = open();

    fireEvent.click(screen.getByRole('button', { name: 'Unselect all' }));
    expect(STATUSES.map((s) => box(s.text).checked)).toEqual([false, false, false]);
    expect(filteredTo(onChange)).toEqual([]); // nothing ticked yet: still no filter
    fireEvent.click(box('Pending Review'));

    expect(filteredTo(onChange)).toEqual(['Pending Review']);
  });

  it('Select all goes back to no filter', () => {
    const onChange = open();
    fireEvent.click(box('Finalised'));

    fireEvent.click(screen.getByRole('button', { name: 'Select all' }));

    expect(filteredTo(onChange)).toEqual([]);
    expect(STATUSES.map((s) => box(s.text).checked)).toEqual([true, true, true]);
  });
});
