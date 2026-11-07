// Runs before every unit test file (vite.config.ts): unmounts what each test rendered, as Testing Library does
// automatically only when test globals are on.
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(cleanup);
