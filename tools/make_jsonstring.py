#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_jsonstring.py — 信号清单 → iscu 采集配置 生成器 (S1)
读取船厂原始清单(AMS FLOAT/INT、BTP、LOAD),按已验证翻译规则产出:
  1. 各通道 channelsdataprotocol.JsonString (AMS/LOAD/BTP)
  2. hldkeymap INSERT SQL(含公共导航段 -2~39)
  3. device_map.csv(orderkey/device_key/中英文/单位/类型建议,供 S2 用)
  4. 核对报告(系数/单位/1-0语义/地址连续性等"清单坑"报警)

用法:
  python3 make_jsonstring.py --xlsx_dir <目录> --out_dir <输出目录> [--ship_id 35701001]
    [--ams_start 101] [--btp_start 0] [--load_start 0]   # 0=自动连续
    [--engine_wayuid 1575876387]
规则(2026-09 对 XT GLORY 定标):
  AMS FLOAT:  reg(40001起,步进2)   → Alias=reg+'00', 类型4(float badc), DataLen2
  AMS INT:    reg(45001起,步进1)   → Alias=reg+'00', 类型1(开关量整值), DataLen1
  LOAD:       modbus_addr m        → 逻辑reg=39999+m, Alias=reg+'00', 类型4, DataLen2
  BTP:        "寄存器 第N位"       → Addr=reg.{N+1}, Alias=reg+(N+1 两位), 类型0(位点)
"""
import argparse, csv, json, os, re, sys
import openpyxl

DEFAULT_ENGINE_WAYUID = "1575876387"   # EngineData 网关
NAV_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "navigation_segment.csv")

# ---------------- 解析 ---------------- #
def parse_ams(path):
    """AMS: 返回 {'FLOAT':[...], 'INT':[...]} 每点含 reg/alias/cn/en/unit/state"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = {"FLOAT": [], "INT": []}
    for sheet in ("FLOAT", "INT"):
        ws = wb[sheet]
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0 or row[1] is None:
                continue
            try:
                reg = int(str(row[5]).strip())
            except Exception:
                continue
            cn = re.sub(r"\s+", " ", str(row[3] or "")).strip()
            en = re.sub(r"\s+", " ", str(row[2] or "")).strip()
            if sheet == "FLOAT":
                out[sheet].append({"reg": reg, "alias": f"{reg}00", "cn": cn, "en": en,
                                   "unit": str(row[6] or "").strip()})
            else:
                out[sheet].append({"reg": reg, "alias": f"{reg}00", "cn": cn, "en": en,
                                   "state": re.sub(r"\s+", " ", str(row[6] or "")).strip()})
    wb.close()
    return out

