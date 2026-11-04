import type { ReactNode } from 'react';

// The frame of a case's pages: the section menu runs down the left from under the top bar as a rail of icons, and the
// page's own header (breadcrumbs over the title) and content share the column beside it. The rail has a fixed width;
// opening the menu lays it over the page, so the content never shifts.
export default function CaseFrame({
  nav,
  children,
}: {
  nav: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="case-frame">
      <div className="case-frame-nav">{nav}</div>
      <div className="case-frame-main">{children}</div>
    </div>
  );
}
