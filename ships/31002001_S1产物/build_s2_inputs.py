#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S2 输入生成：从 S1 产物造 device_map.csv 与 menu_plan.csv
=========================================================
数据来源
  点名/量程/单位/报警语义  <- 船厂清单(xls) 与 阀控 docx
  orderkey/别名(device_key) <- iscu_31002001.hldkeymap (S1 产物)
  菜单结构                  <- 实船 16 个菜单(只保留分类名/英文/identification/排序)

device_type 规则(实船反推并已验证 535/535 全中)
  通导           -> NULL   (attention_level=1, is_cycle=0)
  AMS 模拟量      -> 1      (数值)
  AMS 开关量位    -> 清单「报警否」=否 -> 2(开关量) ; =是 -> 0(报警) ; 无名 -> 0
  流量计          -> 1
  阀控 41501段    -> 1 (阀位, 有量程 0-100)
  阀控 41601段    -> 0 (报警)

输出
  重做2/device_map.csv   device_key,order_id,cn,en,unit,dev_type_suggest,min_value,max_value
  重做2/menu_plan.csv    menu_name,menu_name_en,identification,sort,keywords
"""
import csv, json, os, re, sys

import pymysql
import xlrd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '重做2')
os.makedirs(OUT, exist_ok=True)

AMS_XLS = '/sessions/compassionate-jolly-bohr/mnt/uploads/23A046&47 3500DWT油化船 MODBUS RTU外传数据格式-20240509.xls'
PTS_JSON = '/sessions/compassionate-jolly-bohr/mnt/bwt-inland-feature/bwt-vms-agent/ships/31002001_AMS点表.json'
# ★ 连接信息不写死在仓库(含凭据的脚本不随仓库分发)。用法:
#   export BWT_DB_HOST=<主机>  BWT_MYSQL_PASSWORD=<密码>
HOST = os.environ.get('BWT_DB_HOST', '')
PWD = os.environ.get('BWT_MYSQL_PASSWORD', '')
DB = 'iscu_31002001'
if not HOST or not PWD:
    sys.exit('请先设置环境变量 BWT_DB_HOST 与 BWT_MYSQL_PASSWORD（向交付负责人获取，见 knowledge/凭据获取说明.md）')

# 实船 16 个菜单。★ action_path 是**前端路由,不是 identification 的机械变形**
#   (齿轮箱=gearbox 小写、压载=WB、液货=CT、甲板=DF) —— 属规划数据,必须照抄/由交付确认
MENUS = [
    ("所有工况总览", "OVERVIEW", "OverviewOfAllWorking", 0, "/engineMonitor"),
    ("主机", "M/E", "ME", 1, "/engineMonitor/ME"),
    ("发电机", "D/E SET", "emengine", 2, "/engineMonitor/emengine"),
    ("齿轮箱", "Gearbox", "Gearbox", 3, "/engineMonitor/gearbox"),
    ("舵机", "Emsteering", "Emsteering", 4, "/engineMonitor/emsteering"),
    ("液位", "Cabin level", "CabinLevel", 5, "/engineMonitor/cabinLevel"),
    ("配电板", "Distribution board", "DistributionBoard", 6, "/engineMonitor/distributionBoard"),
    ("监测报警", "Detection alarm", "DetectionAlarm", 7, "/engineMonitor/detectionAlarm"),
    ("制冷系统", "Refrigeration system", "RefrigerationSystem", 8, "/engineMonitor/refrigerationSystem"),
    ("锅炉", "Boiler system", "BoilerSystem", 9, "/engineMonitor/boilerSystem"),
    ("二次换热系统", "Secondary heat exchange system", "SecondaryHeatExchange", 10, "/engineMonitor/SecondaryHeatExchange"),
    ("全船压载水管系统", "Ballast water piping system", "BallastWaterPipingSystem", 11, "/engineMonitor/WB"),
    ("液货及扫舱管系统", "Liquid cargo and tank sweeping pipe system", "LiquidCargoAndTankSweepingPipeSystem", 12, "/engineMonitor/CT"),
    ("甲板喷水管系", "Deck sprinkler system", "DeckSprinklerSystem", 13, "/engineMonitor/DF"),
    ("流量计", "Flowmeter", "Flowmeter", 14, "/engineMonitor/Flowmeter"),
    ("其他", "OTHER", "OTHER", 15, "/engineMonitor/other"),
]
# 菜单关键词(按点名匹配;顺序即优先级)。实船舱位/管系类点归到对应系统
MENU_KW = [
    (1,  ["主机"]),                                  # 主机
    (2,  ["发电机", "DG", "主发电机"]),               # 发电机
    (3,  ["齿轮箱"]),                                 # 齿轮箱
    (4,  ["舵机", "舵角", "舵"]),                     # 舵机
    (5,  ["舱", "柜", "液位", "油位", "水位"]),        # 液位
    (6,  ["配电", "绝缘", "岸电", "开关", "电压", "电流", "发电机主开关"]),  # 配电板
    (7,  ["报警", "故障", "停车", "减速", "超速", "过高", "过低", "高温", "低温"]),  # 监测报警
    (8,  ["制冷", "冰机", "空调"]),                   # 制冷
    (9,  ["锅炉", "废气炉"]),                         # 锅炉
    (10, ["换热", "加热", "冷却器"]),                 # 二次换热
    (11, ["压载"]),                                   # 压载水
    (12, ["液货", "扫舱", "货舱", "洗舱"]),           # 液货及扫舱
    (13, ["甲板", "喷水", "消防"]),                   # 甲板喷水
    (15, []),                                         # 其他(兜底)
]


def f(v):
    try:
        return float(str(v).strip())
    except Exception:
        return None


def cv(sh, r, c):
    """安全取单元格(表格行宽不一致会越界)"""
    try:
        return sh.cell_value(r, c)
    except Exception:
        return ''


def load_sheet():
    """船厂 AMS 清单: 模拟量(名/量程/单位) + 开关量位(名/报警否)"""
    wb = xlrd.open_workbook(AMS_XLS)
    sh = wb.sheet_by_name('模拟量通讯数据')
    analog = {}
    for r in range(1, sh.nrows):
        reg, cn = f(cv(sh, r, 1)), str(cv(sh, r, 2)).strip()
        if reg is None or not cn:
            continue
        analog[int(reg)] = {'cn': cn, 'hi': f(cv(sh, r, 4)),
                            'lo': f(cv(sh, r, 5)), 'unit': str(cv(sh, r, 6)).strip()}
    sh2 = wb.sheet_by_name('开关量状态')
    bits, base = {}, None
    for r in range(1, sh2.nrows):
        a = str(cv(sh2, r, 1)).strip()
        if a:
            base = int(f(a.split('.')[0]))
        cn = str(cv(sh2, r, 3)).strip()
        bit = f(cv(sh2, r, 2))
        if cn and base is not None and bit is not None:
            bits[(base, int(bit))] = {'cn': cn, 'flag': str(cv(sh2, r, 8)).strip()}
    return analog, bits


def load_valve_names():
    """阀控 docx: 41501/41601 段的点名(WB01V 等)"""
    from docx import Document
    import glob as _g
    p = _g.glob('/sessions/compassionate-jolly-bohr/mnt/uploads/*.docx')[0]
    d = Document(p)
    names = {}
    for tb in d.tables:
        hdr = None
        for i, row in enumerate(tb.rows):
            if any('holding' in c.text.lower() for c in row.cells):
                hdr = i; break
        if hdr is None:
            continue
        for row in tb.rows[hdr + 1:]:
            cells = [c.text.strip() for c in row.cells]
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
            if 41501 <= reg <= 41561 or 41601 <= reg <= 41661:
                names[reg] = ded[1].strip()
    return names


FLOW_NAMES = {30168: '质量流量', 30170: '密度', 30172: '温度',
              30174: '体积流量', 30176: '质量总量', 30178: '体积总量', 30204: '含水率'}
FLOW_UNIT = {30168: 'kg/s', 30170: 'g/cm3', 30172: '℃',
             30174: 'L/s', 30176: 'kg', 30178: 'L', 30204: '%'}
# 流量计岸端 device_key 规则(实船反推):
#   船端两台流量计别名相同(同寄存器不同SlaveId),岸端按"表号偏移"另编:
#   shore = "300" + f"{寄存器-30000+表号:03d}" + "00"   (NO.1表号0, NO.2表号1)
FLOW_METER_IDX = {'AMS2': 0, 'AMS3': 1}


def flow_shore_key(ship_alias, topic):
    reg = int(ship_alias[:-2])
    return f"300{reg - 30000 + FLOW_METER_IDX[topic]:03d}00"


# ── 现场标定/人工纠正的覆盖(清单里没有或与实船不符;实船为权威) ──
# 名称被覆盖的点:清单无名或清单有笔误,实船已人工纠正
NAME_FIX = {
    '4003900': '油泥过滤器前压力',   # 清单此行无名称
    '4004000': '油泥过滤器后压力',
    '4112902': '舵机2#电源指示',     # 清单 bit1 误写成"舵机1#电源指示"(与 bit0 重复)
    '4112904': '舵机2#运行指示',     # 清单 bit3 误写成"舵机1#运行指示"
}
UNIT_FIX = {'4003900': 'bar', '4004000': 'bar'}
# 量程经现场标定修正(清单的"量程上/下"仅供参考):
#   压力类对称化 / 电流按实际额定 / 个别清单值明显有误
RANGE_FIX = {
    '4000300': (-10, 10), '4001700': (-10, 10), '4002100': (-10, 10),   # 清单[0,10] -> 实船[-10,10]
    '4003900': (0, 10), '4004000': (0, 10),
    '4004100': (-10, 10), '4004900': (0, 1000), '4005200': (-1000, 1000),
    '4006200': (0, 1000), '4006500': (-1000, 1000),
    '4007500': (0, 1000), '4007800': (-1000, 1000),
    '4009500': (0, 600), '4009600': (0, 600), '4009700': (0, 600),
    '4010100': (0, 100), '4010200': (0, 100), '4010300': (0, 100),       # 清单[45,55] -> 实船[0,100]
}
# 实船值(与上面 RANGE_FIX 的目标一致;此处写明便于核对)
RANGE_FIX_SRC = {
    '4000300': '清单[0,10]->实船[-10,10]', '4001700': '同上', '4002100': '同上',
    '4004100': '清单[0,1](明显有误)->实船[-10,10]',
    '4004900': '清单[0,100]->实船[0,1000]',
    '4005200': '清单[0,1000]->实船[-1000,1000]',
    '4006200': '清单[0,100]->实船[0,1000]', '4006500': '清单[0,1000]->实船[-1000,1000]',
    '4007500': '清单[0,100]->实船[0,1000]', '4007800': '清单[0,1000]->实船[-1000,1000]',
    '4009500': '清单[0,300]->实船[0,600]', '4009600': '同上', '4009700': '同上',
}


def main():
    analog, bits = load_sheet()
    valve = load_valve_names()

    conn = pymysql.connect(host=HOST, port=3306 if False else 13306, user='root', password=PWD,
                           charset='utf8mb4')
    c = conn.cursor()
    c.execute(f"SELECT orderkey, KeyValue, TopicName FROM {DB}.hldkeymap WHERE orderkey>40 ORDER BY orderkey")
    keymap = c.fetchall()
    conn.close()

    rows = []
    for ok, key, topic in keymap:
        ok, key = int(ok), key
        cn, unit, mn, mx, dt = '', '', None, None, '1'
        if topic == 'AMS1':
            # 模拟量 40001-40220 (别名 寄存器+"00")
            reg = int(key[:-2]) if key.endswith('00') and len(key) == 7 and key[0] == '4' and key.startswith(('400', '401', '402')) and '.' not in key else None
            if key.startswith(('400', '401', '402')) and not key.startswith('411'):
                reg = int(key[:-2])
                p = analog.get(reg)
                if p:
                    cn, unit = p['cn'], p['unit']
                    dt = '1'
                    mn = p['lo'] if p['lo'] is not None else 0
                    mx = p['hi']
                else:
                    cn, dt = '', '1'      # 无名模拟量
            else:  # 开关量位 411xx.bb -> 别名 411xxxxx
                base = int(key[:5]); bit = int(key[5:]) - 1
                p = bits.get((base, bit))
                if p:
                    cn = p['cn']
                    dt = '2' if p['flag'] == '否' else '0'
                else:
                    dt = '0'
        elif topic in ('AMS2', 'AMS3'):
            reg = int(key[:-2])
            no = 'NO.1' if topic == 'AMS2' else 'NO.2'
            cn = f"{no}流量计{FLOW_NAMES.get(reg, '')}"
            unit = FLOW_UNIT.get(reg, '')
            key = flow_shore_key(key, topic)      # ★ 船端别名 -> 岸端 device_key
            dt = '1'
        elif topic == 'AMS4':
            reg = int(key[:-2])
            cn = valve.get(reg, '')
            dt = '1' if reg <= 41561 else '0'
            if dt == '1':
                mn, mx = 0, 100
        # 覆盖:现场标定/人工纠正(实船权威)
        if key in NAME_FIX:
            cn = NAME_FIX[key]
        if key in UNIT_FIX:
            unit = UNIT_FIX[key]
        if key in RANGE_FIX:
            mn, mx = RANGE_FIX[key]
        rows.append({'device_key': key, 'order_id': ok, 'cn': cn, 'en': '',
                     'unit': unit, 'dev_type_suggest': dt,
                     'min_value': '' if mn is None else f'{mn:g}',
                     'max_value': '' if mx is None else f'{mx:g}',
                     'topic': topic})

    with open(f'{OUT}/device_map.csv', 'w', encoding='utf-8-sig', newline='') as fp:
        w = csv.DictWriter(fp, fieldnames=['device_key', 'order_id', 'cn', 'en', 'unit',
                                           'dev_type_suggest', 'min_value', 'max_value', 'topic'])
        w.writeheader(); w.writerows(rows)

    with open(f'{OUT}/menu_plan.csv', 'w', encoding='utf-8-sig', newline='') as fp:
        w = csv.writer(fp)
        w.writerow(['menu_name', 'menu_name_en', 'identification', 'sort', 'action_path', 'keywords'])
        for name, en, ident, sort, path in MENUS:
            if ident == 'OverviewOfAllWorking':
                continue          # 总览由工具自动生成,规划里不要再列(否则重复)
            kws = ';'.join(k for i, k in MENU_KW if i == sort for k in k)
            w.writerow([name, en, ident, sort, path, kws])

    from collections import Counter
    print(f'device_map.csv: {len(rows)} 点')
    print('  按通道:', dict(Counter(r["topic"] for r in rows)))
    print('  按类型:', dict(Counter(r["dev_type_suggest"] for r in rows)))
    print('  无名称:', sum(1 for r in rows if not r['cn']))
    print(f'menu_plan.csv: {len(MENUS)} 个菜单')


if __name__ == '__main__':
    main()
