// The backend's group names stay as they are; only what officers read changes.
export function agentGroupLabel(group: string): string {
  return group.replace(/^Fraud detection/, 'Anomaly detection');
}
