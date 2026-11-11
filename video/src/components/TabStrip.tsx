// TabStrip - a generic browser tab strip that keeps opening tabs until they squash to icons.

import React from "react";
import { colors, fonts } from "../theme";

export const TabStrip: React.FC<{ tabs: { label: string; pdf: boolean; p: number }[]; width: number }> = ({ tabs, width }) => {
  const shown = tabs.filter((t) => t.p > 0);
  const n = Math.max(1, shown.reduce((sum, t) => sum + t.p, 0));
  const tabW = Math.min(220, (width - 70) / n);
  return (
    <div style={{ height: 46, display: "flex", alignItems: "flex-end", padding: "0 10px", background: "#dedcd6", fontFamily: fonts.ui, overflow: "hidden" }}>
      {shown.map((t, i) => {
        const active = i === shown.length - 1;
        return (
          <div
            key={i}
            style={{
              width: tabW * t.p, height: 36, flexShrink: 0, overflow: "hidden",
              display: "flex", alignItems: "center", gap: 6, padding: tabW > 40 ? "0 10px" : "0 0 0 6px",
              background: active ? "#f6f5f2" : "transparent", borderRadius: "8px 8px 0 0",
              borderRight: active ? "none" : "1px solid rgba(18,32,61,0.12)", boxSizing: "border-box",
            }}
          >
            <div style={{ width: 14, height: 14, flexShrink: 0, borderRadius: 3, background: t.pdf ? colors.red : colors.steel }} />
            {tabW > 60 && (
              <div style={{ fontSize: 12, color: colors.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{t.label}</div>
            )}
          </div>
        );
      })}
      <div style={{ fontSize: 20, color: colors.inkSoft, padding: "0 10px 6px", flexShrink: 0 }}>+</div>
    </div>
  );
};
