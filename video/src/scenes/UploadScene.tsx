// Section 5 - "upload" (how it works, step 1).
//   A. A cursor drags the whole package into the portal's upload box; 20 rows cascade in; it clicks Upload.
//   B. The box clears and the documents table fills: row by row, Processing, then the type stamps on and the
//      status flips to Extracted.
//   C. The scanned December statement: a scan beam runs down the crooked page and its key fields fill the
//      portal's Extracted Information card as the beam passes them.
// The page behind is the real portal (capture/howitworks.spec.ts); the parts that move are rebuilt from its
// measured styles. Times: UPLOAD in timing.ts. Words: data/upload.ts.

import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { Pointer } from "../components/Pointer";
import { StepTracker } from "../components/StepTracker";
import { Title } from "../components/Title";
import { BOX, UploadBox } from "../components/UploadBox";
import { DEC_STATEMENT, PACKAGE, SCAN_FIELDS, SCREENS_HOW, STEPS, UPLOAD_COPY } from "../data/upload";
import { colors, fonts } from "../theme";
import { FPS, UPLOAD as T, s } from "../timing";
import { Screen, WIN, Window } from "./PortalScene";
import { CLOSE_H, CLOSE_Y, H, Statement, W } from "./TamperScene";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);
const N = PACKAGE.length;
const K0 = WIN.w / 1440;

// ---- Where things are on the captured page (CSS px, measured) ----------------------------------
const BOX_AT = { x: 116, y: 267, w: 1285 };
const CARD_AT = { x: 116, y: 436, w: 1285 };
const CARD_HEAD = 76;
const TABLE_HEAD = 42;
const ROW = 52;
const COL = { mark: 34, type: 83, file: 405, status: 1030 };
const boxBottom = (rows: number) => BOX_AT.y + BOX.dropH + rows * BOX.rowH + BOX.footerH;
const BUTTON = { x: BOX_AT.x + BOX_AT.w - 12 - 64, y: boxBottom(N) - BOX.footerH / 2 };
const DROP = { x: BOX_AT.x + BOX_AT.w / 2, y: BOX_AT.y + 70 };

// Required document types (the portal's checklist for this case, 11 of them) that each file satisfies.
const REQUIRED: Record<string, string> = {
  NRIC: "id", Passport: "id", "Bank Statement": "bank", "Proof of Address": "poa", "FATCA/CRS Declaration": "fatca",
  "Certificate of Incorporation": "inc", "Certificate of Incumbency": "incumbency", "Company Constitution": "constitution",
  "Credit Report": "credit", Payslip: "payslip", "Source of Wealth": "wealth", "Notice of Assessment": "noa",
};

// ---- Timing helpers ---------------------------------------------------------------------------
const cascadeAt = (i: number) => T.dropAt + ((T.cascadeEnd - T.dropAt) * i) / (N - 1);
const ROW_DUR = 0.8;
const rowAt = (i: number) => T.classifyStart + ((T.classifyEnd - T.classifyStart - ROW_DUR) * i) / (N - 1);

// ---- The camera on the documents page, smoothed by averaging its target over the last few frames ----
const camTarget = (f: number) => {
  const t = f / FPS;
  if (t < T.tableAt) {
    const rows = PACKAGE.reduce((n, _, i) => n + interpolate(t, [cascadeAt(i), cascadeAt(i) + 0.15], [0, 1], clamp), 0);
    const zoom = 1.1;
    const half = WIN.h / (K0 * zoom) / 2;
    const followBox = Math.max(420, boxBottom(rows) - half + 50);
    const toButton = interpolate(t, [T.clickAt - 0.7, T.clickAt - 0.2], [0, 1], clamp);
    return { zoom, x: 758 + 200 * toButton, y: followBox };
  }
  const zoom = 1.25;
  const half = WIN.h / (K0 * zoom) / 2;
  const u = interpolate(t, [T.classifyStart, T.classifyEnd], [0, N - 1], clamp);
  const rowY = CARD_AT.y + CARD_HEAD + TABLE_HEAD + u * ROW;
  return { zoom, x: 700, y: Math.max(CARD_AT.y + half - 60, rowY - half * 0.35) };
};
const camera = (f: number) => {
  const k = 9;
  let zoom = 0, x = 0, y = 0;
  for (let i = 0; i < k; i++) { const c = camTarget(Math.max(0, f - i)); zoom += c.zoom; x += c.x; y += c.y; }
  return { zoom: zoom / k, x: x / k, y: y / k };
};

