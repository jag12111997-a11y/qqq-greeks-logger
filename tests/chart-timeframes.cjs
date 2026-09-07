const path = require('path');
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync(path.join(__dirname, '../yama-dashboard/dist/gshinrje.html'),'utf8');
for (const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(m[1]);
const start=html.indexOf('  function qqqAggregateCandles('), end=html.indexOf('  function mountQqqChart(',start);
const ctx={Intl,Date}; vm.createContext(ctx); vm.runInContext(html.slice(start,end),ctx);
const bars=Array.from({length:120},(_,i)=>({time:1788528600+i*60,open:100+i,high:102+i,low:99+i,close:101+i,volume:10}));
for(const [tf,n] of [['1m',120],['5m',24],['15m',8],['1H',3],['4H',1]]) {
 const out=ctx.qqqAggregateCandles(bars,tf); assert.equal(out.length,n,tf); assert.equal(out.reduce((n,b)=>n+b.volume,0),1200);
 assert.equal(out[0].open,100); assert.equal(out.at(-1).close,220);
}
const daily=['2026-08-28','2026-08-31','2026-09-01'].map((time,i)=>({time,open:100+i,high:110+i,low:90+i,close:105+i,volume:10}));
assert.equal(ctx.qqqAggregateCandles(daily,'1D').length,3);
assert.equal(ctx.qqqAggregateCandles(daily,'1W').length,2);
assert.equal(ctx.qqqAggregateCandles(daily,'1M').length,2);
assert.equal(ctx.qqqAggregateCandles([],'1m').length,0);
assert(html.includes('../../market-dash/auction_live.json'));
console.log('PASS: script syntax, all 8 timeframes, OHLC and volume preservation, empty feed, data path');
