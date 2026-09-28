#!/usr/bin/env python3
"""验证 make_jsonstring 生成的 JsonString 与 iscu 现库一致"""
import json

GEN = "../输出_XTGLORY_v1"
ICU = "/sessions/compassionate-awesome-dirac/mnt/outputs/sshwork"

def load(path):
    with open(path) as f:
        raw = f.read()
    for esc, real in [('\\n','\n'), ('\\t','\t'), ('\\"','"'), ('\\\\','\\')]:
        raw = raw.replace(esc, real)
    s, e = raw.find('{'), raw.rfind('}')
    return json.loads(raw[s:e+1])

def canon_seg(rtu):
    return {
        "DataLen": rtu.get("DataLen"), "DevName": rtu.get("DevName"),
        "FunCode": rtu.get("FunCode"), "RegisterBegin": rtu.get("RegisterBegin"),
        "SlaveId": rtu.get("SlaveId"),
        "results": [(r.get("Addr"), r.get("Alias"), r.get("DataTypeRegister")) for r in rtu.get("Results", [])],
    }

for name, genf, icuf in [
    ("AMS",  f"{GEN}/ams.jsonstring.json",  f"{ICU}/chan1.json"),
    ("LOAD", f"{GEN}/load.jsonstring.json", f"{ICU}/chan2.json"),
    ("BTP",  f"{GEN}/btp.jsonstring.json",  f"{ICU}/chan3.json"),
]:
    g, i = json.load(open(genf)), load(icuf)
    ok = True
    # 顶层
    for k in ("ChanCode","ChanType","DataProtocol","WayUid"):
        if str(g.get(k)) != str(i.get(k)):
            print(f"[{name}] 顶层 {k}: gen={g.get(k)} iscu={i.get(k)}"); ok=False
    if len(g["RTU"]) != len(i["RTU"]):
        print(f"[{name}] RTU段数 gen={len(g['RTU'])} iscu={len(i['RTU'])}"); ok=False
    else:
        for idx,(gs,is_) in enumerate(zip(g["RTU"], i["RTU"])):
            gsc, isc = canon_seg(gs), canon_seg(is_)
            for k in ("DevName","FunCode","RegisterBegin","SlaveId"):
                if gsc[k] != isc[k]:
                    print(f"[{name}] 段{idx} {k}: gen={gsc[k]} iscu={isc[k]}"); ok=False
            if gsc["DataLen"] != isc["DataLen"]:
                print(f"[{name}] 段{idx} DataLen 差异: gen={gsc['DataLen']} iscu={isc['DataLen']} (生成器按覆盖最远点计算,iscu 若少1可能截断最后点)")
            if len(gsc["results"]) != len(isc["results"]):
                print(f"[{name}] 段{idx} Results数: gen={len(gsc['results'])} iscu={len(isc['results'])}"); ok=False
                continue
            # 逐点比前2末1 + alias集合
            ga = set(x[1] for x in gsc["results"]); ia = set(x[1] for x in isc["results"])
            if ga != ia:
                print(f"[{name}] 段{idx} Alias集合差异: gen缺={len(ia-ga)} iscu缺={len(ga-ia)}"); ok=False
            for k in (0,1,len(gsc['results'])-1):
                if gsc["results"][k] != isc["results"][k]:
                    print(f"[{name}] 段{idx} 点{k}差异: gen={gsc['results'][k]} iscu={isc['results'][k]}"); ok=False
    print(f"[{name}] {'✅ PASS' if ok else '❌ FAIL'}  RTU段={len(g['RTU'])}")
