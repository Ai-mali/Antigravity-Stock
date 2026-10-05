/* Test: Fulfillment Records Log grouping + natural serial sort.
   Extracts buildTrackGroups() and naturalCompareSerials() from
   ac-stock-tracker.html so the test exercises the real shipped code. */
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'ac-stock-tracker.html'), 'utf8');

function extractFn(name) {
  const start = html.indexOf('function ' + name + '(');
  if (start < 0) throw new Error(name + ' not found in html');
  let i = html.indexOf('{', start), depth = 0;
  for (; i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}') { depth--; if (!depth) break; }
  }
  return html.slice(start, i + 1);
}

// buildTrackGroups depends on naturalCompareSerials — eval both.
eval(extractFn('naturalCompareSerials'));
eval(extractFn('buildTrackGroups'));

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log('  ok  ' + name); }
  else { fail++; console.log('FAIL  ' + name); }
}

const rec = (model, serial, customer, dateOut, status) =>
  ({ model, serial, customer, dateOut, status, dateIn: '2025-01-01', batch: '' });

/* ---- grouping: model + destination + dispatched date + status ---- */
const list = [
  rec('BRC2E61', 'BRC2E61 #10', 'John',  '2026-03-01', 'SOLD'),
  rec('BRC2E61', 'BRC2E61 #2',  'John',  '2026-03-01', 'SOLD'),
  rec('BRC2E61', 'BRC2E61 #1',  'John',  '2026-03-01', 'SOLD'),
  rec('BRC2E61', 'BRC2E61 #11', 'John',  '2026-03-01', 'SOLD'),
  rec('BYCQ125EAF', 'S-1',      'John',  '2026-03-01', 'SOLD'),  // different model
  rec('BRC2E61', 'BRC2E61 #3',  'Mary',  '2026-03-01', 'SOLD'),  // different destination
  rec('BRC2E61', 'BRC2E61 #4',  'John',  '2026-03-02', 'SOLD'),  // different date
];
const groups = buildTrackGroups(list);

check('groups by model+destination+date+status -> 4 groups', groups.length === 4);

const john = groups.find(g => g.model === 'BRC2E61' && g.customer === 'John' && g.dateOut === '2026-03-01');
check('main group has 4 units', !!john && john.items.length === 4);

/* ---- natural sort inside a group ---- */
check('natural order #1 #2 #10 #11',
  john && john.items.map(r => r.serial).join(',') === 'BRC2E61 #1,BRC2E61 #2,BRC2E61 #10,BRC2E61 #11');

check('plain serials natural-sort too',
  ['B2', 'B10', 'B1'].sort(naturalCompareSerials).join(',') === 'B1,B2,B10');

check('alpha tiebreak stable-ish (#1 before #10 before #2)',
  ['MODEL #2', 'MODEL #10', 'MODEL #1'].sort(naturalCompareSerials).join(',') === 'MODEL #1,MODEL #2,MODEL #10');

/* ---- group ordering follows input list order ---- */
check('group insertion order preserved (first seen first)',
  groups[0].model === 'BRC2E61' && groups[0].customer === 'John');

/* ---- empty input ---- */
check('empty list -> no groups', buildTrackGroups([]).length === 0);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
