#!/usr/bin/env python3
import csv, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
HERE=os.path.dirname(os.path.abspath(__file__)); OUT=os.path.join(HERE,"data500")
os.makedirs(OUT,exist_ok=True)
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
FROM,TO="2019-01-01","2026-08-26"
def money(x): return float(str(x).replace("$","").replace(",","").strip())
def one(sym):
    path=os.path.join(OUT,f"{sym}.csv")
    if os.path.exists(path): return sym,-1
    for attempt in range(3):
        url=(f"https://api.nasdaq.com/api/quote/{sym}/historical?assetclass=stocks"
             f"&fromdate={FROM}&todate={TO}&limit=99999")
        p=subprocess.run(["curl","-sSL","--max-time","60","-A",UA,url],capture_output=True,text=True)
        try: d=json.loads(p.stdout)["data"]["tradesTable"]["rows"]
        except Exception:
            time.sleep(2*(attempt+1)); continue
        rows=[]
        for r in d:
            try:
                m,dd,y=r["date"].split("/")
                rows.append({"date":f"{y}-{m}-{dd}","open":money(r["open"]),"high":money(r["high"]),
                             "low":money(r["low"]),"close":money(r["close"]),
                             "volume":float(str(r["volume"]).replace(",",""))})
            except Exception: pass
        if len(rows)<600: return sym,0
        rows.sort(key=lambda x:x["date"])
        with open(path,"w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=["date","open","high","low","close","volume"]); w.writeheader(); w.writerows(rows)
        return sym,len(rows)
    return sym,0
syms=[s.strip() for s in open(os.path.join(HERE,"universe.txt")) if s.strip()]
ok=fail=skip=0
with ThreadPoolExecutor(max_workers=6) as ex:
    for i,(s,n) in enumerate(ex.map(one,syms)):
        if n==-1: skip+=1
        elif n: ok+=1
        else: fail+=1
        if (i+1)%100==0: print(f"  {i+1}/{len(syms)}  ok={ok} cached={skip} failed={fail}",flush=True)
print(f"done: ok={ok} cached={skip} failed={fail}  files={len(os.listdir(OUT))}",flush=True)
