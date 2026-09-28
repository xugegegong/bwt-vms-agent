#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
docx 表格清单(Holding register 列) → iscu JsonString + hldkeymap
================================================================
适用: 供应商 Modbus 文档(docx)里以表格给出 Holding register 列表,
      如 Emerson 阀控(FAMEN)、其他第三方设备。

表格形态: No | Name | Holding register | ValueType | Scale | Remark
  ValueType: Unsigned / INTERGER / REAL 等
  → iscu DataTypeRegister:  Unsigned→8(无符号模拟量)  INTERGER→2(有符号模拟量)

分段: 按寄存器连续性自动分段, 每段 ≤ max_reg(默认100, 供应商若写明 90/80 请传参)

用法:
  python3 make_docx_register_jsonstring.py --docx <文件.docx> \
      --ranges 41501-41561,41601-41661 \
      --way_uid 1789000002 --chan_code 2 --slave_id 1 --funcode 3 \
      --orderkey_start 1072 --suffix_map "41501-41561:阀位,41601-41661:报警" \
      --out_json famen.json --out_sql hldkeymap_famen.sql
"""
import argparse, glob, json, os, re, sys

try:
    from docx import Document
except ImportError:
    sys.exit("需要 python-docx: pip install python-docx --break-system-packages")

VALUE_TYPE_MAP = {'unsigned': 8, 'integer': 2, 'interger': 2, 'real': 4}

# DataTypeRegister —— 以 datacap C++ 枚举(system_config.h)为准
DTR_BIT, DTR_SWITCH, DTR_UNSIGNED, DTR_FLOAT_ABCD, DTR_FLOAT_BADC = 0, 1, 2, 3, 4
DTR_SIGNED = 8


def dumps_iscu(obj):
    """iscu JsonString 统一格式:4 空格缩进 + CRLF(对齐实船机舱通道主流格式)。
    格式不影响 datacap 解析,验收以【语义一致】为准。"""
    return json.dumps(obj, ensure_ascii=False, indent=4).replace('\n', '\r\n') + '\r\n'


def parse_range_map(s):
    """'41501-41561:2,41601-41661:1' -> [(41501,41561,'2'), ...]"""
    out = []
    if not s:
        return out
    for part in s.split(','):
        rng, val = part.split(':')
        a, b = rng.split('-')
        out.append((int(a), int(b), val.strip()))
    return out



def parse_ranges(s):
    out = []
    for part in s.split(','):
        a, b = part.split('-')
        out.append((int(a), int(b)))
    return out


def parse_docx(path, ranges):
    d = Document(path)
    pts = []
    for tb in d.tables:
        rows = tb.rows
        # 找表头行(含 Holding register)
        hdr = None
        for i, r in enumerate(rows):
            cells = [c.text.strip().lower() for c in r.cells]
            if any('holding' in c for c in cells):
                hdr = i
                break
        if hdr is None:
            continue
        for r in rows[hdr + 1:]:
            cells = [c.text.strip() for c in r.cells]
            ded = []
            for c in cells:
                if not ded or ded[-1] != c:
                    ded.append(c)
            if len(ded) < 3:
                continue
            try:
                reg = int(re.sub(r'[^0-9]', '', ded[2]))
            except Exception:
                continue
            name = ded[1].strip()
            vt = ded[3].strip().lower() if len(ded) > 3 else 'unsigned'
            if not any(a <= reg <= b for a, b in ranges):
                continue
            pts.append({'reg': reg, 'name': name, 'vt': vt})
    # 去重 + 排序
    seen, out = set(), []
    for p in sorted(pts, key=lambda x: x['reg']):
        if p['reg'] in seen:
            continue
        seen.add(p['reg'])
        out.append(p)
    return out


def split_segments(pts, max_reg):
    """按段起点算跨度 ≤max_reg,且遇地址断档(不连续)另起一段"""
    segs, cur = [], []
    for p in pts:
        if cur:
            span = p['reg'] - cur[0]['reg'] + 1      # 相对段起点的跨度
            gap = p['reg'] - cur[-1]['reg'] > 1      # 与上一点不连续
            if span > max_reg or gap:
                segs.append(cur); cur = []
        cur.append(p)
    if cur:
        segs.append(cur)
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--docx', required=True)
    ap.add_argument('--ranges', required=True, help='如 41501-41561,41601-41661')
    ap.add_argument('--way_uid', required=True)
    ap.add_argument('--chan_code', required=True)
    ap.add_argument('--slave_id', default='1')
    ap.add_argument('--funcode', default='3')
    ap.add_argument('--dev_name', default='1301')
    ap.add_argument('--max_reg', type=int, default=100)
    ap.add_argument('--dtr_map', default='', help='分段指定类型,如 "41501-41561:2,41601-41661:1";优先于 ValueType 自动判断')
    ap.add_argument('--addr_offset', type=int, default=0,
                    help='逻辑地址 = 清单地址 + 本偏移。FC04 输入寄存器传 30000(清单0168→30168);FC03 传 0')
    ap.add_argument('--orderkey_start', type=int, required=True)
    ap.add_argument('--suffix_map', default='', help='如 "41501-41561:阀位,41601-41661:报警"')
    ap.add_argument('--topic', default=None)
    ap.add_argument('--out_json', default='out.json')
    ap.add_argument('--out_sql', default='out.sql')
    a = ap.parse_args()

    # 兼容:传入的通配符/含不可见字符的路径,统一用 glob 解析
    path = a.docx
    if not os.path.exists(path):
        cand = glob.glob(path)
        if not cand:
            # 退一步:按目录 + 模糊匹配
            d, base = os.path.split(path)
            cand = [f for f in glob.glob(os.path.join(d, '*.docx'))
                    if re.sub(r'\s+', '', os.path.basename(f)) == re.sub(r'\s+', '', base)]
        if not cand:
            sys.exit('找不到文件: ' + path)
        path = cand[0]
    print('读取:', os.path.basename(path))

    ranges = parse_ranges(a.ranges)
    pts = parse_docx(path, ranges)
    print(f"解析到 {len(pts)} 个点, 寄存器 {pts[0]['reg']}~{pts[-1]['reg']}")

    suffix = []
    if a.suffix_map:
        for part in a.suffix_map.split(','):
            rng, suf = part.split(':')
            x, y = rng.split('-')
            suffix.append((int(x), int(y), suf))

    def label(p):
        for x, y, s in suffix:
            if x <= p['reg'] <= y:
                return f"{p['name']} {s}" if p['name'] else s
        return p['name']

    dtr_map = parse_range_map(a.dtr_map)
    off = a.addr_offset

    def logical(reg):
        return reg + off

    def dtr_of(p):
        for x, y, v in dtr_map:
            if x <= p['reg'] <= y:
                return v
        return str(VALUE_TYPE_MAP.get(p['vt'], DTR_SIGNED))

    segs = split_segments(pts, a.max_reg)
    rtu = []
    for seg in segs:
        results = []
        for p in seg:
            results.append({
                "Addr": str(logical(p['reg'])),
                "Alias": f"{logical(p['reg'])}00",
                "Coefficient": "0,1,0",
                "DataLen": "1",
                "DataTypeRegister": dtr_of(p),
            })
        rtu.append({
            "DataLen": str(logical(seg[-1]['reg']) - logical(seg[0]['reg']) + 1),
            "DevName": a.dev_name,
            "FunCode": str(a.funcode),
            "RegisterBegin": str(logical(seg[0]['reg'])),
            "SlaveId": str(a.slave_id),
            "Results": results,
        })

    js = {
        "ChanCode": str(a.chan_code), "ChanDesc": "", "ChanType": "7",
        "DataProtocol": "2", "RTU": rtu, "WayUid": str(a.way_uid),
    }
    print(f"段数: {len(rtu)}")
    for i, s in enumerate(rtu):
        print(f"  段{i}: begin={s['RegisterBegin']} DataLen={s['DataLen']} 点数={len(s['Results'])}")

    al = [r['Alias'] for s in rtu for r in s['Results']]
    dup = {x for x in al if al.count(x) > 1}
    print("Alias 重复:", dup if dup else "无")

    with open(a.out_json, 'w', encoding='utf-8', newline='') as fp:
        fp.write(dumps_iscu(js))

    ok = a.orderkey_start
    lines = []
    for p in pts:
        lines.append(f"INSERT INTO hldkeymap (orderkey,KeyValue,desc_cn,unit,TopicName) VALUES "
                     f"({ok},'{logical(p['reg'])}00','--','',{a.topic!r});")
        ok += 1
    open(a.out_sql, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print(f"orderkey: {a.orderkey_start}~{ok-1} (共 {len(pts)})")
    print(f"输出: {a.out_json} / {a.out_sql}")


if __name__ == '__main__':
    main()
