// The risk scale the dashboard's panels share. 'Not scored' is a case with no analysis yet.
export type Risk = 'Critical' | 'High' | 'Medium' | 'Low';
export type Level = Risk | 'Not scored';

export const LEVELS: Level[] = ['Critical', 'High', 'Medium', 'Low', 'Not scored'];
export const RISK_RANK: Record<Risk, number> = { Critical: 4, High: 3, Medium: 2, Low: 1 };

export const levelClass = (level: Level) => (level === 'Not scored' ? 'risk-overview--none' : `risk-gauge-band--${level.toLowerCase()}`);
