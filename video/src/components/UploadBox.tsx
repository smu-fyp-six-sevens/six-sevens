// UploadBox - the portal's "Upload supporting documents" box, rebuilt from the measured DOM
// (video/public/ui/upload-measure.json, upload-staged.png) so it stays sharp at any zoom and every
// string is editable. Sizes are the portal's CSS px times `u`.

import React from "react";
import { colors, fonts } from "../theme";
import { UI } from "../data/hook";

/** Layout constants in portal CSS px (measured). */
export const BOX = {
  headingH: 48, // "Upload supporting documents" + gap above the box
  dropH: 140, // the dashed drop zone above the first row
  rowH: 63.5,
  footerH: 62,
};

/** Total height in portal px for `rows` rows (fractional rows grow smoothly). */
export const boxHeight = (rows: number) => BOX.headingH + BOX.dropH + rows * BOX.rowH + BOX.footerH;

const kind = (name: string) => (name.split(".").pop() ?? "").toUpperCase();

const Row: React.FC<{ name: string; p: number; u: number; fresh: number }> = ({ name, p, u, fresh }) => {
  const h = BOX.rowH * u;
  return (
    <div style={{ height: h * Math.min(1, p * 1.4), overflow: "visible" }}>
      <div
        style={{
          height: h,
          display: "flex",
          alignItems: "center",
          gap: 12 * u,
          padding: `0 ${14 * u}px`,
          borderTop: `${Math.max(1, u)}px solid ${colors.line}`,
          background: `rgba(53, 95, 150, ${0.09 * fresh})`,
          opacity: Math.min(1, p * 1.6),
          transform: `translateY(${(1 - p) * -22 * u}px)`,
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            width: 28 * u,
            height: 28 * u,
            borderRadius: 5 * u,
            background: colors.ink,
            color: colors.white,
            fontSize: 9 * u,
            fontWeight: 700,
            letterSpacing: 0.2 * u,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          {kind(name)}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 13.5 * u,
              lineHeight: 1.5,
              fontWeight: 600,
              color: colors.ink,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {name}
          </div>
          <div style={{ fontSize: 11.5 * u, lineHeight: 1.4, color: colors.textMuted }}>{UI.rowMeta}</div>
        </div>
        <span
          style={{
            fontSize: 10 * u,
            fontWeight: 600,
            letterSpacing: 0.6 * u,
            textTransform: "uppercase",
            color: colors.textMuted,
            background: colors.pillBg,
            borderRadius: 999,
            padding: `${3 * u}px ${8 * u}px`,
          }}
        >
          {UI.rowBadge}
        </span>
        <span style={{ fontSize: 15 * u, color: colors.ink, width: 14 * u, textAlign: "center" }}>×</span>
      </div>
    </div>
  );
};

export interface UploadBoxProps {
  files: string[];
  /** Per file: 0 not arrived, 1 landed. */
  progress: number[];
  /** Per file: 1 just landed (highlight), fading to 0. */
  fresh: number[];
  /** Portal px to film px. */
  u: number;
  /** Box width in film px. */
  width: number;
}

export const UploadBox: React.FC<UploadBoxProps> = ({ files, progress, fresh, u, width }) => {
  const count = progress.filter((p) => p >= 0.5).length;
  return (
    <div style={{ width, fontFamily: fonts.ui, color: colors.ink }}>
      <div
        style={{
          height: BOX.headingH * u,
          fontSize: 18 * u,
          fontWeight: 700,
          letterSpacing: "-0.01em",
          lineHeight: `${30 * u}px`,
        }}
      >
        {UI.sectionTitle}
      </div>
      <div
        style={{
          background: colors.boxBg,
          border: `${1.2 * u}px dashed ${colors.boxDash}`,
          borderRadius: 10 * u,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: BOX.dropH * u,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 6 * u,
          }}
        >
          <svg width={18 * u} height={18 * u} viewBox="0 0 24 24" fill="none" stroke={colors.steel} strokeWidth={2}>
            <path d="M12 19V5M5 12l7-7 7 7M4 21h16" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <div style={{ fontSize: 14.5 * u, fontWeight: 600 }}>
            {UI.dropTitle}{" "}
            <span style={{ color: colors.steel, textDecoration: "underline" }}>{UI.dropLink}</span>
          </div>
          <div style={{ fontSize: 11.5 * u, color: colors.textMuted }}>{UI.dropHint}</div>
        </div>
        {files.map((name, i) =>
          progress[i] > 0 ? <Row key={name} name={name} p={progress[i]} u={u} fresh={fresh[i]} /> : null,
        )}
        <div
          style={{
            height: BOX.footerH * u,
            borderTop: `${Math.max(1, u)}px solid ${colors.line}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: `0 ${12 * u}px`,
            opacity: count > 0 ? 1 : 0,
          }}
        >
          <span style={{ fontSize: 12.5 * u, color: colors.inkSoft, fontVariantNumeric: "tabular-nums" }}>
            {UI.selected(count)}
          </span>
          <span
            style={{
              fontSize: 13 * u,
              fontWeight: 600,
              color: colors.white,
              background: colors.steel,
              borderRadius: 7 * u,
              padding: `${9 * u}px ${16 * u}px`,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {UI.button(count)}
          </span>
        </div>
      </div>
    </div>
  );
};
