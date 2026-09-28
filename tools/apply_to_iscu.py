#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_to_iscu.py — 把 make_jsonstring 的产物落库到船端 iscu (S1 第 5 步)
前提:在船端服务器(或能 SSH 到船端的机器)上运行,或本机可 docker exec mysql。

功能:
  1. 自动备份(iscu 库 → 备份文件,含时间戳)
  2. 写 channelsdataprotocol.JsonString(按 WayUid+ChanCode 幂等:先删后插)
  3. 执行 hldkeymap.sql(公共段+全部采集点)
  4. 同步 deviceinfo / topicinfo(AMS/LOAD/BTP 三通道按生成数据重写)
  5. 落库后验证(行数/通道 JsonString 点数)

用法(三种执行方式任选):
  A. 在船端服务器上直接跑(有 mysql 容器):
     python3 apply_to_iscu.py --gen_dir 输出目录 --host localhost --mode docker --container mysql
  B. 从岸端 SSH 过去跑(推荐,自动经 SSH):
     python3 apply_to_iscu.py --gen_dir 输出目录 --ssh 192.168.8.142 --ssh_user root --ssh_pass 'xxx' --mode docker --container mysql
  C. mysql 可直连时:--mode direct --mysql_host .. --mysql_port .. --mysql_user root --mysql_pass xxx

注意:
  - 落库后需重启 datacap 才生效(每次重启重新加载 iscu 配置)
  - Deviceinfo/topicinfo 会删除 EngineData 网关(1575876387)下 AMS/LOAD/BTP 的旧通道行并按生成数据重建