// ---- Pieces -----------------------------------------------------------------------------------
const Stack: React.FC = () => (
  <div style={{ position: "absolute", left: 10, top: 18 }}>
    {[2, 1, 0].map((i) => (
      <div key={i} style={{ position: "absolute", left: i * 9, top: -i * 6, width: 64, height: 82, borderRadius: 6, background: colors.white, border: `1px solid ${colors.line}`, boxShadow: "0 6px 16px rgba(18,32,61,0.18)", transform: `rotate(${(i - 1) * 7}deg)` }}>
        <div style={{ margin: "10px 8px", width: 18, height: 10, borderRadius: 2, background: colors.red }} />
        {[38, 44, 30].map((w, j) => <div key={j} style={{ margin: "5px 8px", width: w, height: 4, borderRadius: 2, background: "#e3e2de" }} />)}
      </div>
    ))}
    <div style={{ position: "absolute", left: 46, top: 58, padding: "3px 10px", borderRadius: 999, background: colors.steel, color: colors.white, fontFamily: fonts.ui, fontSize: 12, fontWeight: 700, whiteSpace: "nowrap" }}>
      {UPLOAD_COPY.stackBadge(N)}
    </div>
  </div>
);

const Spinner: React.FC<{ f: number; size: number }> = ({ f, size }) => (
  <svg width={size} height={size} viewBox="0 0 20 20" style={{ transform: `rotate(${f * 14}deg)` }}>
    <circle cx="10" cy="10" r="7.5" fill="none" stroke="#dbe3ee" strokeWidth="2.5" />
    <path d="M10 2.5 A7.5 7.5 0 0 1 17.5 10" fill="none" stroke={colors.steel} strokeWidth="2.5" strokeLinecap="round" />
  </svg>
);

const Pill: React.FC<{ kind: "wait" | "work" | "ok"; text: string; pop?: number }> = ({ kind, text, pop = 1 }) => {
  const c = kind === "ok" ? { bg: "#e1f1ec", fg: "#166d5f" } : kind === "work" ? { bg: "#e4ebf4", fg: colors.steel } : { bg: "#f1f0ed", fg: colors.textMuted };
  return <span style={{ display: "inline-block", font: `600 12px/18px ${fonts.ui}`, padding: "2px 10px", borderRadius: 999, background: c.bg, color: c.fg, transform: `scale(${1.25 - 0.25 * pop})`, transformOrigin: "0 50%" }}>{text}</span>;
};

