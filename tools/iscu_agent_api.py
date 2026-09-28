#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BWT ISCU Agent API

只读 verify 服务。Dify 通过 HTTP 调用本服务,本服务再 SSH 到 7.61 查询 iscu。
无第三方依赖,Python 3 标准库即可运行。

环境变量:
  BWT_SSH_HOST      SSH 主机,默认 192.168.7.61
  BWT_SSH_PORT      SSH 端口,默认 22
  BWT_SSH_USER      SSH 用户,默认 root
  BWT_SSH_KEY       SSH 私钥绝对路径,默认 /root/.ssh/id_rsa
  BWT_MYSQL_CONTAINER  MySQL 容器名,默认 mysql
  BWT_MYSQL_USER    MySQL 用户,默认 root
  BWT_MYSQL_PASSWORD MySQL 密码(★必填,不设默认;真实值向交付负责人获取)
  BWT_BIND_HOST     监听地址,默认 0.0.0.0
  BWT_BIND_PORT     监听端口,默认 8000
"""

import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse


ENV = {
    "ssh_host": os.getenv("BWT_SSH_HOST", "192.168.7.61"),
    "ssh_port": os.getenv("BWT_SSH_PORT", "22"),
    "ssh_user": os.getenv("BWT_SSH_USER", "root"),
    "ssh_key": os.getenv("BWT_SSH_KEY", "/root/.ssh/id_rsa"),
    "mysql_container": os.getenv("BWT_MYSQL_CONTAINER", "mysql"),
    "mysql_user": os.getenv("BWT_MYSQL_USER", "root"),
    "mysql_password": os.getenv("BWT_MYSQL_PASSWORD", ""),
    "bind_host": os.getenv("BWT_BIND_HOST", "0.0.0.0"),
    "bind_port": int(os.getenv("BWT_BIND_PORT", "8000")),
}


def valid_int(value):
    try:
        number = int(value)
    except Exception:
        return None
    return number if number > 0 else None


def valid_name(value, default=""):
    text = str(value or "").strip()
    if not text:
        return default
    if re.fullmatch(r"[A-Za-z0-9_.-]+", text):
        return text
    return None


def run_ssh(remote_command):
    ssh_cmd = [
        "ssh",
        "-F",
        "/dev/null",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout=10",
        "-p",
        str(ENV["ssh_port"]),
    ]
    key = ENV["ssh_key"].strip()
    if key:
        ssh_cmd += ["-i", key]
    ssh_cmd.append(f"{ENV['ssh_user']}@{ENV['ssh_host']}")
    ssh_cmd.append(remote_command)

    completed = subprocess.run(
        ssh_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        text=True,
    )
    return completed


def mysql_sql(sql):
    remote = (
        f"docker exec {ENV['mysql_container']} mysql "
        f"-u{ENV['mysql_user']} -p{ENV['mysql_password']} "
        f"-N -B --default-character-set=utf8 -e \"{sql}\""
    )
    completed = run_ssh(remote)
    if completed.returncode != 0:
        return None, completed.stderr.strip()
    lines = [line.rstrip("\n") for line in completed.stdout.splitlines() if line.strip()]
    rows = [line.split("\t") for line in lines]
    return rows, ""


def verify_topic(ship_id, client_no, topic_name, expected_json_md5, expected_txt_md5):
    result = {
        "request_ship_id": ship_id,
        "ship_id": ship_id,
        "client_no": client_no,
        "topic_name": topic_name,
        "checks": [],
        "iscu_ship_id_note": "iscu 库内部 ShipId 固定为 1,不代表业务船号,不要与 request_ship_id 比较",
    }

    # channelsdataprotocol
    sql = (
        "SELECT ShipId,WayUid,ChanType,ChanCode,DataProtocol,"
        "CHAR_LENGTH(JsonString),MD5(JsonString),JSON_VALID(JsonString) "
        f"FROM iscu.channelsdataprotocol WHERE ChanCode={client_no}"
    )
    rows, err = mysql_sql(sql)
    if err:
        result["channelsdataprotocol"] = {"error": err}
        result["checks"].append({"name": "channelsdataprotocol", "passed": False, "message": err})
    elif not rows:
        result["channelsdataprotocol"] = {"found": False}
        result["checks"].append({"name": "channelsdataprotocol", "passed": False, "message": "未找到通道"})
    else:
        row = rows[0]
        payload = {
            "iscu_ship_id": row[0],
            "way_uid": row[1],
            "chan_type": row[2],
            "chan_code": row[3],
            "data_protocol": row[4],
            "json_length": int(row[5]),
            "json_md5": row[6],
            "json_valid": row[7] == "1",
        }
        if expected_json_md5:
            payload["json_md5_match"] = row[6].lower() == expected_json_md5.lower()
        result["channelsdataprotocol"] = payload
        result["checks"].append({
            "name": "channelsdataprotocol",
            "passed": payload.get("json_valid", False),
            "message": "通道存在" if payload.get("json_valid") else "JsonString 不是合法 JSON",
        })

    # deviceinfo
    sql = (
        "SELECT GatewayId,ChanType,ChanCode,TopicName,SlaveId,DataProto "
        f"FROM iscu.deviceinfo WHERE TopicName='{topic_name}'"
    )
    rows, err = mysql_sql(sql)
    if err:
        result["deviceinfo"] = {"error": err}
        result["checks"].append({"name": "deviceinfo", "passed": False, "message": err})
    elif not rows:
        result["deviceinfo"] = {"found": False}
        result["checks"].append({"name": "deviceinfo", "passed": False, "message": "未找到 TopicName"})
    else:
        row = rows[0]
        result["deviceinfo"] = {
            "gateway_id": row[0],
            "chan_type": row[1],
            "chan_code": row[2],
            "topic_name": row[3],
            "slave_id": row[4],
            "data_proto": row[5],
        }
        result["checks"].append({"name": "deviceinfo", "passed": True, "message": "TopicName 存在"})

    # topicinfo
    sql = (
        "SELECT id,TopicName,TopicDesc,CHAR_LENGTH(IndexSet),MD5(IndexSet) "
        f"FROM iscu.topicinfo WHERE TopicName='{topic_name}'"
    )
    rows, err = mysql_sql(sql)
    if err:
        result["topicinfo"] = {"error": err}
        result["checks"].append({"name": "topicinfo", "passed": False, "message": err})
    elif not rows:
        result["topicinfo"] = {"found": False}
        result["checks"].append({"name": "topicinfo", "passed": False, "message": "未找到 TopicName"})
    else:
        row = rows[0]
        payload = {
            "id": row[0],
            "topic_name": row[1],
            "topic_desc": row[2],
            "index_set_length": int(row[3]),
            "index_set_md5": row[4],
        }
        if expected_txt_md5:
            payload["index_set_md5_match"] = row[4].lower() == expected_txt_md5.lower()
        result["topicinfo"] = payload
        result["checks"].append({"name": "topicinfo", "passed": True, "message": "TopicName 存在"})

    # hldkeymap
    sql = (
        "SELECT COUNT(1),MIN(orderkey),MAX(orderkey),"
        "COUNT(DISTINCT KeyValue),COUNT(1)-COUNT(DISTINCT orderkey) "
        f"FROM iscu.hldkeymap WHERE TopicName='{topic_name}'"
    )
    rows, err = mysql_sql(sql)
    if err:
        result["hldkeymap"] = {"error": err}
        result["checks"].append({"name": "hldkeymap", "passed": False, "message": err})
    elif not rows:
        result["hldkeymap"] = {"found": False}
        result["checks"].append({"name": "hldkeymap", "passed": False, "message": "未找到点表"})
    else:
        row = rows[0]
        result["hldkeymap"] = {
            "point_count": int(row[0]),
            "orderkey_min": int(row[1]),
            "orderkey_max": int(row[2]),
            "key_value_unique_count": int(row[3]),
            "duplicate_orderkey": int(row[4]) > 0,
        }
        result["checks"].append({
            "name": "hldkeymap",
            "passed": int(row[4]) == 0,
            "message": "orderkey 无重复" if int(row[4]) == 0 else "orderkey 存在重复",
        })

    result["ok"] = all(item.get("passed") for item in result["checks"])
    return result


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, {"ok": True, "service": "BWT ISCU Agent API"})
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/iscu/verify":
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "请求不是合法 JSON"})
            return

        client_no = valid_int(data.get("client_no"))
        if client_no is None:
            self._send_json(400, {"ok": False, "error": "client_no 缺失或非法"})
            return

        topic_name = valid_name(data.get("topic_name"), f"AMS{client_no}")
        if topic_name is None:
            self._send_json(400, {"ok": False, "error": "topic_name 非法"})
            return

        ship_id = str(data.get("ship_id") or "").strip()
        expected_json_md5 = str(data.get("expected_json_md5") or "").strip()
        expected_txt_md5 = str(data.get("expected_txt_md5") or "").strip()

        try:
            payload = verify_topic(
                ship_id,
                client_no,
                topic_name,
                expected_json_md5,
                expected_txt_md5,
            )
            self._send_json(200, payload)
        except Exception as exc:
            self._send_json(500, {
                "ok": False,
                "error": "verify 执行失败",
                "detail": str(exc),
            })


def main():
    server = HTTPServer((ENV["bind_host"], ENV["bind_port"]), Handler)
    print(f"BWT ISCU Agent API listening on {ENV['bind_host']}:{ENV['bind_port']}")
    server.serve_forever()


if __name__ == "__main__":
    main()
