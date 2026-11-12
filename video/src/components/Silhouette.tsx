// Silhouette - a profile-picture person icon (head and shoulders in a circle), drawn here so each
// role gets a little personality: the officer wears glasses and a tie and leans toward the screen;
// the customer holds a phone to their ear, fidgets and sweats. `t` is seconds, for the small loops.

import React from "react";
import { colors } from "../theme";

const HEAD = { cx: 100, cy: 78, r: 40 };
// Shoulders with the little gap under the chin, like a stock avatar icon.
const BODY = "M18 214 C18 158 48 132 78 128 Q100 148 122 128 C152 132 182 158 182 214 Z";

export const Silhouette: React.FC<{
  role: "officer" | "customer" | "badActor"; size: number; tint: string; bg: string; t: number; fidget: number;
  /** The customer after hearing back: still, smiling, no sweat. */
  happy?: boolean;
}> = ({ role, size, tint, bg, t, fidget, happy = false }) => {
  const officer = role === "officer";
  const bad = role === "badActor";
  // Officer: a slow nod toward the screen. Customer: a restless sway. Bad actor: leans in, snickering.
  const tilt = officer
    ? 3 + 1.5 * Math.sin((t * 2 * Math.PI) / 4)
    : bad
      ? 7 + 1.2 * Math.sin((t * 2 * Math.PI) / 0.45)
      : happy ? 1.5 * Math.sin((t * 2 * Math.PI) / 3) : 4 * Math.sin((t * 2 * Math.PI) / fidget);
  // Officer: a glint crosses the glasses every 2.4 s. Customer: a sweat drop slides every 2 s.
  const glint = ((t % 2.4) / 0.5);
  const drop = (t % 2) / 2;
  return (
    <svg width={size} height={size} viewBox="0 0 200 200" style={{ overflow: "visible" }}>
      <defs>
        <clipPath id={`clip-${role}`}><circle cx="100" cy="100" r="100" /></clipPath>
        <clipPath id={`lens-${role}`}>
          <circle cx="84" cy="78" r="11" /><circle cx="116" cy="78" r="11" />
        </clipPath>
      </defs>
      <circle cx="100" cy="100" r="100" fill={bg} />
      <g clipPath={`url(#clip-${role})`}>
        <g transform={`rotate(${tilt} 100 200)`}>
          <path d={BODY} fill={colors.ink} />
          {officer && (
            <>
              {/* collar and tie */}
              <path d="M84 132 L100 150 L92 160 Z M116 132 L100 150 L108 160 Z" fill={bg} />
              <path d="M96 150 L104 150 L106 158 L101 196 L99 196 L94 158 Z" fill={tint} />
            </>
          )}
          {bad && (
            /* tail, curling up from behind the shoulder, with an arrowhead */
            <g fill="none" stroke={tint} strokeWidth="6" strokeLinecap="round">
              <path d="M160 186 C 190 170, 176 140, 194 128" />
            </g>
          )}
          {bad && <path d="M194 116 L203 132 L186 133 Z" fill={tint} />}
          {bad && (
            /* horns */
            <g fill={tint}>
              <path d="M70 52 C 62 36, 62 24, 70 12 C 72 28, 80 38, 88 44 Z" />
              <path d="M130 52 C 138 36, 138 24, 130 12 C 128 28, 120 38, 112 44 Z" />
            </g>
          )}
          <circle {...HEAD} fill={colors.ink} />
          {bad ? (
            /* narrowed, scheming eyes and a sly grin */
            <g fill="none" stroke={bg} strokeWidth="4.5" strokeLinecap="round">
              <path d="M76 70 L92 76" />
              <path d="M124 70 L108 76" />
              <path d="M82 94 Q100 108 120 92" />
            </g>
          ) : officer ? (
            <>
              {/* glasses */}
              <g fill="none" stroke={bg} strokeWidth="4">
                <circle cx="84" cy="78" r="11" /><circle cx="116" cy="78" r="11" />
                <path d="M95 77 Q100 73 105 77" />
              </g>
              <g clipPath={`url(#lens-${role})`}>
                {glint <= 1 && (
                  <rect x={60 + glint * 80} y="60" width="8" height="40" fill="#ffffff" opacity="0.85" transform={`rotate(25 ${64 + glint * 80} 80)`} />
                )}
              </g>
            </>
          ) : (
            <>
              {/* phone to the ear, with the hand that holds it */}
              <rect x="130" y="62" width="18" height="38" rx="5" fill={tint} stroke={bg} strokeWidth="3" transform="rotate(14 139 81)" />
              <circle cx="142" cy="104" r="11" fill={colors.ink} stroke={bg} strokeWidth="3" />
              {happy && <path d="M84 92 Q100 108 118 92" fill="none" stroke={bg} strokeWidth="4.5" strokeLinecap="round" />}
              {/* sweat drop */}
              {!happy && <path
                d="M0 -9 C4 -3 6 1 6 4 A6 6 0 0 1 -6 4 C-6 1 -4 -3 0 -9 Z"
                fill="#7fb0e6"
                opacity={drop < 0.8 ? 1 : 1 - (drop - 0.8) / 0.2}
                transform={`translate(70 ${58 + drop * 22})`}
              />}
            </>
          )}
        </g>
      </g>
    </svg>
  );
};
