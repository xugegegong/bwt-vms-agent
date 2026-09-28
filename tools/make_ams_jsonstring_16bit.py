#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AMS 16位模拟量格式 → iscu JsonString + hldkeymap
================================================
适用清单(区别于 XT GLORY 的 FLOAT/INT 双 sheet 格式):
  sheet「模拟量通讯数据」: 序号|起始地址|测点名称|类型|量程上|量程下|单位|系数|备注
                            —— 40001 起，**步进1**，16位整型 + 系数
  sheet「开关量状态」:     序号|起始地址(41101.0)|Bit位|测点名称|...
  sheet「模拟量报警状态」: **不处理**（本系统不支持模拟量告警；且各人做法不一，见 S1 skill ①）

★ 与实船(远东吉祥 31002001)比对后确认的规则
--------------------------------------------------
1. **按首末点区间全覆盖**：区间内没有名字的寄存器/位也要配（实船模拟量 40001–40220 全 220 点、
   开关量 41101.01–41135.13 全 557 点）。
2. **模拟量一段到底**，不按 100 拆段（实船 DataLen=220）。
3. **开关量位段一段**（实船 DataLen=35）。
4. 船端 **desc_cn 用 '--' 占位、unit 留空**——中文名在岸端 tab_engine_param。
   本工具额外输出 names.csv，供 S2 配岸端用。
5. orderkey 起点按本船实船值（远东吉祥=51；XT GLORY=101）。

用法:
  python3 make_ams_jsonstring_16bit.py --xlsx <清单.xls> --way_uid <网关uid> \
      --chan_code 1 --slave_id 1 --funcode 3 --orderkey_start 51 \
      --out_json ams.json --out_sql hldkeymap_ams.sql --out_names names.csv
