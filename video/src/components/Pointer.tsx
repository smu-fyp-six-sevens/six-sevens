// Pointer - an arrow cursor; `press` (0..1) dips it for a click. Sized in the units of the layer it sits in.

import React from "react";
import { colors } from "../theme";

export const Pointer: React.FC<{ x: number; y: number; size?: number; press?: number; children?: React.ReactNode }> = ({ x, y, size = 26, press = 0, children }) => (
  <div style={{ position: "absolute", left: x, top: y, transform: `scale(${1 - 0.15 * press})`, transformOrigin: "0 0", zIndex: 10 }}>
    {children}
    <svg width={size} height={size * 1.4} viewBox="0 0 20 28" style={{ position: "absolute", left: 0, top: 0, filter: "drop-shadow(0 2px 3px rgba(0,0,0,0.3))" }}>
      <path d="M1 1 L1 22 L6.5 16.8 L10.4 26 L14 24.4 L10.2 15.4 L18 15.4 Z" fill={colors.ink} stroke="#fff" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  </div>
);