const DocumentsCard: React.FC<{ f: number }> = ({ f }) => {
  const t = f / FPS;
  const states = PACKAGE.map((_, i) => (t - rowAt(i)) / ROW_DUR);
  const done = states.map((u) => u >= 0.55);
  const covered = new Set(PACKAGE.filter((_, i) => done[i]).map((d) => REQUIRED[d.type]).filter(Boolean));
  const n = Math.min(11, covered.size);
  return (
    <div style={{ position: "absolute", left: CARD_AT.x, top: CARD_AT.y, width: CARD_AT.w, background: colors.white, border: `1px solid ${colors.line}`, borderRadius: 12, overflow: "hidden", fontFamily: fonts.ui }}>
      <div style={{ height: CARD_HEAD, display: "flex", alignItems: "center", gap: 12, padding: "0 26px" }}>
        <svg width="18" height="14" viewBox="0 0 18 14"><g stroke={colors.ink} strokeWidth="1.8" strokeLinecap="round"><path d="M1 2h1M5 2h12M1 7h1M5 7h12M1 12h1M5 12h12" /></g></svg>
        <span style={{ fontSize: 17, fontWeight: 700, color: colors.ink }}>Documents</span>
        <span style={{ fontSize: 14, fontWeight: 700, color: colors.ink, fontVariantNumeric: "tabular-nums" }}>{n}</span>
        <span style={{ fontSize: 14, color: colors.inkSoft }}>of 11 required</span>
        <span style={{ width: 90, height: 5, borderRadius: 3, background: "#e7e9ee", overflow: "hidden" }}><span style={{ display: "block", height: 5, width: `${(n / 11) * 100}%`, background: colors.steel }} /></span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 11, fontWeight: 600, color: "#166d5f", background: "#e1f1ec", borderRadius: 999, padding: "3px 10px" }}>● LIVE</span>
      </div>
      <div style={{ height: TABLE_HEAD, position: "relative", background: "#f7f8fa", borderTop: `1px solid ${colors.line}`, borderBottom: `1px solid ${colors.line}`, font: `700 11px/16.5px ${fonts.ui}`, letterSpacing: 0.88, textTransform: "uppercase", color: colors.textMuted }}>
        <span style={{ position: "absolute", left: COL.mark - 1, top: 13, width: 16, height: 16, border: "1.5px solid #9aa3b2", borderRadius: 3 }} />
        {[["Document Type", COL.type], ["Filename", COL.file], ["Status", COL.status]].map(([label, x]) => (
          <span key={label as string} style={{ position: "absolute", left: x as number, top: 13 }}>{label}</span>
        ))}
      </div>
      {PACKAGE.map((d, i) => {
        const u = states[i];
        const stamp = land(interpolate(u, [0.55, 0.8], [0, 1], clamp));
        const working = u >= 0 && u < 0.55;
        return (
          <div key={i} style={{ height: ROW, position: "relative", borderTop: i ? "1px solid #eef0f3" : "none", background: done[i] && u < 1.6 ? `rgba(22,109,95,${0.06 * (1 - (u - 0.55))})` : "transparent" }}>
            <span style={{ position: "absolute", left: COL.mark - 2, top: 16 }}>
              {done[i] ? (
                <span style={{ display: "flex", width: 20, height: 20, borderRadius: 10, background: "#166d5f", alignItems: "center", justifyContent: "center", transform: `scale(${0.6 + 0.4 * stamp})` }}>
                  <svg width="11" height="11" viewBox="0 0 16 16"><path d="M3 8.5 L6.5 12 L13 4.5" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </span>
              ) : working ? <Spinner f={f} size={20} /> : <span style={{ display: "block", width: 16, height: 16, margin: 2, borderRadius: 8, border: "2px dashed #c5cad3" }} />}
            </span>
            <span style={{ position: "absolute", left: COL.type, top: 16, font: `600 14px/20px ${fonts.ui}`, color: colors.ink, whiteSpace: "nowrap" }}>
              {done[i] ? (
                <span style={{ display: "inline-block", transform: `scale(${1.35 - 0.35 * stamp})`, opacity: stamp, transformOrigin: "0 50%" }}>{d.type}</span>
              ) : working ? (
                <span style={{ display: "inline-block", width: 150, height: 10, marginTop: 5, borderRadius: 5, background: `linear-gradient(90deg, #edf0f4 ${((f * 4) % 200) - 60}%, #dfe5ee ${((f * 4) % 200) - 30}%, #edf0f4 ${(f * 4) % 200}%)` }} />
              ) : null}
            </span>
            <span style={{ position: "absolute", left: COL.file, top: 16, font: `13px/19.5px ${fonts.ui}`, color: colors.inkSoft, whiteSpace: "nowrap" }}>
              {d.file}{d.scan && <span style={{ marginLeft: 10, font: `600 11px/16px ${fonts.ui}`, color: colors.amber, background: colors.amberBg, borderRadius: 4, padding: "1px 6px" }}>SCAN</span>}
            </span>
            <span style={{ position: "absolute", left: COL.status, top: 15 }}>
              {done[i] ? <Pill kind="ok" text="Extracted" pop={stamp} /> : working ? <Pill kind="work" text="Processing" /> : <Pill kind="wait" text="Uploading" />}
            </span>
          </div>
        );
      })}
    </div>
  );
};

