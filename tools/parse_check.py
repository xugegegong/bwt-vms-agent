#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号清单解析 + 与 iscu 现库对照 (XT GLORY)
用途:摸清"船厂原始清单 → iscu 采集配置"翻译规则,为 make_jsonstring 生成器定标。
对照基准:iscu_hldkeymap.csv (orderkey/keyvalue/desc/unit/topic)
清单: 新船信号清单样例_XTGLORY/ 下的 AMS/BTP/LOAD xlsx
"""
import csv, re, sys
import openpyxl

XLSX_DIR = "/sessions/compassionate-awesome-dirac/mnt/bwt-inland-feature/新船信号清单样例_XTGLORY"
ISCU_CSV = "/sessions/compassionate-awesome-dirac/mnt/outputs/iscu_hldkeymap.csv"

# ---------- 读 iscu ----------
def load_iscu():
    rows = {}
    with open(ISCU_CSV) as f:
        for r in csv.DictReader(f):
            try:
                ok = int(r["orderkey"])
            except:
                continue
            rows[ok] = {"orderkey": ok, "key": r["keyvalue"], "desc": r["desc"],
                        "unit": r["unit"], "topic": r["topic"]}
    return rows

# ---------- 读 AMS (FLOAT/INT sheet) ----------
def load_ams(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = {"FLOAT": [], "INT": []}
    for sheet in ("FLOAT", "INT"):
        ws = wb[sheet]
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0 or row[1] is None:
                continue
            # 列: TagName(1), EN(2), CN(3), 类型(4), 寄存器(5), 单位/数据(6)
            try:
                reg = int(str(row[5]).strip())
            except:
                continue
            out[sheet].append({
                "seq": len(out[sheet]) + 1, "tag": str(row[1]).strip(),
                "en": str(row[2] or "").strip(), "cn": str(row[3] or "").strip(),
                "type": str(row[4] or "").strip(), "reg": reg,
                "ext": str(row[6] or "").strip(),
            })
    wb.close()
    return out

# ---------- 读 BTP ----------
def load_btp(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Sheet1"]
    out = []
    for row in ws.iter_rows(values_only=True):
        if row[0] is None or not isinstance(row[0], (int, float)):
            continue
        addr = str(row[2] or "")
        m = re.match(r"(\d+)\s*第(\d+)位", addr)
        out.append({"seq": int(row[0]), "name": str(row[1]).strip(),
                    "reg": int(m.group(1)) if m else None,
                    "bit": int(m.group(2)) if m else None, "raw_addr": addr,
                    "remark": str(row[3] or "").strip()})
    wb.close()
    return out

# ---------- 读 LOAD (LCS→CCS sheet 数据行) ----------
def load_load(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["LCS →CCS iship-E"]
    out = []
    for row in ws.iter_rows(values_only=True):
        if row and str(row[0]).strip() in ("LCS",):
            # 列: Package(0) PLC(1) Service(2) IO(3) Min(4) Max(5) Unit(6) Scaling(7) ModbusAddr(8) DataType(9)
            try:
                addr = int(str(row[8]).strip())
            except:
                addr = None
            out.append({"name": str(row[2]).strip(), "min": row[4], "max": row[5],
                        "unit": str(row[6] or "").strip(), "scaling": row[7],
                        "modbus_addr": addr, "data_type": str(row[9] or "").strip()})
    wb.close()
    return out

# ---------- 预测 iscu 的 alias/device_key ----------
def predict_alias(reg):
    """iscu Alias 规则:寄存器逻辑地址尾接 00 (BTP 位点 .xx 例外,单独处理)"""
    return f"{reg}00"

# ---------- main ----------
if __name__ == "__main__":
    iscu = load_iscu()
    print(f"iscu hldkeymap 总数: {len(iscu)}\n")

    # AMS
    ams = load_ams(f"{XLSX_DIR}/AMS---SMART-TKA13000.xlsx")
    for sheet in ("FLOAT", "INT"):
        rows = ams[sheet]
        print(f"===== AMS {sheet}: {len(rows)} 行 =====")
        # iscu 中 AMS 段对应 orderkey 范围
        # 抽样前3 + 末1,预测 alias,到 iscu 找
        for r in rows[:3] + rows[-1:]:
            alias = predict_alias(r["reg"])
            hit = [v for v in iscu.values() if v["key"] == alias]
            print(f"  reg={r['reg']} alias?={alias} | {r['cn'][:40]} | ext={r['ext'][:25]}")
            if hit:
                h = hit[0]
                print(f"      → iscu orderkey={h['orderkey']} desc='{h['desc'][:40]}'")
            else:
                print(f"      → iscu 无 alias={alias}")
        # 首尾 reg 检查连续
        regs = [r["reg"] for r in rows]
        print(f"  reg 范围 {regs[0]}~{regs[-1]}, 相邻差 {sorted(set(regs[i+1]-regs[i] for i in range(len(regs)-1)))[:5]}")

    # BTP
    btp = load_btp(f"{XLSX_DIR}/BTP信号清单.xlsx")
    print(f"\n===== BTP: {len(btp)} 行 =====")
    for r in btp[:3] + btp[-1:]:
        alias = f"{r['reg']}{r['bit']:02d}"
        hit = [v for v in iscu.values() if v["key"] == alias]
        print(f"  {r['reg']}.{r['bit']:02d} alias={alias} | {r['name']}")
        if hit:
            print(f"      → iscu orderkey={hit[0]['orderkey']} desc='{hit[0]['desc'][:30]}'")

    # LOAD
    ld = load_load(f"{XLSX_DIR}/装载机算计协议Modbus list- Loading computer -CCS iship-E-TKA2501-TKA2504-Rev0-20260613.xlsx")
    print(f"\n===== LOAD(LCS→CCS): {len(ld)} 行 =====")
    for r in ld:
        # 假设 iscu LOAD 从 40001 起每点 +2:预测 addr 对应 iscu 40001 + (modbus_addr-2)
        if r["modbus_addr"] is not None:
            guess_reg = 39999 + r["modbus_addr"]
            alias = predict_alias(guess_reg)
            hit = [v for v in iscu.values() if v["key"] == alias]
            mark = hit[0]["orderkey"] if hit else "?"
            print(f"  maddr={r['modbus_addr']} → reg?={guess_reg} alias?={alias} | {r['name'][:30]}({r['unit']}) → iscu ok={mark}")

    # 总体行数对照
    print(f"\n===== 行数对照 =====")
    print(f"AMS FLOAT: xlsx={len(ams['FLOAT'])} | iscu AMS 40001段?" )
    print(f"AMS INT:   xlsx={len(ams['INT'])} | iscu AMS 45001段?")
    ams_rows = [v for v in iscu.values() if v["topic"] == "AMS"]
    print(f"iscu AMS total: {len(ams_rows)}")
