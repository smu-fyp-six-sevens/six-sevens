// The score range of each level; mirrors SCORE_BANDS in backend/app/case_pipeline/risk/case_risk.py.
export const SCORE_BANDS = [
  { level: 'Low', min: 0, max: 24 },
  { level: 'Medium', min: 25, max: 49 },
  { level: 'High', min: 50, max: 79 },
  { level: 'Critical', min: 80, max: 100 },
] as const;
