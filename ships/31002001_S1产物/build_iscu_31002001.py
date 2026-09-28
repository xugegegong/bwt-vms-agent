#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
31002001 远东吉祥 S1 一键生成 + 组装
=====================================
以实船(192.168.8.142:23306 容器 mysql-yuandongjixiang)为基准,按规则重新生成
船端 iscu 配置并落库到仿真库 iscu_31002001(192.168.8.142:13306)。

各通道来源:
  AMS1  模拟量+开关量   <- 船厂 xls(23A046&47 ...MODBUS RTU外传数据格式)
  AMS2  重油流量计      <- 流量计协议(FC04, 0168-0179+0204)
  AMS3  轻油流量计      <- 同上,SlaveId=2
  AMS4  阀控            <- Emerson docx(3.1 阀位 41501 / 3.2 报警 41601)
  SailingData 8 通道    <- 从实船复制(公共通导段,与船无关)

用法: python3 build_iscu_31002001.py
"""
import json, os, re, subprocess, sys

import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, '..', '..', 'tools'))
OUT = os.path.join(HERE, '重做')
os.makedirs(OUT, exist_ok=True)

AMS_XLS = '/sessions/compassionate-jolly-bohr/mnt/uploads/23A046&47 3500DWT油化船 MODBUS RTU外传数据格式-20240509.xls'
VALVE_DOCX = '/sessions/compassionate-jolly-bohr/mnt/uploads/*.docx'

NEW_DB = 'iscu_31002001'
UID_S, UID_E = '1575875275', '1575876387'
# ★ 连接信息不写死在仓库(含凭据的脚本不随仓库分发)。用法:
#   export BWT_DB_HOST=<主机>  BWT_MYSQL_PASSWORD=<密码>
HOST = os.environ.get('BWT_DB_HOST', '')
PWD = os.environ.get('BWT_MYSQL_PASSWORD', '')
if not HOST or not PWD:
    sys.exit('请先设置环境变量 BWT_DB_HOST 与 BWT_MYSQL_PASSWORD（向交付负责人获取，见 knowledge/凭据获取说明.md）')


def dumps_iscu(obj):
    return json.dumps(obj, ensure_ascii=False, indent=4).replace('\n', '\r\n') + '\r\n'


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode:
        print(r.stdout, r.stderr)
        sys.exit(f'命令失败: {cmd}')
    return r.stdout


def build_flowmeter(chan_code, slave_id):
    """流量计:清单 0168-0179 + 0204 → 逻辑地址 30000+清单地址, FC04, 类型14(照实船)"""
    seg0 = [{"Addr": str(r), "Alias": f"{r}00",
             "Coefficient": ("0,0.001,0" if r in (30176, 30178) else "0,1,0"),
             "DataLen": "2", "DataTypeRegister": "14"}
            for r in (30168, 30170, 30172, 30174, 30176, 30178)]
    seg1 = [{"Addr": "30204", "Alias": "3020400", "Coefficient": "0,1,0",
             "DataLen": "2", "DataTypeRegister": "14"}]
    return {"ChanCode": str(chan_code), "ChanDesc": "", "ChanType": "7",
            "DataProtocol": "2",
            "RTU": [{"DataLen": "12", "DevName": "1301", "FunCode": "4",
                     "RegisterBegin": "30168", "SlaveId": str(slave_id), "Results": seg0},
                    {"DataLen": "2", "DevName": "1301", "FunCode": "4",
                     "RegisterBegin": "30204", "SlaveId": str(slave_id), "Results": seg1}],
            "WayUid": UID_E}


def main():
    # ---------- 1. 生成各通道 ----------
    run(f'python3 {TOOLS}/make_ams_jsonstring_16bit.py '
        f'--xlsx "{AMS_XLS}" --way_uid {UID_E} --chan_code 1 --slave_id 1 --funcode 3 '
        f'--orderkey_start 51 --dtr_analog 8 --topic AMS1 --out_json {OUT}/ams1.json '
        f'--out_sql {OUT}/hldkeymap_ams1.sql --out_names {OUT}/names_ams1.csv '
        f'--coeff_override "40091:0.1,40092:0.1,40093:0.1,40190:1,40191:0.1,'
        f'40217:0.001,40218:0.001,40219:0.001,40220:0.001"')
    run(f'python3 {TOOLS}/make_docx_register_jsonstring.py --docx "{VALVE_DOCX}" '
        f'--ranges 41501-41561,41601-41661 --way_uid {UID_E} --chan_code 4 --slave_id 1 '
        f'--funcode 3 --dtr_map "41501-41561:2,41601-41661:1" --orderkey_start 842 '
        f'--topic AMS4 --out_json {OUT}/ams4.json --out_sql {OUT}/hldkeymap_ams4.sql')
    for code, slave, topic, ok0 in [(2, 1, 'AMS2', 828), (3, 2, 'AMS3', 835)]:
        js = build_flowmeter(code, slave)
        with open(f'{OUT}/ams{code}.json', 'w', encoding='utf-8', newline='') as fp:
            fp.write(dumps_iscu(js))
        lines, ok = [], ok0
        for s in js['RTU']:
            for r in s['Results']:
                lines.append(f"INSERT INTO hldkeymap (orderkey,KeyValue,desc_cn,unit,TopicName) "
                             f"VALUES ({ok},'{r['Alias']}','--','','{topic}');")
                ok += 1
        open(f'{OUT}/hldkeymap_ams{code}.sql', 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('1. 4 个机舱通道已生成')

    # ---------- 2. 组装落库 ----------
    ref = pymysql.connect(host=HOST, port=23306, user='root', password=PWD, charset='utf8mb4')
    rc = ref.cursor()
    mine = pymysql.connect(host=HOST, port=13306, user='root', password=PWD, charset='utf8mb4')
    mc = mine.cursor()

    mc.execute(f'DROP DATABASE IF EXISTS {NEW_DB}')
    mc.execute(f'CREATE DATABASE {NEW_DB} DEFAULT CHARSET utf8 COLLATE utf8_bin')
    rc.execute('SHOW TABLES FROM iscu')
    for t in [r[0] for r in rc.fetchall() if not re.search(r'_copy\d+$', r[0])]:
        rc.execute(f'SHOW CREATE TABLE iscu.`{t}`')
        ddl = re.sub(r'AUTO_INCREMENT=\d+\s*', '',
                     rc.fetchone()[1].replace(f'CREATE TABLE `{t}`', f'CREATE TABLE `{NEW_DB}`.`{t}`'))
        mc.execute(ddl)

    rc.execute('SELECT * FROM iscu.dict')
    cols = [d[0] for d in rc.description]
    ph = ','.join(['%s'] * len(cols))
    for r in rc.fetchall():
        mc.execute(f"INSERT INTO {NEW_DB}.dict ({','.join('`'+c+'`' for c in cols)}) VALUES ({ph})", r)

    mc.execute(f"INSERT INTO {NEW_DB}.ways VALUES ('1','{UID_S}','','SailingData','','11','','','3','7')")
    mc.execute(f"INSERT INTO {NEW_DB}.ways VALUES ('1','{UID_E}','','EngineData','','13','','','13','4')")

    for i, t in enumerate(['GPS', 'AIS', 'GYRO', 'EchoSounder', 'SatelliteSpeedLog',
                           'SpeedLog', 'Anemometer', 'Rudder'], 1):
        mc.execute(f"INSERT INTO {NEW_DB}.deviceinfo (GatewayId,ChanType,ChanCode,TopicName,SlaveId,"
                   f"DataProto) VALUES ('{UID_S}',7,{i},%s,0,1)", t)
    for i in range(1, 5):
        mc.execute(f"INSERT INTO {NEW_DB}.deviceinfo (GatewayId,ChanType,ChanCode,TopicName,SlaveId,"
                   f"DataProto) VALUES ('{UID_E}',7,{i},'AMS{i}',1,2)")

    # 通导 8 通道从实船复制（只换 WayUid）
    rc.execute("SELECT ChanCode, DataProtocol, JsonString FROM iscu.channelsdataprotocol "
               "WHERE WayUid='1575875275' ORDER BY ChanCode")
    for code, dp, js in rc.fetchall():
        mc.execute(f"INSERT INTO {NEW_DB}.channelsdataprotocol (ShipId,WayUid,ChanType,ChanCode,"
                   f"DataProtocol,ChanDesc,JsonString) VALUES ('1',%s,7,%s,%s,'',%s)",
                   (UID_S, code, dp, js.replace('1575875275', UID_S)))
    # 机舱 4 通道
    for code in (1, 2, 3, 4):
        js = json.load(open(f'{OUT}/ams{code}.json', encoding='utf-8'))
        js['WayUid'] = UID_E
        mc.execute(f"INSERT INTO {NEW_DB}.channelsdataprotocol (ShipId,WayUid,ChanType,ChanCode,"
                   f"DataProtocol,ChanDesc,JsonString) VALUES ('1',%s,7,%s,2,'',%s)",
                   (UID_E, code, dumps_iscu(js)))

    # hldkeymap：公共通导段 + 4 个机舱通道
    rc.execute("SELECT orderkey,KeyValue,desc_cn,unit,TopicName FROM iscu.hldkeymap "
               "WHERE orderkey<=40 ORDER BY orderkey")
    for r in rc.fetchall():
        mc.execute(f"INSERT INTO {NEW_DB}.hldkeymap (orderkey,KeyValue,desc_cn,unit,TopicName) "
                   f"VALUES (%s,%s,%s,%s,%s)", r)
    for code in (1, 2, 3, 4):
        for line in open(f'{OUT}/hldkeymap_ams{code}.sql', encoding='utf-8'):
            line = line.strip()
            if line:
                mc.execute(line.replace('INSERT INTO hldkeymap', f'INSERT INTO {NEW_DB}.hldkeymap'))

    # topicinfo：通导按实船复制（排除 AMS*，机舱的自建），机舱自建
    rc.execute("SELECT TopicName, TopicDesc, DataType, IndexSet FROM iscu.topicinfo")
    for t, d, dt, ix in rc.fetchall():
        if t.startswith('AMS'):
            continue
        mc.execute(f"INSERT INTO {NEW_DB}.topicinfo (TopicName,TopicDesc,DataType,IndexSet) "
                   f"VALUES (%s,%s,%s,%s)", (t, d, dt, ix))
    for code in (1, 2, 3, 4):
        topic = f'AMS{code}'
        mc.execute(f"SELECT KeyValue FROM {NEW_DB}.hldkeymap WHERE TopicName=%s ORDER BY orderkey", (topic,))
        keys = [r[0] for r in mc.fetchall()]
        mc.execute(f"INSERT INTO {NEW_DB}.topicinfo (TopicName,TopicDesc,DataType,IndexSet) "
                   f"VALUES (%s,'Modbus TCP 开关量',NULL,%s)", (topic, ','.join(keys) + ','))
    mine.commit()
    print('2. 组装落库完成')

    # ---------- 3. 验证 ----------
    print('\n3. 与实船语义比对:')
    def norm(v):
        s = str(v)
        for a, b in (('1575875275', 'UID_S'), ('1575876387', 'UID_E'),
                     ('1789000001', 'UID_S'), ('1789000002', 'UID_E')):
            s = s.replace(a, b)
        return s

    allok = True
    for t in ['ways', 'deviceinfo', 'topicinfo', 'hldkeymap', 'dict']:
        rc.execute(f'SELECT * FROM iscu.{t}')
        rcols = [d[0] for d in rc.description]; rrows = rc.fetchall()
        mc.execute(f'SELECT * FROM {NEW_DB}.{t}')
        mcols = [d[0] for d in mc.description]; mrows = mc.fetchall()
        ri = rcols.index('id') if 'id' in rcols else None
        def N(rows):
            out = []
            for r in rows:
                r = [norm(v) for v in r]
                if ri is not None: r[ri] = ''
                out.append(tuple(r))
            return sorted(out)
        eq = rcols == mcols and N(rrows) == N(mrows)
        allok &= eq
        print(f'   {t:<22} 实船{len(rrows):>4} / 我{len(mrows):>4}  {"✅" if eq else "❌"}')

    # JsonString 语义比对（忽略格式）
    for way_ref, way_new, label in [('1575876387', UID_E, '机舱'),
                                    ('1575875275', UID_S, '通导')]:
        rc.execute(f"SELECT ChanCode, JsonString FROM iscu.channelsdataprotocol WHERE WayUid='{way_ref}'")
        for code, rjs in rc.fetchall():
            mc.execute(f"SELECT JsonString FROM {NEW_DB}.channelsdataprotocol "
                       f"WHERE WayUid=%s AND ChanCode=%s", (way_new, code))
            a, b = json.loads(rjs), json.loads(mc.fetchone()[0])
            a['WayUid'] = b['WayUid'] = 'X'
            if a != b:
                allok = False
                print(f'   ❌ {label} 通道{code} 语义有差异')
    print(f'\n   >>> {"✅ 全部与实船一致" if allok else "❌ 仍有差异"}')
    ref.close(); mine.close()


if __name__ == '__main__':
    main()
