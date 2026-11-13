// Section 11 - "cta". "Bring your next loan case." The product's name, the line under it, and the button the
// cursor presses. A faint field of the case's documents drifts behind, the pile from the start, now in order.
// Times: CTA in timing.ts. Words: data/howitworks.ts.

import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { DocWindow } from "../components/DocWindow";
import { Pointer } from "../components/Pointer";
import { Title } from "../components/Title";
import { CTA_COPY as C } from "../data/howitworks";
import { COPY2, DOCS } from "../data/overwhelm";
import { colors, fonts } from "../theme";
import { CTA as T, FPS, s } from "../timing";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const land = Easing.bezier(0.2, 0.9, 0.1, 1);
const even = Easing.bezier(0.37, 0, 0.63, 1);
const pop = Easing.bezier(0.34, 1.56, 0.64, 1);

export const CtaScene: React.FC<{ showText?: boolean }> = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const btn = land(interpolate(t, [T.buttonIn, T.buttonIn + 0.5], [0, 1], { ...clamp, easing: pop }));
  const hover = interpolate(t, [T.hoverAt - 0.8, T.hoverAt], [0, 1], { ...clamp, easing: even });
  const press = interpolate(t, [T.pressAt - 0.05, T.pressAt + 0.05, T.pressAt + 0.25], [0, 1, 0], clamp);
  const ripple = interpolate(t, [T.pressAt, T.pressAt + 0.8], [0, 1], clamp);
  const drift = t * 14;
  const BTN = { x: 960, y: 760 };

  return (
    <AbsoluteFill style={{ background: colors.paper, fontFamily: fonts.ui }}>
      {/* the documents, in neat rows now, faint */}
      <AbsoluteFill style={{ opacity: 0.08, transform: `translateX(${-drift}px)` }}>
        {Array.from({ length: 24 }, (_, i) => (
          <div key={i} style={{ position: "absolute", left: (i % 8) * 270 - 60, top: Math.floor(i / 8) * 360 - 40, transformOrigin: "0 0", transform: "scale(0.5)" }}>
            <DocWindow {...DOCS[i % DOCS.length]} company={COPY2.company} width={440} height={560} />
          </div>
        ))}
      </AbsoluteFill>
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at 50% 50%, rgba(249,249,248,0.96) 30%, rgba(249,249,248,0.7) 75%)" }} />

      <div style={{ position: "absolute", left: 0, right: 0, top: 300, display: "flex", flexDirection: "column", alignItems: "center", gap: 18 }}>
        <Title text={C.lead} enterAt={s(T.leadIn)} exit="none" align="center" style={{ fontFamily: fonts.display, fontSize: 48, fontWeight: 600, color: colors.inkSoft }} />
        <Title text={C.name} enterAt={s(T.nameIn)} exit="none" align="center" style={{ fontFamily: fonts.display, fontSize: 96, fontWeight: 700, letterSpacing: "-0.03em", color: colors.ink, lineHeight: 1.05 }} />
      </div>

      {btn > 0 && (
        <div style={{ position: "absolute", left: BTN.x, top: BTN.y, transform: `translate(-50%, -50%) scale(${(0.7 + 0.3 * btn) * (1 - 0.05 * press)})` }}>
          {ripple > 0 && ripple < 1 && <div style={{ position: "absolute", inset: -10, borderRadius: 999, border: `3px solid ${colors.steel}`, opacity: 1 - ripple, transform: `scale(${1 + 0.35 * ripple})` }} />}
          <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "24px 44px", borderRadius: 999, background: colors.steel, color: colors.white, fontSize: 36, fontWeight: 700, boxShadow: `0 ${16 + 8 * hover}px ${36 + 10 * hover}px rgba(53,95,150,${0.3 + 0.15 * hover})`, whiteSpace: "nowrap", opacity: Math.min(1, btn * 1.5) }}>
            {C.button}<span style={{ transform: `translateX(${6 * hover}px)` }}>→</span>
          </div>
        </div>
      )}
      {t >= T.hoverAt - 0.8 && <Pointer x={BTN.x + 60 + 260 * (1 - hover)} y={BTN.y + 10 + 160 * (1 - hover)} size={30} press={press} />}
    </AbsoluteFill>
  );
};
