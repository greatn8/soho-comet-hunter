const {test}=require('node:test'),assert=require('node:assert/strict');
const T=require('../docs/display-time.js');
test('requested UTC example displays in Sydney with daylight saving',()=>{
 assert.equal(T.format('2026-11-01T09:45:00Z'),'1 Nov 2026, 8:45 pm AEDT');
 assert.equal(T.format('2026-11-01T20:45:00+11:00'),T.format('2026-11-01T09:45:00Z'));
});
test('Sydney calendar date rolls over correctly',()=>{
 assert.equal(T.format('2026-11-01T15:30:00Z'),'2 Nov 2026, 2:30 am AEDT');
});
test('winter and both daylight-saving transitions use the IANA timezone',()=>{
 assert.equal(T.format('2026-07-01T09:45:00Z'),'1 July 2026, 7:45 pm AEST');
 assert.equal(T.format('2026-10-03T15:59:00Z'),'4 Oct 2026, 1:59 am AEST');
 assert.equal(T.format('2026-10-03T16:00:00Z'),'4 Oct 2026, 3:00 am AEDT');
 assert.equal(T.format('2026-04-04T15:59:00Z'),'5 Apr 2026, 2:59 am AEDT');
 assert.equal(T.format('2026-04-04T16:00:00Z'),'5 Apr 2026, 2:00 am AEST');
});
test('C3 filenames are parsed as UTC for both source resolutions',()=>{
 for(const n of [512,1024])assert.equal(T.frame('20261101_0945_c3_'+n+'.jpg'),'1 Nov 2026, 8:45 pm AEDT');
 assert.equal(T.frame('20261101_0945'),'1 Nov 2026, 8:45 pm AEDT');
 assert.equal(T.frame('20260230_1200_c3_1024.jpg'),'—');
 assert.equal(T.frame('bad filename'),'—');
});
test('display formatting leaves raw source timestamps and report exports in UTC',()=>{
 const source={time_utc:'2026-11-01T09:45:00Z'};const before=JSON.stringify(source);
 T.format(source.time_utc);assert.equal(JSON.stringify(source),before);
 assert.equal(T.utc(source.time_utc),'2026-11-01T09:45:00.000Z');
});
test('missing and malformed display values are handled without inventing a date',()=>{
 assert.equal(T.format(null),'—');assert.equal(T.format(''),'—');
 assert.equal(T.format('not a timestamp'),'not a timestamp');
});
