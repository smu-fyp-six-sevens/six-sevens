import {
  ADDITIONAL_DOCUMENT_TYPES,
  REQUIRED_DOCUMENT_TYPES,
} from '../../services/uploadService';

export function RequirementsSidebar({
  fulfilledRequirements,
  idDocumentCoverage,
  identityNames,
  proofOfAddressCoverage,
}: {
  fulfilledRequirements: Set<string>;
  idDocumentCoverage: { covered: number; total: number; missing_side: string | null };
  identityNames: string[];
  proofOfAddressCoverage: { covered: number; total: number; unmatched_names: string[] };
}) {
  return (
          <aside className="upload-sidebar">
            <div className="section-label">
              <span>Required documents</span>

              <span>
                {
                  REQUIRED_DOCUMENT_TYPES.filter((requirement) =>
                    fulfilledRequirements.has(requirement.value),
                  ).length
                }{' '}
                / {REQUIRED_DOCUMENT_TYPES.length}
              </span>
            </div>

            <p className="requirements-note">Present in every case</p>

            <ul className="requirements-list">
              {REQUIRED_DOCUMENT_TYPES.map((requirement) => {
                const isFulfilled =
                  fulfilledRequirements.has(requirement.value);

                // proof_of_address is the one requirement that isn't just yes/no.
                const isProofOfAddress =
                  requirement.value === 'proof_of_address';

                const showProgress =
                  isProofOfAddress &&
                  proofOfAddressCoverage.total > 0;

                const unmatchedNames = isProofOfAddress
                  ? proofOfAddressCoverage.unmatched_names
                  : [];

                // id_document needs both a front and a back capture.
                const isIdDocument = requirement.value === 'id_document';

                const showIdProgress =
                  isIdDocument && idDocumentCoverage.total > 0;

                // missing_side is only set when the case has exactly one identified individual.
                const idDocumentHint =
                  isIdDocument && idDocumentCoverage.missing_side
                    ? idDocumentCoverage.missing_side === 'front'
                      ? 'Back photo present, front missing.'
                      : 'Front photo present, back missing.'
                    : null;

                return (
                  <li key={requirement.value}>
                    <div
                      className={`requirement-row${
                        isFulfilled ? ' fulfilled' : ''
                      }`}
                    >
                      <span className="requirement-dot" />

                      {requirement.label}

                      {showProgress && (
                        <span className="requirement-progress">
                          {' '}
                          ({proofOfAddressCoverage.covered}/
                          {proofOfAddressCoverage.total})
                        </span>
                      )}

                      {showIdProgress && (
                        <span className="requirement-progress">
                          {' '}
                          ({idDocumentCoverage.covered}/
                          {idDocumentCoverage.total})
                        </span>
                      )}
                    </div>

                    {unmatchedNames.length > 0 && (
                      <div className="requirement-unmatched">
                        Address on file but no matching ID uploaded:{' '}
                        {unmatchedNames.join(', ')}
                      </div>
                    )}

                    {isIdDocument && identityNames.length > 0 && (
                      <ul className="requirement-identities">
                        {identityNames.map((name) => (
                          <li key={name}>{name}</li>
                        ))}
                      </ul>
                    )}

                    {idDocumentHint && (
                      <div className="requirement-unmatched">{idDocumentHint}</div>
                    )}
                  </li>
                );
              })}
            </ul>

            <div className="section-label">
              <span>Additional documents</span>
            </div>

            <p className="requirements-note">May be required depending on the case</p>

            <ul className="requirements-list">
              {ADDITIONAL_DOCUMENT_TYPES.map((requirement) => (
                <li key={requirement.value}>
                  <div
                    className={`requirement-row${
                      fulfilledRequirements.has(requirement.value)
                        ? ' fulfilled'
                        : ''
                    }`}
                  >
                    <span className="requirement-dot" />

                    {requirement.label}
                  </div>

                </li>
              ))}
            </ul>
          </aside>
  );
}
