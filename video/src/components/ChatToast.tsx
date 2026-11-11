// ChatToast - a generic chat notification (no real product's look).

import React from "react";
import { colors, fonts } from "../theme";

export const ChatToast: React.FC<{ name: string; team: string; text: string; tint: string; width: number }> = ({ name, team, text, tint, width }) => (
  <div
    style={{
      width, boxSizing: "border-box", display: "flex", gap: 14, padding: "14px 16px", borderRadius: 14,
      background: "#ffffff", boxShadow: "0 14px 34px rgba(18,32,61,0.22), 0 2px 6px rgba(18,32,61,0.10)",
      border: "1px solid rgba(18,32,61,0.08)", fontFamily: fonts.ui,
    }}
  >
    <div style={{ width: 40, height: 40, borderRadius: 20, background: tint, color: "#fff", fontWeight: 700, fontSize: 15, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
      {name.split(" ").map((w) => w[0]).join("").slice(0, 2)}
    </div>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 3 }}>
        <span style={{ fontSize: 15, fontWeight: 700, color: colors.ink }}>{name}</span>
        <span style={{ fontSize: 12, color: colors.textMuted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flex: 1 }}>{team}</span>
        <span style={{ fontSize: 12, color: colors.textMuted }}>now</span>
      </div>
      <div style={{ fontSize: 16, lineHeight: 1.35, color: colors.ink }}>{text}</div>
    </div>
  </div>
);
