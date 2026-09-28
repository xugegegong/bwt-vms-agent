#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diff_snapshots.py — 影子学习对比工具
对比两个快照,输出行级差异(新增/删除/修改),按表逐列对比。
用法: python3 diff_snapshots.py snapshots/001_基线 snapshots/002_步骤1
"""
import argparse, difflib, json, os, sys


def load_table(path):
    if path is None or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return [l for l in f.read().splitlines() if l.strip()]


def diff_tsv(old_path, new_path, name):
    old, new = load_table(old_path), load_table(new_path)
    if old is None and new is None:
        return None
    if old is None:
        return f"### {name}: 新表 {len(new)} 行"
    if new is None:
        return f"### {name}: 表被删除(原有 {len(old)} 行)"

    old_set, new_set = set(old), set(new)
    added = [l for l in new if l not in old_set]
    removed = [l for l in old if l not in new_set]

    # 修改 = 新增+删除同数量时尝试配对(简化:直接列出)
    out = []
    if added:
        out.append(f"### {name}: +{len(added)} 行")
        for l in added[:30]:  # 最多显示30行
            out.append(f"  + {l[:200]}")
        if len(added) > 30:
            out.append(f"  ...(共 {len(added)} 行)")
    if removed:
        out.append(f"### {name}: -{len(removed)} 行")
        for l in removed[:30]:
            out.append(f"  - {l[:200]}")
        if len(removed) > 30:
            out.append(f"  ...(共 {len(removed)} 行)")
    if not added and not removed:
        return None
    return "\n".join(out)


def diff_json_string(old_tsv, new_tsv):
    """对 channelsdataprotocol 的 JsonString 做语义 diff"""
    import re as _re
    def parse(tsv):
        rows = {}
        for l in tsv:
            parts = l.split("\t")
            if len(parts) >= 7 and parts[6].startswith("{"):
                rows[parts[2]] = parts[6]  # ChanCode -> JsonString
        return rows
    old, new = parse(old_tsv or []), parse(new_tsv or [])
    changes = []
    for k in sorted(set(old) | set(new)):
        if k not in old:
            changes.append(f"  通道{k}: 新增 JsonString({len(new[k])}字符)")
        elif k not in new:
            changes.append(f"  通道{k}: 删除 JsonString")
        elif old[k] != new[k]:
            # 解码 \n 转义后按行 diff
            o = old[k].replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
            n = new[k].replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
            diff = list(difflib.unified_diff(o.splitlines(), n.splitlines(), lineterm=""))
            changes.append(f"  通道{k}: JsonString 修改 ({len(old[k])}→{len(new[k])}字符)")
            for d in diff[2:25]:  # 跳过 header,最多25行
                changes.append(f"    {d[:150]}")
    return "\n".join(changes) if changes else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old_dir")
    ap.add_argument("new_dir")
    a = ap.parse_args()

    old_files = {f: os.path.join(a.old_dir, f) for f in os.listdir(a.old_dir) if f.endswith(".tsv")}
    new_files = {f: os.path.join(a.new_dir, f) for f in os.listdir(a.new_dir) if f.endswith(".tsv")}
    all_files = sorted(set(list(old_files.keys()) + list(new_files.keys())))

    results = []
    for f in all_files:
        r = diff_tsv(old_files.get(f), new_files.get(f), f.replace(".tsv", ""))
        if r:
            results.append(r)

    # JsonString 语义 diff
    csp = "iscu.channelsdataprotocol.tsv"
    if csp in old_files and csp in new_files:
        old_tsv = load_table(old_files[csp]) or []
        new_tsv = load_table(new_files[csp]) or []
        jd = diff_json_string(old_tsv, new_tsv)
        if jd:
            results.append("### channelsdataprotocol JsonString 语义变化:\n" + jd)

    # XML 变化
    for f in sorted(set(os.listdir(a.old_dir)) & set(os.listdir(a.new_dir))):
        if f.endswith(".xml"):
            o = open(os.path.join(a.old_dir, f), encoding="utf-8").read()
            n = open(os.path.join(a.new_dir, f), encoding="utf-8").read()
            if o != n:
                diff = list(difflib.unified_diff(o.splitlines(), n.splitlines(), lineterm=""))
                results.append(f"### {f} 变化:\n" + "\n".join(l[:150] for l in diff[:30]))

    if not results:
        print("两个快照之间无差异。")
    else:
        header = f"{'='*60}\n{a.old_dir} → {a.new_dir}\n{'='*60}"
        print(header + "\n\n" + "\n\n".join(results))


if __name__ == "__main__":
    main()
