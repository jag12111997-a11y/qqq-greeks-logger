import csv,json,collections,datetime as dt
from zoneinfo import ZoneInfo
import sys,glob
# usage: python3 market-dash/my_trades_from_csv.py "<your 'trading performance' folder>/"   (run from the repo root)
B=sys.argv[1] if len(sys.argv)>1 else 'trading performance/'
files=sorted(f for f in glob.glob(B+'*/*.csv') if '/archive/' not in f)
legs=collections.defaultdict(list)
for f in files:
  for r in csv.DictReader(open(f)):
    if r['Status']!='Filled': continue
    s=r['Filled Time'].strip(); stamp,tz=s.rsplit(' ',1)
    t=dt.datetime.strptime(stamp,'%m/%d/%Y %H:%M:%S')
    q=int(float(r['Filled'])); px=float(r['Avg Price'].lstrip('@'))
    for _ in range(q): legs[r['Symbol']].append((t,r['Side'],px))
cards=collections.OrderedDict(); skipped=collections.Counter()
for sym,L in legs.items():
  i=0
  while i<len(sym) and not sym[i].isdigit(): i+=1
  under=sym[:i]; rest=sym[i:]; exp=dt.datetime.strptime(rest[:6],'%y%m%d').date(); side='call' if rest[6]=='C' else 'put'; K=int(rest[7:])/1000
  L.sort(key=lambda x:(x[0],0 if x[1]=='Buy' else 1)); q=[]; trips=[]
  for t,sd,px in L:
    if sd=='Buy': q.append((t,px))
    elif q: bt,bp=q.pop(0); trips.append((bt,bp,t,px,'sold'))
  for bt,bp in q:
    if exp < dt.date.today(): trips.append((bt,bp,dt.datetime.combine(exp,dt.time(16,0)),0.0,'expired'))
  for bt,bp,xt,xp,why in trips:
    if under!='QQQ': skipped[under]+=1; continue
    m=lambda t:(t.hour*60+t.minute)-570+(t.second/60)
    key=(sym,bt.strftime('%Y-%m-%d %H:%M'),xt.strftime('%H:%M'),bp,xp)
    c=cards.get(key)
    if c: c['ct']+=1; c['pnl']=round(c['pnl']+(xp-bp)*100,2); continue
    cards[key]=dict(date=bt.date().isoformat(),side=side,strike=K,m=round(m(bt),2),exit_m=round(min(m(xt),390),2) if xt.date()==bt.date() else 390,
      paid=bp,sold=xp,why=why,ct=1,per=round((xp-bp)*100,2),pnl=round((xp-bp)*100,2),exp=exp.isoformat(),tin=bt.strftime('%H:%M:%S'),tout=xt.strftime('%H:%M:%S'))
out=sorted(cards.values(),key=lambda c:(c['date'],c['m']))
json.dump(out,open('market-dash/my_trades_list.json','w'),indent=0)
print(len(out),'cards',sum(c['ct'] for c in out),'lots','skipped',dict(skipped),'pnl',round(sum(c['pnl'] for c in out),2))
print(collections.Counter(c['date'][:7] for c in out))
print('not 0DTE:',sum(1 for c in out if c['exp']!=c['date']))
