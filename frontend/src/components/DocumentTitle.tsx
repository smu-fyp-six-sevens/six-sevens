import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { pageTitle } from '../utils/pageTitle';

// Keeps the browser tab's title in step with the page.
export default function DocumentTitle() {
  const { pathname, search } = useLocation();
  useEffect(() => {
    document.title = pageTitle(pathname, search);
  }, [pathname, search]);
  return null;
}
