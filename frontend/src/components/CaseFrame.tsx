import type { ReactNode } from 'react';

// The frame of a case's pages: the section menu runs down the left from under the top bar, and the page's own header
// (breadcrumbs over the title) and content share the column beside it. The menu is full width on the case page and
// collapsed to its icons on a document page, so the case's sections stay one click away.
export default function CaseFrame({
  nav,
  collapsed = false,
  children,
}: {
  nav: ReactNode;
  collapsed?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={`case-frame${collapsed ? ' case-frame--collapsed' : ''}`}>
      <div className="case-frame-nav">{nav}</div>
      <div className="case-frame-main">{children}</div>
    </div>
  );
}
