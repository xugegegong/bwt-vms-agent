#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
snapshot_iscu.py — 影子学习快照工具
对 iscu 全库 + vms_sys_db 工况点相关表 + datacap 配置文件做完整快照。
用法: python3 snapshot_iscu.py --tag 基线        (配置前)
      python3 snapshot_iscu.py --tag 步骤1_添加AMS通道   (每步操作后)
快照存 ./snapshots/<序号>_<tag>/ 目录。
"""
import argparse, hashlib, json, os, sys

try:
    import paramiko
except ImportError:
    sys.exit("需要 paramiko: pip install paramiko")

# 环境参数:★ 不存仓库,从环境变量读(缺则报错),支持命令行覆盖
#   BWT_DB_HOST / BWT_SSH_USER / BWT_SSH_PASSWORD / BWT_MYSQL_USER / BWT_MYSQL_PASSWORD
#   真实值向交付负责人获取,见 knowledge/凭据获取说明.md
DEFAULT = dict(host=os.getenv("BWT_DB_HOST", ""),
               user=os.getenv("BWT_SSH_USER", "root"),
               pw=os.getenv("BWT_SSH_PASSWORD", ""),
               container=os.getenv("BWT_MYSQL_CONTAINER", "mysql"),
               mysql_user=os.getenv("BWT_MYSQL_USER", "root"),
               mysql_pw=os.getenv("BWT_MYSQL_PASSWORD", ""))

# 快照范围:iscu 全库 + 配置工具可能联动的主库表
TABLES = {
    "iscu": ["ways", "deviceinfo", "channelsdataprotocol", "hldkeymap",
             "topicinfo", "dict", "registerdatatype"],
    "vms_sys_db": ["tab_engine_param", "tab_engine_param_menu",
                   "tab_engine_param_relationship", "tab_engine_state_quantity_cfg",
                   "tab_statistics_param", "tab_alarm_mapping"],
}
XML_FILES = ["/home/hlddatacap/config/LocalGateway.xml",
             "/home/hlddatacap/config/system_config.xml",
             "/home/hlddatacap/config/flowmeterconfig.xml"]


def run(ssh, cmd, timeout=120):
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    e = "\n".join(l for l in e.splitlines() if "Using a password" not in l)
    return o, e


def dump_table(ssh, env, db, table):
    inner = f"docker exec {env['container']} mysql -N -u{env['mysql_user']} -p{env['mysql_pw']} {db} -e \"SELECT * FROM {table}\""
    o, e = run(ssh, inner)
    if e.strip():
        return None, e
    return o, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="步骤名,如 基线/步骤1_添加AMS通道")
    ap.add_argument("--host", default=DEFAULT["host"])
    ap.add_argument("--user", default=DEFAULT["user"])
    ap.add_argument("--pw", default=DEFAULT["pw"])
    ap.add_argument("--container", default=DEFAULT["container"])
    ap.add_argument("--mysql_user", default=DEFAULT["mysql_user"])
    ap.add_argument("--mysql_pw", default=DEFAULT["mysql_pw"])
    a = ap.parse_args()
    env = dict(host=a.host, user=a.user, pw=a.pw, container=a.container,
               mysql_user=a.mysql_user, mysql_pw=a.mysql_pw)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(env["host"], username=env["user"], password=env["pw"], timeout=15)

    # 快照目录:自动序号
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")
    os.makedirs(base, exist_ok=True)
    seq = len([d for d in os.listdir(base) if not d.startswith(".")]) + 1
    snapdir = os.path.join(base, f"{seq:03d}_{a.tag}")
    os.makedirs(snapdir, exist_ok=True)

    summary = {"tag": a.tag, "tables": {}, "files": {}}
    for db, tables in TABLES.items():
        for t in tables:
            data, err = dump_table(ssh, env, db, t)
            if err:
                print(f"  [跳过] {db}.{t}: {err.strip()[:80]}")
                continue
            # channelsdataprotocol 的 JsonString 太大,存原文但摘要长度
            path = os.path.join(snapdir, f"{db}.{t}.tsv")
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            nrows = len([l for l in data.splitlines() if l.strip()])
            summary["tables"][f"{db}.{t}"] = {"rows": nrows, "md5": hashlib.md5(data.encode()).hexdigest()[:8]}
            print(f"  ✓ {db}.{t}: {nrows} 行")

    # XML 文件(带 hash,变了才知道)
    for xf in XML_FILES:
        o, e = run(ssh, f"cat {xf} 2>/dev/null || echo __MISSING__")
        if o.strip() == "__MISSING__":
            summary["files"][xf] = {"missing": True}
            continue
        path = os.path.join(snapdir, os.path.basename(xf))
        with open(path, "w", encoding="utf-8") as f:
            f.write(o)
        summary["files"][xf] = {"md5": hashlib.md5(o.encode()).hexdigest()[:8]}
        print(f"  ✓ {xf}")

    with open(os.path.join(snapdir, "_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"\n快照完成: {snapdir}")
    ssh.close()


if __name__ == "__main__":
    main()