// ---- C: the scanned statement ---------------------------------------------------------------
const PANE = { x: 117, y: 232, w: 629, h: 754 };
const PAGE_K = 1.18;
const CARD2 = { x: 772, y: 475, w: 629 };
// Where each field sits on the statement page (its own px), and when the beam reaches it.
const ANCHOR = { title: { y: 72, h: 24, w: 200 }, company: { y: 97, h: 16, w: 230 }, account: { y: 115, h: 15, w: 300 }, closing: { y: CLOSE_Y + 4, h: CLOSE_H - 8, w: 348 } };
const PAGE_TOP = 50;
const beamY = (t: number) => interpolate(t, [T.beamStart, T.beamEnd], [PAGE_TOP, H - 10], { ...clamp, easing: even });
const reachedAt = (y: number) => T.beamStart + ((y - PAGE_TOP) / (H - 10 - PAGE_TOP)) * (T.beamEnd - T.beamStart);

const Scan: React.FC<{ f: number }> = ({ f }) => {
  const t = f / FPS;
  const by = beamY(t);
  const beamOn = t >= T.beamStart - 0.1 && t <= T.beamEnd + 0.2;
  return (
    <>
      <div style={{ position: "absolute", left: PANE.x, top: PANE.y, width: PANE.w, height: PANE.h, background: "#d9dbe0", overflow: "hidden" }}>
        <div style={{ position: "absolute", left: (PANE.w - W * PAGE_K) / 2, top: 34, transformOrigin: "50% 0", transform: `scale(${PAGE_K}) rotate(-1.8deg)` }}>
          <div style={{ filter: "grayscale(0.55) sepia(0.25) contrast(1.06) blur(0.35px)" }}>
            <Statement figure={<span style={{ fontWeight: 700 }}>{DEC_STATEMENT.closing}</span>} selection={0} caret={null} data={DEC_STATEMENT} />
          </div>
          {/* paper grain and an uneven scanner light */}
          <svg width={W} height={H} style={{ position: "absolute", left: 0, top: 0, mixBlendMode: "multiply", opacity: 0.35 }}>
            <filter id="grain"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="3" /><feColorMatrix type="saturate" values="0" /></filter>
            <rect width={W} height={H} filter="url(#grain)" />
          </svg>
          <div style={{ position: "absolute", inset: 0, background: "linear-gradient(100deg, rgba(0,0,0,0.10), rgba(0,0,0,0) 30%, rgba(0,0,0,0) 75%, rgba(0,0,0,0.12))" }} />
          {/* fields found so far */}
          {Object.entries(ANCHOR).map(([key, a]) => {
            const p = land(interpolate(t, [reachedAt(a.y), reachedAt(a.y) + 0.25], [0, 1], clamp));
            return p > 0 ? <div key={key} style={{ position: "absolute", left: 40, top: a.y - 2, width: a.w, height: a.h + 4, border: `2px solid ${colors.steel}`, borderRadius: 3, background: "rgba(53,95,150,0.10)", opacity: p, transform: `scale(${1.15 - 0.15 * p})` }} /> : null;
          })}
          {beamOn && <div style={{ position: "absolute", left: -20, top: by, width: W + 40, height: 3, background: colors.steel, boxShadow: "0 0 22px 7px rgba(53,95,150,0.45)" }} />}
        </div>
      </div>
      <div style={{ position: "absolute", left: CARD2.x, top: CARD2.y, width: CARD2.w, height: 520, background: colors.white, border: `1px solid ${colors.line}`, borderRadius: 14, fontFamily: fonts.ui, padding: "26px 26px" , boxSizing: "border-box" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
          <svg width="18" height="20" viewBox="0 0 18 20"><path d="M3 1h8l4 4v14H3z M11 1v4h4 M6 10h6 M6 14h6" fill="none" stroke={colors.ink} strokeWidth="1.6" /></svg>
          <span style={{ fontSize: 19, fontWeight: 700, color: colors.ink }}>Extracted Information</span>
        </div>
        <div style={{ border: `1px solid ${colors.line}`, borderRadius: 8, overflow: "hidden" }}>
          <div style={{ display: "flex", height: 42, alignItems: "center", background: "#f7f8fa", font: `700 11px ${fonts.ui}`, letterSpacing: 0.88, color: colors.textMuted, textTransform: "uppercase", padding: "0 16px" }}>
            <span style={{ width: 180 }}>Field</span><span style={{ flex: 1 }}>Extracted value</span><span style={{ width: 96 }}>Confidence</span>
          </div>
          {SCAN_FIELDS.map((fld) => {
            const at = reachedAt(ANCHOR[fld.from].y) + 0.15;
            const chars = Math.round(interpolate(t, [at, at + 0.35], [0, fld.value.length], clamp));
            const pill = land(interpolate(t, [at + 0.3, at + 0.45], [0, 1], clamp));
            const live = t >= at && t < at + 0.6;
            return (
              <div key={fld.name} style={{ display: "flex", height: 52, alignItems: "center", padding: "0 16px", borderTop: `1px solid ${colors.line}`, background: live ? "rgba(53,95,150,0.07)" : "transparent" }}>
                <span style={{ width: 180, fontSize: 13, color: colors.inkSoft }}>{fld.name}</span>
                <span style={{ flex: 1, fontSize: 14, color: colors.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", paddingRight: 10 }}>{fld.value.slice(0, chars)}</span>
                <span style={{ width: 96 }}>{pill > 0 && <span style={{ display: "inline-block", font: `700 12px ${fonts.ui}`, color: "#166d5f", background: "#e1f1ec", borderRadius: 4, padding: "3px 7px", transform: `scale(${1.3 - 0.3 * pill})`, opacity: pill }}>{fld.confidence}</span>}</span>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
};

// ---- The scene --------------------------------------------------------------------------------
export const UploadScene: React.FC<{ showText?: boolean }> = ({ showText = true }) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const inScan = t >= T.scanAt;

  // A: drag, drop, cascade, click
  const drag = interpolate(t, [T.dragStart, T.dropAt], [0, 1], { ...clamp, easing: even });
  const hover = interpolate(t, [T.dropAt - 0.4, T.dropAt - 0.25, T.dropAt, T.dropAt + 0.15], [0, 1, 1, 0], clamp);
  const progress = PACKAGE.map((_, i) => land(interpolate(t, [cascadeAt(i), cascadeAt(i) + 0.22], [0, 1], clamp)));
  const fresh = PACKAGE.map((_, i) => interpolate(t, [cascadeAt(i), cascadeAt(i) + 0.5], [1, 0], clamp));
  const collapse = interpolate(t, [T.clickAt + 0.15, T.tableAt + 0.2], [1, 0], { ...clamp, easing: even });
  const toButton = interpolate(t, [T.clickAt - 0.6, T.clickAt - 0.05], [0, 1], { ...clamp, easing: even });
  const press = interpolate(t, [T.clickAt - 0.05, T.clickAt + 0.05, T.clickAt + 0.2], [0, 1, 0], clamp);
  const start = { x: 1520, y: 900 };
  const px = toButton > 0 ? interpolate(toButton, [0, 1], [DROP.x + 40, BUTTON.x]) : interpolate(drag, [0, 1], [start.x, DROP.x + 40]);
  const py = toButton > 0 ? interpolate(toButton, [0, 1], [DROP.y + 30, BUTTON.y]) : interpolate(drag, [0, 1], [start.y, DROP.y + 30]);
  const pointerOn = t >= T.dragStart && t < T.tableAt;
  const cam = camera(frame);
  const tableIn = t >= T.clickAt + 0.15;
  const scanEnter = land(interpolate(t, [T.scanAt, T.scanAt + 0.6], [0, 1], clamp));
  const scanZoom = interpolate(t, [T.scanAt, T.length], [1.0, 1.1], clamp);

  return (
    <AbsoluteFill style={{ background: colors.paper }}>
      {!inScan ? (
        <Window enter={1}>
          <Screen {...SCREENS_HOW.documents} h={1800} imgH={900} zoom={cam.zoom} focus={{ x: cam.x, y: cam.y }}>
            {/* Before the upload, the case has no documents yet: hide the captured (already processed) table. */}
            {!tableIn && <div style={{ position: "absolute", left: 100, top: 420, width: 1340, height: 1380, background: colors.paper }} />}
            {tableIn && <DocumentsCard f={frame} />}
            <div style={{ position: "absolute", left: BOX_AT.x, top: BOX_AT.y, background: colors.paper, paddingBottom: 4 }}>
              <UploadBox files={PACKAGE.map((d) => d.file)} progress={progress.map((p) => p * collapse)} fresh={fresh} u={1} width={BOX_AT.w} heading={false} />
              {hover > 0 && <div style={{ position: "absolute", left: 0, top: 0, width: BOX_AT.w, height: BOX.dropH, borderRadius: 10, border: `2px dashed ${colors.steel}`, background: `rgba(53,95,150,${0.08 * hover})`, opacity: hover }} />}
            </div>
            {pointerOn && (
              <Pointer x={px} y={py} size={24} press={press}>
                {t < T.dropAt && <div style={{ transform: `scale(${1 - 0.6 * hover * (t > T.dropAt - 0.1 ? 1 : 0)})` }}><Stack /></div>}
              </Pointer>
            )}
          </Screen>
        </Window>
      ) : (
        <Window enter={scanEnter} tilt={{ ry: (1 - scanEnter) * -14, dx: (1 - scanEnter) * -200 }}>
          <Screen {...SCREENS_HOW.scan} zoom={scanZoom} focus={{ x: 740, y: 560 }}>
            <Scan f={frame} />
          </Screen>
        </Window>
      )}

      {/* The step tracker shows in every product section, captions or not: it is how the viewer follows along. */}
      <div style={{ position: "absolute", left: 0, right: 0, top: 30, display: "flex", justifyContent: "center" }}>
        <StepTracker steps={STEPS} current={0} />
      </div>
      {showText && (
        <div style={{ position: "absolute", left: 0, right: 0, top: 104, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
          <Title text={UPLOAD_COPY.headline} enterAt={s(T.headlineIn)} exit="none" align="center" style={{ fontFamily: fonts.display, fontSize: 54, fontWeight: 700, letterSpacing: "-0.02em", color: colors.ink, lineHeight: 1.1 }} />
          <div style={{ display: "flex", gap: 12 }}>
            <Title text={UPLOAD_COPY.line1} enterAt={s(T.line1In)} exit="none" style={{ fontFamily: fonts.display, fontSize: 34, fontWeight: 600, color: colors.inkSoft, lineHeight: 1.2 }} />
            <Title text={UPLOAD_COPY.line2} enterAt={s(T.line2In)} exit="none" style={{ fontFamily: fonts.display, fontSize: 34, fontWeight: 600, color: colors.steel, lineHeight: 1.2 }} />
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
};
