// Usage: node check_results.cjs <suite output dir>... ; fails unless every suite recorded only passes.
const fs = require('node:fs');
const path = require('node:path');

let failed = false;
for (const directory of process.argv.slice(2)) {
  const file = path.join(directory, 'results.json');
  let problem;
  if (!fs.existsSync(file)) problem = 'no results file; the suite did not finish';
  else {
    const results = JSON.parse(fs.readFileSync(file, 'utf8'));
    const failures = results.filter(r => r.status !== 'passed').map(r => r.name);
    if (!results.length) problem = 'zero scenarios recorded';
    else if (failures.length) problem = 'failed: ' + failures.join(', ');
    else console.log(`${directory}: ${results.length} scenario(s) passed`);
  }
  if (problem) { console.error(`${directory}: ${problem}`); failed = true; }
}
if (process.argv.length <= 2) { console.error('No suite directories given'); failed = true; }
process.exitCode = failed ? 1 : 0;
