// StepTracker - the "how it works" steps (the portal's own: Upload, Check, Score). Every product section shows
// it at the top, the current step lit, so the viewer always knows where they are.

import React from "react";
import { colors, fonts } from "../theme";

export const StepTracker: React.FC<{ steps: readonly string[]; current: number; p?: number }> = ({ steps, current, p = 1 }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 14, fontFamily: fonts.ui, opacity: p, transform: `translateY(${(1 - p) * -16}px)` }}>
    {steps.map((label, i) => {
      const on = i === current;
      const done = i < current;
      return (
        <React.Fragment key={label}>
          {i > 0 && <div style={{ width: 54, height: 3, borderRadius: 2, background: done || on ? colors.steel : colors.line }} />}
          <div
            style={{
              display: "flex", alignItems: "center", gap: 10, padding: "8px 18px 8px 8px", borderRadius: 999,
              background: on ? colors.steel : colors.white, border: `2px solid ${on || done ? colors.steel : colors.line}`,
              boxShadow: on ? "0 8px 20px rgba(53,95,150,0.28)" : "none", transform: `scale(${on ? 1.06 : 1})`,
            }}
          >
            <span style={{ width: 30, height: 30, borderRadius: 15, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16, fontWeight: 700, background: on ? colors.white : done ? colors.steel : colors.paper, color: on ? colors.steel : done ? colors.white : colors.textMuted }}>
              {done ? "✓" : i + 1}
            </span>
            <span style={{ fontSize: 22, fontWeight: 700, color: on ? colors.white : done ? colors.steel : colors.textMuted }}>{label}</span>
          </div>
        </React.Fragment>
      );
    })}
  </div>
);
