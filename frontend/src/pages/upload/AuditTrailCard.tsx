export function AuditTrailCard() {
  return (
    <section className="summary-card">
      <div className="summary-card-header">
        <div className="summary-card-title">
          <span className="summary-icon summary-icon--audit">
            ◴
          </span>
          <h2>Case Audit Trail</h2>
        </div>
      </div>

      <div className="summary-message summary-message--info">
        <span className="summary-message-icon">ⓘ</span>

        <div>
          <strong>No audit events available yet.</strong>

          <p>
            Case activity history will appear here once actions
            are recorded.
          </p>
        </div>
      </div>
    </section>
  );
}