"""
import argparse, json, os, subprocess, sys, time

ENGINE_WAYUID = "1575876387"
NAV_MAX = 100  # orderkey<=100 为公共导航段

# ---------------- SQL 执行器 ---------------- #
class RemoteSqlRunner:
    """经 SSH(paramiko)在远端 docker exec mysql 执行"""
    def __init__(self, ssh_host, ssh_user, ssh_pass, container="mysql",
                 user="root", pw=None):
        if not pw:
            raise SystemExit("缺少 MySQL 密码:请设环境变量 BWT_MYSQL_PASSWORD,或用 --mysql_pass 传入")
        import paramiko
        self.cli = paramiko.SSHClient()
        self.cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.cli.connect(ssh_host, username=ssh_user, password=ssh_pass, timeout=15)
        self.container, self.user, self.pw = container, user, pw
        self.host = ssh_host
    def run(self, sql, database=None):
        db = database or "iscu"
        # -N 去表头;stdin 传 SQL 避免引号转义
        inner = (f"docker exec -i {self.container} mysql -N -u{self.user} -p{self.pw} {db}")
        stdin, stdout, stderr = self.cli.exec_command(inner, timeout=120)
        stdin.write(sql); stdin.channel.shutdown_write()
        out, err = stdout.read().decode('utf-8', 'replace'), stderr.read().decode('utf-8', 'replace')
        err_clean = "\n".join(l for l in err.splitlines() if "Using a password" not in l)
        if err_clean.strip():
            raise RuntimeError(f"SQL失败: {err_clean[:400]}\nSQL: {sql[:200]}")
        return out
    def dump_backup(self, bak_remote_path):
        inner = (f"docker exec {self.container} sh -c 'mysqldump -u{self.user} -p{self.pw} iscu' > {bak_remote_path}")
        _, stdout, stderr = self.cli.exec_command(inner, timeout=300)
        rc = stdout.channel.recv_exit_status()
        return rc == 0

# ---------------- 主流程 ---------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen_dir", required=True, help="make_jsonstring 输出目录")
    ap.add_argument("--mode", choices=["docker", "direct", "ssh"], default="ssh")
    ap.add_argument("--host", default=os.getenv("BWT_DB_HOST", ""))
    ap.add_argument("--ssh_user", default=os.getenv("BWT_SSH_USER", "root"))
    ap.add_argument("--ssh_pass", default=os.getenv("BWT_SSH_PASSWORD", ""))
    ap.add_argument("--container", default="mysql")
    ap.add_argument("--mysql_user", default=os.getenv("BWT_MYSQL_USER", "root"))
    ap.add_argument("--mysql_pass", default=os.getenv("BWT_MYSQL_PASSWORD", ""))
    ap.add_argument("--skip_backup", action="store_true")
    a = ap.parse_args()

    # 连接信息不写死在仓库里（含凭据的脚本不随仓库分发）——缺什么就明确报错
    missing_env = [n for n, v in (("--host/BWT_DB_HOST", a.host),
                                  ("--ssh_pass/BWT_SSH_PASSWORD", a.ssh_pass),
                                  ("--mysql_pass/BWT_MYSQL_PASSWORD", a.mysql_pass)) if not v]
    if missing_env:
        raise SystemExit("缺少连接参数: " + "、".join(missing_env) +
                         "\n（这些值不存仓库，向交付负责人获取；见 knowledge/凭据获取说明.md）")

    gen = a.gen_dir
    need = ["hldkeymap.sql"] + [f"{n}.jsonstring.json" for n in ("ams", "load", "btp")]
    missing = [f for f in need if os.path.exists(os.path.join(gen, f)) is False]
    have = {n: os.path.exists(os.path.join(gen, f"{n}.jsonstring.json")) for n in ("ams","load","btp")}
    print(f"产物检查: hldkeymap.sql={'OK' if not missing or 'hldkeymap.sql' not in missing else '缺'} "
          f"ams={have['ams']} load={have['load']} btp={have['btp']}")

    if a.mode == "ssh":
        runner = RemoteSqlRunner(a.host, a.ssh_user, a.ssh_pass, a.container, a.mysql_user, a.mysql_pass)
    elif a.mode == "docker":
        raise SystemExit("docker 模式:请在船端服务器上改用 LocalRunner(本工具当前版本只支持 ssh 模式,可在船端直接用 mysql 客户端手工执行)")
    else:
        raise SystemExit("direct 模式暂未实现,请用 --mode ssh")

    # 0. 现状
    cur = runner.run("SELECT COUNT(*) FROM hldkeymap;").strip()
    print(f"落库前 hldkeymap 行数: {cur}")

    # 1. 备份
    if not a.skip_backup:
        ts = time.strftime("%Y%m%d_%H%M%S")
        bak = f"/tmp/iscu_backup_{ts}.sql"
        if runner.dump_backup(bak):
            print(f"✅ 备份: 远端 {bak}")
        else:
            print("⚠️ 备份失败,中止(确认后可 --skip_backup 跳过)")
            sys.exit(1)

    # 2. hldkeymap.sql(自带 DELETE+REPLACE,幂等;按语句拆分执行)
    sql_file = os.path.join(gen, "hldkeymap.sql")
    body = open(sql_file, encoding="utf-8").read()
    import re as _re
    body = _re.sub(r"--[^\n]*", "", body)          # 去行注释
    for stmt in [s.strip() for s in body.split(";") if s.strip()]:
        runner.run(stmt)
    cnt = runner.run("SELECT COUNT(*) FROM hldkeymap;").strip()
    print(f"✅ hldkeymap 落库: {cnt} 行")

    # 3. channelsdataprotocol(按通道:删旧插新)
    chan_map = {"ams": 1, "load": 2, "btp": 3}
    for name, ok in have.items():
        if not ok:
            continue
        js = open(os.path.join(gen, f"{name}.jsonstring.json"), encoding="utf-8").read()
        cc = chan_map[name]
        runner.run(f"DELETE FROM channelsdataprotocol WHERE WayUid='{ENGINE_WAYUID}' AND ChanCode={cc};")
        # JsonString 存"带 \n 转义的原样字符串"(与现库格式一致):直接把文件内容转义入库
        esc = js.replace("\\", "\\\\").replace("'", "''").replace("\n", "\\n").replace("\t", "\\t").replace('"', '\\"')
        runner.run(
            f"INSERT INTO channelsdataprotocol (ShipId, WayUid, ChanType, ChanCode, DataProtocol, ChanDesc, JsonString) "
            f"VALUES ('1', '{ENGINE_WAYUID}', 7, {cc}, 2, '', '{esc}');")
        n = runner.run(f"SELECT LENGTH(JsonString) FROM channelsdataprotocol WHERE WayUid='{ENGINE_WAYUID}' AND ChanCode={cc};").strip()
        print(f"✅ channelsdataprotocol 通道{cc}({name}): JsonString {n} 字节")

    # 4. deviceinfo / topicinfo 重建(EngineData 下 AMS/LOAD/BTP)
    dev_rows = []
    if have["ams"]: dev_rows.append((1, "AMS", 1))
    if have["load"]: dev_rows.append((2, "LOAD", 1))
    if have["btp"]: dev_rows.append((3, "BTP", 3))
    runner.run(f"DELETE FROM deviceinfo WHERE GatewayId='{ENGINE_WAYUID}';")
    for cc, topic, slave in dev_rows:
        runner.run(f"INSERT INTO deviceinfo (GatewayId, ChanType, ChanCode, TopicName, SlaveId, DataProto) "
                   f"VALUES ('{ENGINE_WAYUID}', 7, {cc}, '{topic}', {slave}, 2);")
    print(f"✅ deviceinfo: EngineData 下重建 {len(dev_rows)} 通道")

    # topicinfo: 每通道 IndexSet = 该 topic 全部 KeyValue 逗号串
    for name, ok in have.items():
        if not ok: continue
        topic = {"ams":"AMS","load":"LOAD","btp":"BTP"}[name]
        vals = runner.run(f"SELECT KeyValue FROM hldkeymap WHERE TopicName='{topic}' ORDER BY orderkey;")
        keys = [l.strip() for l in vals.splitlines() if l.strip()]
        idx = ",".join(keys) + ","
        runner.run(f"DELETE FROM topicinfo WHERE TopicName='{topic}';")
        esc = idx.replace("'", "''")
        runner.run(f"INSERT INTO topicinfo (TopicName, TopicDesc, DataType, IndexSet) VALUES ('{topic}', 'Modbus RTU', NULL, '{esc}');")
        print(f"✅ topicinfo {topic}: {len(keys)} 点 IndexSet")

    # 5. 验证
    print("\n===== 落库验证 =====")
    total = runner.run("SELECT COUNT(*) FROM hldkeymap;").strip()
    ams_n = runner.run("SELECT COUNT(*) FROM hldkeymap WHERE TopicName='AMS';").strip()
    dup = runner.run("SELECT COUNT(*) FROM (SELECT orderkey FROM hldkeymap GROUP BY orderkey HAVING COUNT(*)>1) t;").strip()
    print(f"hldkeymap 总数={total} (预期含公共段41) | AMS={ams_n} | orderkey 重复={dup}")
    chans = runner.run(f"SELECT ChanCode, LENGTH(JsonString) FROM channelsdataprotocol WHERE WayUid='{ENGINE_WAYUID}';")
    print(f"channelsdataprotocol:\n{chans}")
    print("\n⚠️ 记得重启 datacap 加载新配置,然后看日志验证采集!")

if __name__ == "__main__":
    main()
