// DocWindow - a generic PDF viewer window with a schematic page: enough shape to read as
// "a statement", "a letter", "a form" at a glance, with no real data on it.

import React from "react";
import { random } from "remotion";
import type { DocKind } from "../data/overwhelm";
import { colors, fonts } from "../theme";

const bar = (w: number | string, h = 7, c = "#dcdcd8", mb = 7): React.CSSProperties => ({
  width: w, height: h, borderRadius: h / 2, background: c, marginBottom: mb,
});

const money = (seed: string, i: number) =>
  (Math.floor(random(`${seed}-${i}`) * 900000) / 100 + 1000).toLocaleString("en-US", { minimumFractionDigits: 2 });

const Page: React.FC<{ kind: DocKind; title: string; company: string; seed: string }> = ({ kind, title, company, seed }) => {
  const head = (
    <div style={{ marginBottom: 18 }}>
      <div style={{ fontSize: 17, fontWeight: 700, color: colors.ink, marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 10.5, color: colors.textMuted }}>{company}</div>
    </div>
  );
  if (kind === "statement") {
    return (
      <>
        {head}
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: colors.textMuted, borderBottom: `1px solid ${colors.line}`, paddingBottom: 5, marginBottom: 6, fontWeight: 600 }}>
          <span>DATE</span><span>DESCRIPTION</span><span>AMOUNT</span>
        </div>
        {Array.from({ length: 14 }, (_, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 9.5, color: colors.inkSoft, height: 19, borderBottom: `1px solid #f0efec` }}>
            <span style={{ width: 44, fontVariantNumeric: "tabular-nums" }}>{String(1 + ((i * 2) % 28)).padStart(2, "0")}/01</span>
            <span style={{ flex: 1 }}><span style={{ ...bar(`${45 + random(`${seed}d${i}`) * 45}%`, 6, "#e3e2de", 0), display: "inline-block" }} /></span>
            <span style={{ fontVariantNumeric: "tabular-nums" }}>{money(seed, i)}</span>
          </div>
        ))}
      </>
    );
  }
  if (kind === "financials") {
    return (
      <>
        {head}
        {Array.from({ length: 12 }, (_, i) => (
          <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 9.5, color: colors.inkSoft, height: 21, fontWeight: i % 4 === 3 ? 700 : 400, borderTop: i % 4 === 3 ? `1px solid ${colors.ink}` : "none" }}>
            <span style={{ ...bar(80 + random(`${seed}f${i}`) * 90, 6, i % 4 === 3 ? "#bfc3cc" : "#e3e2de", 0) }} />
            <span style={{ fontVariantNumeric: "tabular-nums" }}>{money(seed, i + 40)}</span>
          </div>
        ))}
      </>
    );
  }
  if (kind === "form") {
    return (
      <>
        {head}
        {Array.from({ length: 7 }, (_, i) => (
          <div key={i} style={{ marginBottom: 12 }}>
            <div style={bar(60 + random(`${seed}l${i}`) * 60, 5, "#cfd2d8", 5)} />
            <div style={{ height: 20, border: `1px solid ${colors.line}`, borderRadius: 3, background: "#fbfbfa" }} />
          </div>
        ))}
        <div style={{ display: "flex", gap: 16, marginTop: 4 }}>
          {[0, 1, 2].map((i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <div style={{ width: 10, height: 10, border: `1.5px solid ${colors.inkSoft}`, borderRadius: 2 }} />
              <div style={bar(40, 5, "#dcdcd8", 0)} />
            </div>
          ))}
        </div>
      </>
    );
  }
  if (kind === "id") {
    return (
      <>
        {head}
        <div style={{ border: `1px solid ${colors.line}`, borderRadius: 10, padding: 14, display: "flex", gap: 14, background: "#f4f6f9" }}>
          <div style={{ width: 86, height: 108, borderRadius: 6, background: "#d5dae3" }} />
          <div style={{ flex: 1, paddingTop: 4 }}>
            {[70, 90, 55, 80, 60].map((w, i) => (
              <div key={i} style={bar(`${w}%`, i === 0 ? 9 : 6, i === 0 ? "#b9c0cc" : "#dcdfe5", 11)} />
            ))}
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          {[85, 70].map((w, i) => <div key={i} style={bar(`${w}%`)} />)}
        </div>
      </>
    );
  }
  // letter
  return (
    <>
      {head}
      {Array.from({ length: 4 }, (_, p) => (
        <div key={p} style={{ marginBottom: 12 }}>
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} style={bar(i === 3 ? `${40 + random(`${seed}p${p}`) * 40}%` : "100%", 6)} />
          ))}
        </div>
      ))}
      <svg width="120" height="36" viewBox="0 0 120 36" style={{ marginTop: 6 }}>
        <path d="M4 26 C 18 4, 26 34, 40 16 S 62 8, 70 24 S 96 10, 116 18" fill="none" stroke={colors.steel} strokeWidth="2" />
      </svg>
      <div style={bar(110, 5, "#cfd2d8")} />
    </>
  );
};

export const DocWindow: React.FC<{
  title: string; file: string; kind: DocKind; pages: number; company: string; width: number; height: number;
}> = ({ title, file, kind, pages, company, width, height }) => (
  <div
    style={{
      width, height, borderRadius: 10, overflow: "hidden", background: "#eceae6",
      boxShadow: "0 18px 40px rgba(18,32,61,0.18), 0 2px 6px rgba(18,32,61,0.10)",
      border: "1px solid rgba(18,32,61,0.10)", fontFamily: fonts.ui, display: "flex", flexDirection: "column",
    }}
  >
    <div style={{ height: 34, flexShrink: 0, display: "flex", alignItems: "center", gap: 8, padding: "0 12px", background: "#f6f5f2", borderBottom: `1px solid ${colors.line}` }}>
      <div style={{ width: 18, height: 18, borderRadius: 3, background: colors.red, color: "#fff", fontSize: 6.5, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center" }}>PDF</div>
      <div style={{ flex: 1, fontSize: 12, fontWeight: 600, color: colors.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{file}</div>
      <div style={{ fontSize: 11, color: colors.textMuted, fontVariantNumeric: "tabular-nums" }}>1 / {pages}</div>
    </div>
    <div style={{ flex: 1, padding: "16px 22px 0", overflow: "hidden" }}>
      <div style={{ background: "#fff", height: "100%", padding: "22px 24px", boxShadow: "0 1px 3px rgba(0,0,0,0.08)" }}>
        <Page kind={kind} title={title} company={company} seed={file} />
      </div>
    </div>
  </div>
);
