/* Test: Fulfillment Records Log — grouping, natural sort, group ordering,
   the Date-in range rule, the Mixed warranty rule, and the expand/collapse
   "moves" column rule. Extracts the real functions from ac-stock-tracker.html
   so the test exercises shipped code. */
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

function extractConst(name) {
  const start = html.indexOf('const ' + name + ' =');
  if (start < 0) throw new Error(name + ' not found in html');
  let i = html.indexOf('[', start), depth = 0;
  for (; i < html.length; i++) {
    if (html[i] === '[') depth++;
    else if (html[i] === ']') { depth--; if (!depth) break; }
  }
  return html.slice(start, i + 2);
}

// Dependencies of the functions under test — eval them all.
// (const/let don't escape eval scope, so extract as var.)
eval(extractConst('TL_MONTHS').replace('const ', 'var '));
eval(extractConst('TL_COLS').replace('const ', 'var '));
eval(extractFn('escapeHtml'));
eval(extractFn('parseDateAny'));
eval(extractFn('parseDateTimeAny'));
eval(extractFn('isTrackBooked'));
eval(extractFn('naturalCompareSerials'));
eval(extractFn('buildTrackGroups'));
eval(extractFn('tlFmtDate'));
eval(extractFn('tlFmtDateTime'));
eval(extractFn('tlDateInLabel'));
eval(extractFn('tlWarrantyLabel'));
eval(extractFn('tlWarrantyCls'));
eval(extractFn('tlWarrantyCell'));
eval(extractFn('tlCellMovesToUnits'));

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log('  ok  ' + name); }
  else { fail++; console.log('FAIL  ' + name); }
}

const PAST = '2020-03-01';      // -> Dispatched
const FUTURE = '2999-03-01';    // -> Booked
const rec = (model, serial, customer, dateOut, dateIn, warranty, batch) => ({
  model, serial, customer, dateOut,
  dateIn: dateIn || '2025-01-01',
  batch: batch || '',
  warranty: warranty || { status: 'active', daysRemaining: 366, expiry: '2026-03-01' },
});

/* ---- grouping: model + destination + dispatched date + status ---- */
const list = [
  rec('BRC2E61', 'BRC2E61 #10', 'John', PAST),
  rec('BRC2E61', 'BRC2E61 #2',  'John', PAST),
  rec('BRC2E61', 'BRC2E61 #1',  'John', PAST),
  rec('BRC2E61', 'BRC2E61 #11', 'John', PAST),
  rec('BYCQ125EAF', 'S-1',      'John', PAST),           // different model
  rec('BRC2E61', 'BRC2E61 #3',  'Mary', PAST),           // different destination
  rec('BRC2E61', 'BRC2E61 #4',  'John', '2020-03-02'),   // different date
  rec('BRC2E61', 'BRC2E61 #5',  'John', FUTURE),         // different status (Booked)
];
const groups = buildTrackGroups(list, 'desc');

check('groups by model+destination+date+status -> 5 groups', groups.length === 5);

/* ---- a second checkout of the same model on the same day must NOT merge ---- */
const twoSales = [
  rec('BRC2E61', 'X #1', 'John', PAST, null, null, 'SALE-20200301-090000-AAA11'),
  rec('BRC2E61', 'X #2', 'John', PAST, null, null, 'SALE-20200301-090000-AAA11'),
  rec('BRC2E61', 'Y #1', 'John', PAST, null, null, 'SALE-20200301-120000-BBB22'),
];
const ts = buildTrackGroups(twoSales, 'desc');
check('same model+dest+date, two batches -> 2 groups', ts.length === 2);
check('same-day: newer checkout (later batch time) first',
  ts[0].batch === 'SALE-20200301-120000-BBB22');

/* ---- multi-model checkout: siblings stay adjacent, ordered by model ---- */
const linked = [
  rec('AAA', 'a1', 'GCNP', '2020-05-01', null, null, 'SALE-20200501-100000-000X1'),
  rec('ZZZ', 'z1', 'GCNP', '2020-05-01', null, null, 'SALE-20200501-100000-000X1'),
  rec('MMM', 'm1', 'GCNP', '2020-05-01', null, null, 'SALE-20200501-090000-000Y2'),
];
const lg = buildTrackGroups(linked, 'desc');
check('multi-model batch: newer sale block on top', lg[0].batch.endsWith('X1') && lg[2].batch.endsWith('Y2'));
check('batch siblings render adjacent', lg[0].batch === lg[1].batch);
check('siblings ordered by model', lg[0].model === 'AAA' && lg[1].model === 'ZZZ');

/* ---- Booked (future) pins to the top regardless of sort direction ---- */
const mixBD = [rec('AAA', 's1', 'c', PAST), rec('ZZZ', 's2', 'c', FUTURE)];
check('booked first in desc', buildTrackGroups(mixBD, 'desc')[0].status === 'Booked');
check('booked first even in asc', buildTrackGroups(mixBD, 'asc')[0].status === 'Booked');

const john = groups.find(g => g.model === 'BRC2E61' && g.customer === 'John' && g.dateOut === PAST);
check('main group has 4 units', !!john && john.items.length === 4);
check('future-dated group is Booked', groups.some(g => g.dateOut === FUTURE && g.status === 'Booked'));
check('past-dated group is Dispatched', john && john.status === 'Dispatched');

/* ---- natural sort inside a group ---- */
check('natural order #1 #2 #10 #11',
  john && john.items.map(r => r.serial).join(',') === 'BRC2E61 #1,BRC2E61 #2,BRC2E61 #10,BRC2E61 #11');