"""
import argparse, csv, json, sys

try:
    import xlrd
except ImportError:
    sys.exit("需要 xlrd: pip install xlrd --break-system-packages")

# DataTypeRegister —— 以 datacap C++ 枚举(system_config.h)为准
DTR_UNSIGNED = 2   # ANALOG_VALUE   2字节无符号
DTR_SIGNED   = 8   # ANALOG_SIGN    2字节有符号
DTR_BIT      = 0   # BITS_VALUE     位点

PLACEHOLDER = '--'   # 船端 desc_cn 占位符(实船如此)


def dumps_iscu(obj):
    """iscu JsonString 统一格式:4 空格缩进 + CRLF(对齐实船机舱通道主流格式)。
    注:格式不影响 datacap 解析(它按 JSON 解),验收以【语义一致】为准。"""
    return json.dumps(obj, ensure_ascii=False, indent=4).replace('\n', '\r\n') + '\r\n'


def write_iscu(path, obj):
    with open(path, 'w', encoding='utf-8', newline='') as fp:
        fp.write(dumps_iscu(obj))


def f(v):
    try:
        return float(str(v).strip())
    except Exception:
        return None


def coeff_from_range(hi):
    """量程上 → 系数(船厂清单规则表)。量程上>250 → 1"""
    if hi is None:
        return 1.0
    if hi < 2.5:
        return 0.001
    if hi < 25:
        return 0.01
    if hi < 250:
        return 0.1
    return 1.0


def parse(xls_path):
    wb = xlrd.open_workbook(xls_path)

    # --- 模拟量（收集所有行的量程/单位；区间端点只看"有名字"的）---
    sh = wb.sheet_by_name('模拟量通讯数据')
    allrows = {}
    for r in range(1, sh.nrows):
        reg = f(sh.cell_value(r, 1))
        if reg is None:
            continue
        allrows[int(reg)] = {
            'reg': int(reg), 'cn': str(sh.cell_value(r, 2)).strip(),
            'hi': f(sh.cell_value(r, 4)), 'lo': f(sh.cell_value(r, 5)),
            'unit': str(sh.cell_value(r, 6)).strip(),
        }
    analog = {reg: v for reg, v in allrows.items() if v['cn']}   # ★ 无名行不算端点
    analog['__allrows__'] = allrows

    # --- 开关量位 ---
    def parse_bits(sheet_name):
        s = wb.sheet_by_name(sheet_name)
        out, base = [], None
        for r in range(1, s.nrows):
            a = str(s.cell_value(r, 1)).strip()
            if a:
                base = int(f(a.split('.')[0]))
            cn = str(s.cell_value(r, 3)).strip()
            bit = f(s.cell_value(r, 2))
            if cn and base is not None and bit is not None:
                out.append({'base': base, 'bit': int(bit), 'cn': cn})
        return out

    return analog, parse_bits('开关量状态')


def fill_range(analog):
    """★ 按首末【有名】寄存器区间全覆盖；无名寄存器也要带出它的量程/单位(清单里有)"""
    if not analog:
        return []
    allrows = analog.pop('__allrows__', {})
    lo, hi = min(analog), max(analog)
    out = []
    for reg in range(lo, hi + 1):
        p = analog.get(reg) or allrows.get(reg)
        if p:
            out.append(dict(p))
        else:
            out.append({'reg': reg, 'cn': '', 'hi': None, 'lo': None,
                        'unit': '', 'unnamed': True})
    return out


def fill_bits(bits):
    """★ 从首个到末个有名字的位，中间全填"""
    if not bits:
        return []
    numbered = {(b['base'], b['bit']): b for b in bits}
    bases = sorted(set(b['base'] for b in bits))
    first = min(bits, key=lambda x: (x['base'], x['bit']))
    last = max(bits, key=lambda x: (x['base'], x['bit']))
    out = []
    for base in range(first['base'], last['base'] + 1):
        max_bit = last['bit'] if base == last['base'] else 15
        for bit in range(0, max_bit + 1):
            b = numbered.get((base, bit))
            out.append(b if b else {'base': base, 'bit': bit, 'cn': '', 'unnamed': True})
    return out


def build_jsonstring(analog_filled, bits_filled, way_uid, chan_code, slave_id,
                     funcode, dev_name, dtr_analog, override=None):
    override = override or {}
    rtu = []
    # 模拟量：一段到底
    results = []
    for p in analog_filled:
        dtr = dtr_analog
        if dtr is None:  # 自动：量程下<0 → 有符号
            dtr = DTR_SIGNED if (p.get('lo') is not None and p['lo'] < 0) else DTR_UNSIGNED
        coeff = override.get(p['reg'], coeff_from_range(p.get('hi')))
        results.append({
            "Addr": str(p['reg']),
            "Alias": f"{p['reg']}00",
            "Coefficient": f"0,{coeff:g},0",
            "DataLen": "1",
            "DataTypeRegister": str(dtr),
        })
    if results:
        rtu.append({
            "DataLen": str(analog_filled[-1]['reg'] - analog_filled[0]['reg'] + 1),
            "DevName": dev_name, "FunCode": str(funcode),
            "RegisterBegin": str(analog_filled[0]['reg']),
            "SlaveId": str(slave_id), "Results": results,
        })
    # 位点：一段
    if bits_filled:
        results = []
        for b in bits_filled:
            results.append({
                "Addr": f"{b['base']}.{b['bit']+1:02d}",
                "Alias": f"{b['base']}{b['bit']+1:02d}",
                "Coefficient": "0,1,0", "DataLen": "1",
                "DataTypeRegister": str(DTR_BIT),
            })
        firstr, lastr = bits_filled[0]['base'], bits_filled[-1]['base']
        rtu.append({
            "DataLen": str(lastr - firstr + 1),
            "DevName": dev_name, "FunCode": str(funcode),
            "RegisterBegin": str(firstr), "SlaveId": str(slave_id),
            "Results": results,
        })
    return {"ChanCode": str(chan_code), "ChanDesc": "", "ChanType": "7",
            "DataProtocol": "2", "RTU": rtu, "WayUid": str(way_uid)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--xlsx', required=True)
    ap.add_argument('--way_uid', required=True)
    ap.add_argument('--chan_code', default='1')
    ap.add_argument('--slave_id', default='1')
    ap.add_argument('--funcode', default='3')
    ap.add_argument('--dev_name', default='1301')
    ap.add_argument('--coeff_override', default='',
                    help='现场标定的人工系数覆盖,如 "40091:0.1,40217:0.001"。'
                         '规则只能推出约95%%的点,其余是现场按实际标定调的')
    ap.add_argument('--orderkey_start', type=int, default=101)
    ap.add_argument('--dtr_analog', default='8',
                    help='模拟量类型: 8=有符号(实船默认) 2=无符号 auto=按量程判断')
    ap.add_argument('--topic', default='AMS1')
    ap.add_argument('--out_json', default='ams_jsonstring.json')
    ap.add_argument('--out_sql', default='hldkeymap_ams.sql')
    ap.add_argument('--out_names', default='names.csv')
    a = ap.parse_args()

    dtr = None if a.dtr_analog == 'auto' else int(a.dtr_analog)
    override = {}
    if a.coeff_override:
        for part in a.coeff_override.split(','):
            k, v = part.split(':')
            override[int(k.strip())] = float(v.strip())

    analog, bits = parse(a.xlsx)
    af = fill_range(analog)
    bf = fill_bits(bits)
    print(f"清单有名点: 模拟量 {len(analog)} | 开关量位 {len(bits)}")
    print(f"★ 区间全覆盖后: 模拟量 {len(af)} | 开关量位 {len(bf)} | 合计 {len(af)+len(bf)}")
    unnamed_a = [p['reg'] for p in af if p.get('unnamed')]
    unnamed_b = [f"{p['base']}.{p['bit']+1:02d}" for p in bf if p.get('unnamed')]
    print(f"  其中无名补齐: 模拟量 {len(unnamed_a)} 个 {unnamed_a} | 开关量 {len(unnamed_b)} 个 {unnamed_b[:8]}")
    print(f"  模拟量类型: {'auto(按量程)' if dtr is None else dtr}")

    js = build_jsonstring(af, bf, a.way_uid, a.chan_code, a.slave_id,
                          a.funcode, a.dev_name, dtr, override)
    if override:
        print(f"  系数覆盖 {len(override)} 个: {override}")
    for i, s in enumerate(js['RTU']):
        dtrs = {}
        for r in s['Results']:
            dtrs[r['DataTypeRegister']] = dtrs.get(r['DataTypeRegister'], 0) + 1
        print(f"  段{i}: begin={s['RegisterBegin']} DataLen={s['DataLen']} "
              f"点数={len(s['Results'])} 类型={dtrs}")

    al = [r['Alias'] for s in js['RTU'] for r in s['Results']]
    dup = {x for x in al if al.count(x) > 1}
    print("Alias 重复:", dup if dup else "无")
    write_iscu(a.out_json, js)

    # hldkeymap（★ 船端 desc_cn 一律 '--'、unit 空——实船如此，中文名在岸端）
    # names.csv 另行输出，供 S2 配岸端 tab_engine_param 用
    ok, lines, names = a.orderkey_start, [], []
    for p in af:
        lines.append(f"INSERT INTO hldkeymap (orderkey,KeyValue,desc_cn,unit,TopicName) VALUES "
                     f"({ok},'{p['reg']}00','{PLACEHOLDER}','',{a.topic!r});")
        if p['cn']:
            names.append(('AMS', f"{p['reg']}00", p['cn'], p.get('unit') or ''))
        ok += 1
    for p in bf:
        lines.append(f"INSERT INTO hldkeymap (orderkey,KeyValue,desc_cn,unit,TopicName) VALUES "
                     f"({ok},'{p['base']}{p['bit']+1:02d}','{PLACEHOLDER}','',{a.topic!r});")
        if p['cn']:
            names.append(('AMS', f"{p['base']}{p['bit']+1:02d}", p['cn'], ''))
        ok += 1
    open(a.out_sql, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    with open(a.out_names, 'w', encoding='utf-8', newline='') as fp:
        w = csv.writer(fp); w.writerow(['topic', 'key', 'name_cn', 'unit']); w.writerows(names)
    print(f"orderkey: {a.orderkey_start}~{ok-1} (共 {len(lines)})")
    print(f"输出: {a.out_json} / {a.out_sql} / {a.out_names}")


if __name__ == '__main__':
    main()
