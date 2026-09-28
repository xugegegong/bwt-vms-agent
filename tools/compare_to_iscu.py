#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号清单 ↔ iscu 完整对照 (XT GLORY)
摸清翻译规则:AMS alias=reg+'00';BTP Excel"第N位"→iscu .NN(N+1);LOAD reg=39999+modbus_addr
输出:每通道 iscu vs xlsx 点数、alias 匹配、需补/多余/描述差异。
"""
import csv, re
import openpyxl

XLSX_DIR = "/sessions/compassionate-awesome-dirac/mnt/bwt-inland-feature/新船信号清单样例_XTGLORY"
ISCU_CSV = "/sessions/compassionate-awesome-dirac/mnt/outputs/iscu_hldkeymap.csv"

def load_iscu():
    by_topic, by_key = {}, {}
    with open(ISCU_CSV) as f:
        for r in csv.DictReader(f):
            try:
                ok = int(r["orderkey"])
            except:
                continue
            item = {"orderkey": ok, "key": r["keyvalue"], "desc": r["desc"], "topic": r["topic"]}
            by_key[item["key"]] = item
            by_topic.setdefault(r["topic"], []).append(item)
    return by_topic, by_key

def load_ams(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    pts = []
    for sheet in ("FLOAT", "INT"):
        ws = wb[sheet]
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0 or row[1] is None: continue
            try: reg = int(str(row[5]).strip())
            except: continue
            pts.append({"alias": f"{reg}00", "reg": reg, "cn": str(row[3] or "").strip(),
                        "type": str(row[4] or "").strip(), "ext": str(row[6] or "").strip(),
                        "sheet": sheet})
    wb.close(); return pts

def load_btp(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Sheet1"]; pts = []
    for row in ws.iter_rows(values_only=True):
        if row[0] is None or not isinstance(row[0], (int, float)): continue
        m = re.match(r"(\d+)\s*第\s*(\d+)\s*位", str(row[2] or ""))
        if not m: continue
        reg, bit = int(m.group(1)), int(m.group(2))
        pts.append({"alias": f"{reg}{bit+1:02d}", "reg": reg, "bit": bit,
                    "name": str(row[1]).strip(), "cn": str(row[1]).strip()})
    wb.close(); return pts

def load_load(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["LCS →CCS iship-E"]; pts = []
    for row in ws.iter_rows(values_only=True):
        if not row or str(row[0]).strip() != "LCS": continue
        try: addr = int(str(row[8]).strip())
        except: continue
        pts.append({"alias": f"{39999+addr}00", "reg": 39999+addr,
                    "cn": str(row[2]).strip(), "unit": str(row[6] or "").strip()})
    wb.close(); return pts

def diff(topic, pts, iscu_topic, by_key, label):
    iscu_pts = iscu_topic.get(topic, [])
    iscu_local = {p["key"]: p for p in iscu_pts}   # topic 内局部(避免跨通道 alias 撞车)
    iscu_keys = set(iscu_local)
    xl_keys = {p["alias"] for p in pts}
    matched = xl_keys & iscu_keys
    to_add = xl_keys - iscu_keys
    extra = iscu_keys - xl_keys
    print(f"\n===== {label} [{topic}] =====")
    print(f"  iscu={len(iscu_pts)}  xlsx={len(pts)}  匹配={len(matched)}  xlsx缺={len(to_add)}  isCU多余={len(extra)}")
    if to_add:
        print(f"  -- xlsx 有但 iscu 无(需补,前5):")
        for k in sorted(to_add)[:5]:
            p = next(x for x in pts if x["alias"] == k)
            print(f"     alias={k} | {p['cn'][:40]}")
    if extra:
        print(f"  -- iscu 有但 xlsx 无(多余,前5):")
        for k in sorted(extra)[:5]:
            p = iscu_local[k]
            print(f"     alias={k} ok={p['orderkey']} | {p['desc'][:40]}")
    # 同 alias desc 不一致(用 topic 内局部)
    diffs = []
    for p in pts:
        if p["alias"] in iscu_local and iscu_local[p["alias"]]["desc"] != p["cn"]:
            diffs.append((p["alias"], p["cn"], iscu_local[p["alias"]]["desc"]))
    print(f"  -- 描述不一致 {len(diffs)} 条,前3:")
    for alias, x, i in diffs[:3]:
        print(f"     {alias}\n       xlsx='{x[:50]}'\n       iscu='{i[:50]}'")

if __name__ == "__main__":
    iscu_topic, by_key = load_iscu()
    ams = load_ams(f"{XLSX_DIR}/AMS---SMART-TKA13000.xlsx")
    btp = load_btp(f"{XLSX_DIR}/BTP信号清单.xlsx")
    ld  = load_load(f"{XLSX_DIR}/装载机算计协议Modbus list- Loading computer -CCS iship-E-TKA2501-TKA2504-Rev0-20260613.xlsx")
    diff("AMS", ams, iscu_topic, by_key, "AMS(FLOAT+INT)")
    diff("BTP", btp, iscu_topic, by_key, "BTP")
    diff("LOAD", ld, iscu_topic, by_key, "LOAD")