check('plain serials natural-sort too',
  ['B2', 'B10', 'B1'].sort(naturalCompareSerials).join(',') === 'B1,B2,B10');

check('alpha tiebreak stable-ish (#1 before #10 before #2)',
  ['MODEL #2', 'MODEL #10', 'MODEL #1'].sort(naturalCompareSerials).join(',') === 'MODEL #1,MODEL #2,MODEL #10');

/* ---- group ordering: dispatchedAt Desc/Asc, then model ---- */
const ordered = [
  rec('ZZZ', 'a', 'X', '2020-01-01'),
  rec('AAA', 'b', 'X', '2020-01-01'),   // same date -> model sorts first
  rec('MMM', 'c', 'X', '2020-06-01'),
];
const desc = buildTrackGroups(ordered, 'desc');
check('desc: newest date first', desc[0].dateOut === '2020-06-01');
check('desc: same-date tiebreak by model', desc[1].model === 'AAA' && desc[2].model === 'ZZZ');
const asc = buildTrackGroups(ordered, 'asc');
check('asc: oldest date first', asc[0].dateOut === '2020-01-01' && asc[0].model === 'AAA');
check('asc: newest last', asc[asc.length - 1].dateOut === '2020-06-01');

/* ---- Date in range rule (calendar day only, never 'Various') ---- */
const sameDay = [rec('M', 's1', 'c', PAST, '2025-10-05'), rec('M', 's2', 'c', PAST, '2025-10-05T14:30')];
check('same calendar day -> single date', tlDateInLabel(sameDay) === 'Oct 5, 2025');
const diffDay = [rec('M', 's1', 'c', PAST, '2025-10-05'), rec('M', 's2', 'c', PAST, '2025-10-07')];
check('different days -> range', tlDateInLabel(diffDay) === 'Oct 5, 2025 \u2013 Oct 7, 2025');
check('never Various', tlDateInLabel(diffDay).indexOf('Various') < 0);

/* ---- warranty: shared value or Mixed ---- */
check('shared warranty -> Active · N days',
  tlWarrantyCell([rec('M','a','c',PAST), rec('M','b','c',PAST)]).includes('Active \u00b7 366 days'));
check('expired -> Expired label',
  tlWarrantyCell([rec('M','a','c',PAST,'2025-01-01',{status:'expired',daysRemaining:0,expiry:'2024-01-01'})]).includes('Expired'));
const mixed = tlWarrantyCell([
  rec('M','a','c',PAST,'2025-01-01',{status:'active',daysRemaining:366,expiry:''}),
  rec('M','b','c',PAST,'2025-01-01',{status:'active',daysRemaining:100,expiry:''}),
]);
check('differing units -> Mixed (amber)', mixed.includes('Mixed') && mixed.includes('w-mixed'));

/* ---- expand/collapse: the four "moves" values leave the parent row ---- */
const MOVES = ['customer', 'dateIn', 'dateOut', 'warranty'];
check('moves columns blank on parent when open',
  MOVES.every(k => tlCellMovesToUnits(k, true)));
check('moves columns render on parent when closed',
  MOVES.every(k => !tlCellMovesToUnits(k, false)));
check('model/qty/status never move',
  ['model', 'qty', 'status'].every(k => !tlCellMovesToUnits(k, true) && !tlCellMovesToUnits(k, false)));

/* ---- booking time: Booked until the planned date+time passes ---- */
const pad2 = n => String(n).padStart(2, '0');
const isoD = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const isoDT = d => `${isoD(d)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
const laterToday = isoDT(new Date(Date.now() + 2 * 3600e3));
const earlierToday = isoDT(new Date(Date.now() - 2 * 3600e3));
const tomorrow = isoD(new Date(Date.now() + 24 * 3600e3));

check('future datetime -> Booked', isTrackBooked(rec('M', 's', 'c', laterToday)));
check('past datetime same day -> Dispatched', !isTrackBooked(rec('M', 's', 'c', earlierToday)));
check('date-only future (legacy) -> Booked', isTrackBooked(rec('M', 's', 'c', tomorrow)));
check('date-only today -> Dispatched', !isTrackBooked(rec('M', 's', 'c', isoD(new Date()))));

/* ---- same-day bookings order by their planned delivery time ---- */
const timed = [
  rec('AAA', 'a', 'c', `${tomorrow} 09:00`),
  rec('BBB', 'b', 'c', `${tomorrow} 07:00`),
];
const tDesc = buildTrackGroups(timed, 'desc');
check('timed bookings: later planned time first in desc',
  tDesc[0].model === 'AAA' && tDesc[0].status === 'Booked');
check('timed bookings: earlier planned time first in asc',
  buildTrackGroups(timed, 'asc')[0].model === 'BBB');

/* ---- dispatched cells show 'Oct 8, 2026 · 07:00' when a time is stored ---- */
check('datetime formats with time', tlFmtDateTime('2026-10-08 07:00') === 'Oct 8, 2026 \u00b7 07:00');
check('datetime formats T-separator', tlFmtDateTime('2026-10-08T07:00') === 'Oct 8, 2026 \u00b7 07:00');
check('date-only stays date-only', tlFmtDateTime('2026-10-08') === 'Oct 8, 2026');

/* ---- empty input ---- */
check('empty list -> no groups', buildTrackGroups([], 'desc').length === 0);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
