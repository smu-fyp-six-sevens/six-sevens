// CI's npm audit (ci-cd.yml): fails on any high or critical advisory, except the ones in audit-allowlist.json, each
// with a reason and a review date. An entry past its date fails too, so an exception can't be forgotten.
// Run from frontend/: node scripts/npm-audit.mjs
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const BLOCKING = new Set(['high', 'critical']);
const allowlist = JSON.parse(readFileSync(new URL('../audit-allowlist.json', import.meta.url), 'utf8')).advisories;
const today = new Date().toISOString().slice(0, 10);

let report;
try {
  report = execFileSync('npm', ['audit', '--json'], { encoding: 'utf8', shell: process.platform === 'win32' });
} catch (error) {
  report = error.stdout; // npm audit exits non-zero when it finds anything
}
const { vulnerabilities = {} } = JSON.parse(report);

// The advisories themselves (a "via" object); a "via" string is a package that only depends on one.
const advisories = new Map();
for (const vulnerability of Object.values(vulnerabilities)) {
  for (const via of vulnerability.via) {
    if (typeof via === 'object' && BLOCKING.has(via.severity)) {
      const id = via.url.split('/').pop();
      advisories.set(id, `${via.name} ${via.range}: ${via.title} (${via.url})`);
    }
  }
}

let failed = false;
for (const [id, text] of advisories) {
  const allowed = allowlist.find((entry) => entry.id === id);
  if (!allowed) {
    console.error(`::error::${text}`);
    failed = true;
  } else if (allowed.review_by < today) {
    console.error(`::error::${id} was allowed until ${allowed.review_by}; check for a fix, then renew or remove it`);
    failed = true;
  } else {
    console.log(`Allowed until ${allowed.review_by}: ${text}. ${allowed.reason}`);
  }
}
console.log(`${advisories.size} high or critical ${advisories.size === 1 ? 'advisory' : 'advisories'}`);
process.exit(failed ? 1 : 0);
