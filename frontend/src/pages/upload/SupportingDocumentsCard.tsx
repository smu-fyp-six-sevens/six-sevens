import type { RecommendationItem, SupportingDocumentRecommendation } from '../../services/uploadService';

const formatMoney = (value: number) =>
  value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// "+54,080.00 (+18.3%)"
function formatChange(previous: number | null, current: number): string {
  if (previous == null) return '';
  const increase = current - previous;
  const sign = increase >= 0 ? '+' : '-';
  const percent = previous ? ` (${sign}${Math.abs((increase / previous) * 100).toFixed(1)}%)` : '';
  return `${sign}${formatMoney(Math.abs(increase))}${percent}`;
}

// The lines a total is made of, with the total as its last row — laid out like the
// balance sheet it comes from.
function BreakdownTable({ total }: { total: RecommendationItem }) {
  return (
    <table className="supporting-document-breakdown">
      <thead>
        <tr>
          <th scope="col">Breakdown</th>
          <th scope="col">Prior year</th>
          <th scope="col">This year</th>
          <th scope="col">Change</th>
        </tr>
      </thead>
      <tbody>
        {[...total.breakdown, total].map((line, index) => (
          <tr key={index} className={line === total ? 'supporting-document-breakdown-total' : undefined}>
            <th scope="row">{line.description}</th>
            <td>{line.prior_amount == null ? '' : formatMoney(line.prior_amount)}</td>
            <td>{formatMoney(line.amount)}</td>
            <td>{formatChange(line.prior_amount, line.amount)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// Produced by the case analysis run (backend supporting_documents.py), from what the case's documents show.
export function SupportingDocumentsCard({
  recommendations,
  analysed,
}: {
  recommendations: SupportingDocumentRecommendation[];
  analysed: boolean;
}) {
  return (
    <section className="summary-card">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon summary-icon--audit">⊕</span>
          <h2>Recommended Supporting Documents</h2>
        </div>

        {recommendations.length > 0 && (
          <span className="summary-count">
            {recommendations.length} {recommendations.length === 1 ? 'request' : 'requests'}
          </span>
        )}
      </div>

      {!analysed ? (
        <div className="summary-message summary-message--info">
          <span className="summary-message-icon">ⓘ</span>

          <div>
            <strong>Not checked yet.</strong>

            <p>Run the case analysis to see which further documents to request from the client.</p>
          </div>
        </div>
      ) : recommendations.length === 0 ? (
        <div className="summary-message summary-message--success">
          <span className="summary-message-icon">✓</span>

          <div>
            <strong>No additional documents needed.</strong>

            <p>
              If anything in the submitted documents needs further evidence, the documents to
              request from the client will be listed here.
            </p>
          </div>
        </div>
      ) : (
        <div className="flagged-list">
          {recommendations.map((recommendation) => (
            <div
              key={`${recommendation.source_document_id}-${recommendation.document_type}`}
              className="flagged-item"
            >
              <div className="flagged-item-main">
                <strong>{recommendation.title}</strong>

                <p>{recommendation.reason}</p>

                <p className="supporting-document-request">{recommendation.request}</p>

                <ul className="supporting-document-items">
                  {recommendation.items.map((item, index) => (
                    <li key={index} title={item.description}>
                      {item.reason}

                      {item.breakdown.length > 0 && <BreakdownTable total={item} />}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
