import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

// Space kept between the popover and the trigger, and between the popover and the window's edges.
const GAP = 8;

// A short explanation that opens on hover or keyboard focus, for touch and keyboard users as well as mouse ones
// (a title attribute does neither, and is slow and unstyled). It's drawn on the body so a table or card that
// clips its overflow can't cut it off.
export function InfoTip({ tip, children, className }: { tip: ReactNode; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const tipRef = useRef<HTMLSpanElement>(null);
  const tipId = useId();

  // Below the trigger, centred on it and kept inside the window; above it when there's no room below.
  useLayoutEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current?.getBoundingClientRect();
    const tipBox = tipRef.current?.getBoundingClientRect();
    if (!trigger || !tipBox) return;
    const left = Math.min(
      Math.max(GAP, trigger.left + trigger.width / 2 - tipBox.width / 2),
      window.innerWidth - tipBox.width - GAP,
    );
    const below = trigger.bottom + GAP;
    const top = below + tipBox.height > window.innerHeight - GAP ? trigger.top - GAP - tipBox.height : below;
    setPosition({ top, left });
  }, [open]);

  function show() {
    setPosition(null);
    setOpen(true);
  }

  return (
    <>
      <span
        ref={triggerRef}
        className={`info-tip-trigger${className ? ` ${className}` : ''}`}
        tabIndex={0}
        aria-describedby={open ? tipId : undefined}
        onMouseEnter={show}
        onMouseLeave={() => setOpen(false)}
        onFocus={show}
        onBlur={() => setOpen(false)}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false);
        }}
      >
        {children}
      </span>
      {open &&
        createPortal(
          <span
            ref={tipRef}
            id={tipId}
            role="tooltip"
            className="info-tip"
            style={position ? { top: position.top, left: position.left } : { visibility: 'hidden' }}
          >
            {tip}
          </span>,
          document.body,
        )}
    </>
  );
}

function InfoIcon() {
  return (
    <svg className="info-tip-icon" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <circle cx="8" cy="8" r="6.75" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M8 7.25v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="8" cy="4.9" r="0.95" fill="currentColor" />
    </svg>
  );
}

// A heading or caption with an ⓘ after it that explains it.
export function InfoLabel({ label, tip }: { label: ReactNode; tip: ReactNode }) {
  return (
    <InfoTip tip={tip} className="info-label">
      {label}
      <InfoIcon />
    </InfoTip>
  );
}
