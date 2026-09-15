const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(require('path').join(__dirname, '../yama-dashboard/dist/gshinrje.html'), 'utf8');
const code = html.slice(html.indexOf('  function qqqTimeBucket('), html.indexOf('  function qqqMergeLiveCandle('));
const context = vm.createContext({Intl, Date, Map});
vm.runInContext(code, context);
const start = Date.parse('2026-09-15T13:30:00Z') / 1000;
const bars = Array.from({length:60}, (_,i)=>({time:start+i*60,open:100+i,high:102+i,low:99+i,close:101+i,volume:10}));
for (const [tf,count] of Object.entries({'1m':60,'5m':12,'15m':4,'1H':1,'4H':1,'1D':1,'1W':1,'1M':1})) {
  assert.equal(context.qqqAggregateCandles(bars,tf).length,count,tf);
}
const five = context.qqqAggregateCandles(bars,'5m')[0];
assert.deepEqual(JSON.parse(JSON.stringify(five)),{time:start,open:100,high:106,low:99,close:105,volume:50});
assert.equal(context.qqqAggregateCandles(bars.slice(0,7),'5m')[1].volume,20,'partial candle');
assert.equal(context.qqqAggregateCandles(bars.slice().reverse(),'5m')[0].open,100,'source order');
assert.equal(context.qqqTimeBucket(Date.parse('2026-09-15T14:29:00Z')/1000,'1H'),start,'hour starts at market open');
assert.equal(context.qqqTimeBucket(Date.parse('2026-01-15T15:29:00Z')/1000,'1H'),Date.parse('2026-01-15T14:30:00Z')/1000,'winter clock');
assert.equal(context.qqqTimeBucket(start,'1W'),Date.parse('2026-09-14T00:00:00Z')/1000,'Monday week start');
assert.equal(context.qqqTimeBucket(start,'1M'),Date.parse('2026-09-01T00:00:00Z')/1000,'calendar month');
assert.equal(context.qqqAggregateCandles([],'5m').length,0,'missing data stays empty');
const nextDay = {...bars[0],time:start+86400};
assert.equal(context.qqqAggregateCandles([bars[0],nextDay],'1D').length,2,'day boundary');
assert.equal(context.qqqAggregateCandles([bars[0],nextDay],'1W').length,1,'week grouping');
console.log('PASS: all intervals, OHLC and volume, partial candles, ordering, market-open alignment, DST, calendar boundaries, missing data');
const daily = [
  {time:Date.parse('2026-08-31T04:00:00Z')/1000,open:90,high:100,low:85,close:95,volume:1000},
  {time:Date.parse('2026-09-01T04:00:00Z')/1000,open:95,high:110,low:90,close:105,volume:2000},
];
const mixed = context.qqqChartCandles([bars[0]],daily,'1D');
assert.equal(mixed.length,3,'current minute bar joins historical daily bars');
assert.equal(mixed[0].time,Date.parse('2026-08-31T00:00:00Z')/1000,'daily date preserved');
const month = context.qqqChartCandles([bars[0]],daily,'1M');
assert.equal(month.length,2,'real calendar months');
assert.equal(month[1].open,95);
assert.equal(month[1].high,110);
assert.equal(month[1].volume,2010);
const duplicate = {...bars[0],time:Date.parse('2026-09-01T13:30:00Z')/1000};
assert.equal(context.qqqChartCandles([duplicate],daily,'1D')[1].volume,2000,'do not double-count completed daily sessions');
console.log('PASS: real daily bars, weekly/monthly aggregation, current session merging, no duplicate volume');