def parse_btp(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Sheet1"]
    pts = []
    for row in ws.iter_rows(values_only=True):
        if row[0] is None or not isinstance(row[0], (int, float)):
            continue
        m = re.match(r"(\d+)\s*第\s*(\d+)\s*位", str(row[2] or ""))
        if not m:
            continue
        reg, bit = int(m.group(1)), int(m.group(2))
        pts.append({"reg": reg, "bit": bit, "addr": f"{reg}.{bit+1:02d}",
                    "alias": f"{reg}{bit+1:02d}",
                    "cn": re.sub(r"\s+", " ", str(row[1] or "")).strip(),
                    "remark": str(row[3] or "").strip()})
    wb.close()
    return pts

def parse_load(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["LCS →CCS iship-E"]
    pts = []
    for row in ws.iter_rows(values_only=True):
        if not row or str(row[0]).strip() != "LCS":
            continue
        try:
            maddr = int(str(row[8]).strip())
        except Exception:
            continue
        reg = 39999 + maddr
        pts.append({"reg": reg, "alias": f"{reg}00", "cn": re.sub(r"\s+", " ", str(row[2])).strip(),
                    "unit": str(row[6] or "").strip()})
    wb.close()
    return pts

# ---------------- JsonString 组装 ---------------- #
def results_json(points, dtr, occupy, addr_is_bit=False):
    rs = []
    for p in points:
        rs.append({
            "Addr": p.get("addr", str(p["reg"])),
            "Alias": p["alias"],
            "Coefficient": "0,1,0",
            "DataLen": str(occupy),
            "DataTypeRegister": str(dtr),
        })
    return rs

def build_jsonstring(chan_code, rtu_segments):
    """rtu_segments: list of {regbegin, points, dtr, occupy, addr_is_bit, devname?, funcode?, slave?}"""
    rtus = []
    for seg in rtu_segments:
        dlen = segment_datalen(seg["points"], seg["occupy"]) if seg["points"] else 0
        rtus.append({
            "DataLen": str(dlen), "DevName": seg.get("devname", "1301"),
            "FunCode": seg.get("funcode", "3"),
            "RegisterBegin": str(seg["regbegin"]),
            "SlaveId": str(seg.get("slave", "1")),
            "Results": results_json(seg["points"], seg["dtr"], seg["occupy"], seg.get("addr_is_bit", False)),
        })
    return {"ChanCode": str(chan_code), "ChanDesc": "", "ChanType": "7",
            "DataProtocol": "2", "WayUid": DEFAULT_ENGINE_WAYUID, "RTU": rtus}

def segment_datalen(points, occupy):
    """DataLen = 覆盖[min,max+occupy)寄存器数"""
    regs = [p["reg"] for p in points]
    mn, mx = min(regs), max(regs)
    return mx + occupy - mn

# ---------------- 主流程 ---------------- #
def guess_type_from_name(cn, state, is_float):
    """建议 device_type:0报警/1数值/2开关(供 S2,需人工核对)"""
    if is_float:
        return "1"   # 数值
    s = cn
    if re.search(r"报警|故障|失电|越限|异常|漏|高温|低压|alarm|trip", s, re.I) or "ALARM" in (state or "").upper():
        return "0"
    if re.search(r"运行|指示|状态|合闸|启动|停止|run|start", s, re.I) or "RUN" in (state or "").upper():
        return "2"
    return "0"  # 默认报警量(开关量多为报警/状态)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx_dir", required=True)
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--ship_id", default="35701001")
    ap.add_argument("--ams_start", type=int, default=0, help="AMS 段 orderkey 起点,0=从101")
    ap.add_argument("--btp_start", type=int, default=0, help="BTP orderkey 起点,0=自动")
    ap.add_argument("--load_start", type=int, default=0, help="LOAD orderkey 起点,0=自动")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    files = os.listdir(a.xlsx_dir)
    def find(*keys):
        for fn in files:
            if any(k.lower() in fn.lower() for k in keys):
                return os.path.join(a.xlsx_dir, fn)
        return None
    ams_path = find("ams")
    btp_path = find("btp")
    load_path = find("装载", "loading")

    issues = []   # 核对报告
    def warn(msg):
        issues.append(msg)
        print("  [!]", msg)

    # 1. 解析
    ams = parse_ams(ams_path) if ams_path else {"FLOAT": [], "INT": []}
    btp = parse_btp(btp_path) if btp_path else []
    ld  = parse_load(load_path) if load_path else []
    print(f"解析: AMS FLOAT={len(ams['FLOAT'])} INT={len(ams['INT'])} | BTP={len(btp)} | LOAD={len(ld)}")

    # 2. 核对:寄存器连续/重复/步进
    for sheet, step in (("FLOAT", 2), ("INT", 1)):
        pts = ams[sheet]
        regs = [p["reg"] for p in pts]
        if len(set(regs)) != len(regs): warn(f"AMS {sheet} 寄存器重复!")
        aliases = [p["alias"] for p in pts]
        if len(set(aliases)) != len(aliases): warn(f"AMS {sheet} Alias 重复!")
        for i in range(len(regs) - 1):
            if regs[i + 1] - regs[i] != step:
                warn(f"AMS {sheet} 寄存器不连续(reg {regs[i]}→{regs[i+1]},期望+{step})")
        if sheet == "FLOAT":
            for p in pts:
                if not p["unit"]:
                    warn(f"AMS FLOAT reg{p['reg']} {p['cn'][:20]} 单位缺失")

    # 3. orderkey 分配
    order = []
    # 公共导航段
    with open(NAV_CSV) as f:
        nav = list(csv.DictReader(f))
    order.extend([(int(r["orderkey"]), r["keyvalue"], r["desc"], r["unit"], r["topic"]) for r in nav])
    next_ok = a.ams_start if a.ams_start else 101
    ams_order = []
    for sheet in ("FLOAT", "INT"):
        for p in ams[sheet]:
            ams_order.append((next_ok, p))
            next_ok += 1
    btp_start = a.btp_start if a.btp_start else next_ok
    next_ok = btp_start
    btp_order = [(next_ok + i, p) for i, p in enumerate(btp)] or []
    load_start = a.load_start if a.load_start else (next_ok + len(btp))
    load_order = [(load_start + i, p) for i, p in enumerate(ld)] or []

    # 4. 生成 JsonString
    ams_json = build_jsonstring(1, [
        {"regbegin": 40001, "points": ams["FLOAT"], "dtr": 4, "occupy": 2},
        {"regbegin": 45001, "points": ams["INT"], "dtr": 1, "occupy": 1},
    ])
    # 修整段 DataLen/去 SlaveId(AMS 顶层无 SlaveId,iscu 段内有;此处段级已有)
    ams_json["RTU"][0]["DataLen"] = str(segment_datalen(ams["FLOAT"], 2)) if ams["FLOAT"] else "0"
    ams_json["RTU"][1]["DataLen"] = str(segment_datalen(ams["INT"], 1)) if ams["INT"] else "0"
    load_json = None
    if ld:
        load_json = build_jsonstring(2, [{"regbegin": 40001, "points": ld, "dtr": 4, "occupy": 2}])
        load_json["RTU"][0]["DataLen"] = str(segment_datalen(ld, 2))
    btp_json = None
    if btp:
        btp_json = build_jsonstring(3, [{"regbegin": 40001, "points": btp, "dtr": 0, "occupy": 1}])
        btp_json["RTU"][0]["DataLen"] = str(segment_datalen(btp, 1))
        btp_json["RTU"][0]["SlaveId"] = "3"

    # 5. 写 JsonString 文件
    for name, obj in (("ams", ams_json), ("load", load_json), ("btp", btp_json)):
        if obj:
            p = os.path.join(a.out_dir, f"{name}.jsonstring.json")
            with open(p, "w") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            print(f"写入 {p} ({os.path.getsize(p)//1024}KB)")

    # 6. 统一收集 hldkeymap 行 + device_map
    hkeys = []     # (orderkey, alias, cn, unit, topic)
    devmap = []    # (device_key, order_id, cn, en, unit, dev_type_suggest, state)
    def add_dev(ok, p, topic, dev_key, is_float):
        unit = p.get("unit", "")
        hkeys.append((ok, p["alias"], p["cn"], unit, topic))
        state = p.get("state", "")
        dt = guess_type_from_name(p["cn"], state, is_float)
        devmap.append([dev_key, ok, p["cn"], p.get("en", ""), unit, dt, state])
    for ok, kv, desc, unit, topic in order:
        hkeys.append((ok, kv, desc, unit, topic))
    for ok, p in ams_order:
        sheet_float = p in ams["FLOAT"]
        add_dev(ok, p, "AMS", str(p["reg"]), sheet_float)
    for ok, p in btp_order:
        add_dev(ok, p, "BTP", p["alias"], False)      # 位点 device_key = alias
    for ok, p in load_order:
        add_dev(ok, p, "LOAD", p["alias"], True)       # ⚠️ LOAD device_key=Alias原样(4000100),不去尾00,避免与AMS撞名

    with open(os.path.join(a.out_dir, "hldkeymap.sql"), "w") as f:
        f.write(f"-- hldkeymap for ship {a.ship_id}  (共 {len(hkeys)} 行;幂等可重复执行)\n")
        f.write("-- 只清 AMS/BTP/LOAD 三通道点(>100),不动 EIM/公共段;REPLACE 覆盖公共段\n")
        f.write("DELETE FROM hldkeymap WHERE orderkey > 100 AND TopicName IN ('AMS','BTP','LOAD');\n")
        f.write("REPLACE INTO hldkeymap (orderkey, KeyValue, desc_cn, unit, TopicName) VALUES\n")
        f.write(",\n".join(f"  ({ok}, '{kv}', {sql_quote(cn)}, {sql_quote(unit)}, {sql_quote(tp)})"
                           for ok, kv, cn, unit, tp in hkeys))
        f.write(";\n")
    with open(os.path.join(a.out_dir, "device_map.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["device_key", "order_id", "cn", "en", "unit", "dev_type_suggest", "state_sem"])
        w.writerows(devmap)

    print(f"\nhldkeymap 行数: {len(hkeys)} (公共{len(order)} + AMS{len(ams_order)} + BTP{len(btp_order)} + LOAD{len(load_order)})")
    print(f"device_map 行数: {len(devmap)}")
    print(f"\n核对报告 {len(issues)} 条:")
    for i in issues:
        print("   -", i)

def sql_quote(s):
    s = str(s or "").replace("'", "''")
    return f"'{s}'" if s != "" else "NULL"
def csv_quote(s):
    s = str(s or "").replace("'", "''")
    return f"'{s}'" if s != "" else "NULL"

if __name__ == "__main__":
    main()
