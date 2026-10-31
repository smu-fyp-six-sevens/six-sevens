import { COMPANY_NAME_COUNT_FROM, MAX_COMPANY_NAME_LENGTH } from '../utils/caseValidation';

// Below a company name field: nothing until the name is 80 characters long, then "80/100", and at the limit
// "100/100" in red with why typing stops (the input's maxLength stops it).
export function CharacterCount({
  length,
  max = MAX_COMPANY_NAME_LENGTH,
  from = COMPANY_NAME_COUNT_FROM,
  id,
}: {
  length: number;
  max?: number;
  from?: number;
  id?: string;
}) {
  if (length < from) return null;
  const atLimit = length >= max;
  return (
    <p id={id} className={`character-count${atLimit ? ' character-count--limit' : ''}`} aria-live="polite">
      {length}/{max}
      {atLimit && ` · You cannot type more than ${max} characters.`}
    </p>
  );
}
