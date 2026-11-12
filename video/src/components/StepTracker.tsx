// StepTracker - the "how it works" steps (Upload, Detect, Score). Every product section shows it at the top, the
// current step lit, so the viewer always knows where they are. `current` may be fractional: 0.5 is halfway from
// step 1 to step 2, so a section can animate the hand-over from the previous step.

import React from "react";
import { blendHex, colors, fonts } from "../theme";

export const StepTracker: React.FC<{ steps: readonly string[]; current: number; p?: number }> = ({ steps, current, p = 1 }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 14, fontFamily: fonts.ui, opacity: p, transform: `translateY(${(1 - p) * -16}px)` }}>
    {steps.map((label, i) => {
      const on = Math.max(0, 1 - Math.abs(current - i)); // 1 when this is the current step
      const done = current - i > 0.5;
      const fill = Math.max(0, Math.min(1, current - (i - 1))); // the connector into this step
      const bg = blendHex(colors.white, colors.steel, on);
      const fg = on > 0.5 ? colors.white : done ? colors.steel : colors.textMuted;
      return (
        <React.Fragment key={label}>
          {i > 0 && (
            <div style={{ width: 54, height: 3, borderRadius: 2, background: colors.line, overflow: "hidden" }}>
              <div style={{ width: `${fill * 100}%`, height: 3, background: colors.steel }} />
            </div>
          )}
          <div
            style={{
              display: "flex", alignItems: "center", gap: 10, padding: "8px 18px 8px 8px", borderRadius: 999,
              background: bg, border: `2px solid ${on > 0.05 || done ? colors.steel : colors.line}`,
              boxShadow: `0 8px 20px rgba(53,95,150,${0.28 * on})`, transform: `scale(${1 + 0.06 * on})`,
            }}
          >
            <span style={{ width: 30, height: 30, borderRadius: 15, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16, fontWeight: 700, background: on > 0.5 ? colors.white : done ? colors.steel : colors.paper, color: on > 0.5 ? colors.steel : done ? colors.white : colors.textMuted }}>
              {done && on < 0.5 ? "✓" : i + 1}
            </span>
            <span style={{ fontSize: 22, fontWeight: 700, color: fg }}>{label}</span>
          </div>
        </React.Fragment>
      );
    })}
  </div>
);
