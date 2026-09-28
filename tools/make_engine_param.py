#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_engine_param.py — S2: 生成 vms_sys_db 岸端工况点库表 SQL (S1 闸门通过后用)

输入:
  --device_map   S1 产出的 device_map.csv
                 (device_key,order_id,cn,en,unit,dev_type_suggest[,min_value,max_value])
  --menu_plan    菜单规划 CSV (menu_name,menu_name_en,identification,sort,keywords)
  --out          输出 SQL 文件
可选:
  --assign_csv   ★ 显式归类 CSV (device_key,identification)。给了就用它,不再靠关键词。
                 菜单归类是**业务判定**,关键词只能命中约 65%(实测),正式交付必须人工定。
  --menu_scheme  uuid(默认,同 XT GLORY) | shipseq(同远东吉祥)
                 uuid    : menu.device_id = 随机32位hex, 不设 param.device_father_id
                 shipseq : menu.device_id = f"{ship_id}{序号:04d}"(总览=0000), 并设
                           param.device_father_id = 该菜单的 device_id
  --nav_csv      公共导航段模板(默认同目录 navigation_segment.csv)
  --with_eim     附带 EIM 倾斜仪 2 点(orderkey 1577/1578)
产出(幂等,先 DELETE 本船再插):
  1. tab_engine_param  2. tab_engine_param_menu  3. tab_engine_param_relationship
"""
import argparse, csv, os, uuid

OVERVIEW = ("所有工况总览", "OVERVIEW", "OverviewOfAllWorking", 0)


def q(s):
    s = str(s or "").strip()
    return f"'{s.replace(chr(39), chr(39)*2)}'" if s else "NULL"


def num(s):
    s = str(s or "").strip()
    return s if s else "NULL"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device_map", required=True)
    ap.add_argument("--menu_plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ship_id", type=int, required=True)
    ap.add_argument("--assign_csv", default="")
    ap.add_argument("--menu_scheme", choices=["uuid", "shipseq"], default="uuid")
    ap.add_argument("--nav_csv",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "navigation_segment.csv"))
    ap.add_argument("--with_eim", action="store_true")
    a = ap.parse_args()

    pts = list(csv.DictReader(open(a.device_map, encoding="utf-8-sig")))
    print(f"device_map: {len(pts)} 业务点")

    nav = []
    if os.path.exists(a.nav_csv):
        nav = list(csv.DictReader(open(a.nav_csv, encoding="utf-8-sig")))
    eim = []
    if a.with_eim:
        eim = [{"orderkey": "1577", "keyvalue": "50001", "desc": "横倾角"},
               {"orderkey": "1578", "keyvalue": "50002", "desc": "纵倾角"}]

    menus = [m for m in csv.DictReader(open(a.menu_plan, encoding="utf-8-sig")) if m.get("menu_name")]
    # 菜单排序 + device_id(总览占序号 0,分类从 1 起)
    menus.sort(key=lambda m: int(m.get("sort") or 0))
    for i, m in enumerate(menus):
        m["menu_id"] = f"{a.ship_id}{i+1:02d}"
        m["uuid"] = (f"{a.ship_id}{i+1:04d}" if a.menu_scheme == "shipseq" else uuid.uuid4().hex)
        m["sort"] = m.get("sort") or str(i + 1)
    by_ident = {m["identification"]: m for m in menus}
    print(f"菜单规划: {len(menus)} 分类 (scheme={a.menu_scheme})")

    # 归类
    assign = {}
    if a.assign_csv:
        for r in csv.DictReader(open(a.assign_csv, encoding="utf-8-sig")):
            assign[r["device_key"]] = r["identification"]
        print(f"显式归类: {len(assign)} 条")
    unmatched = []
    for p in pts:
        m = by_ident.get(assign.get(p["device_key"], "")) if assign else None
        if m is None and not assign:
            for mm in menus:
                kws = [k.strip() for k in (mm.get("keywords") or "").split(";") if k.strip()]
                dk = [k.strip() for k in (mm.get("device_keys") or "").split(";") if k.strip()]
                if p["device_key"] in dk or any(k in p["cn"] for k in kws):
                    m = mm; break
        if m is None:
            unmatched.append(p["device_key"])
        p["menu"] = m
    fallback = next((m for m in menus if "其他" in m["menu_name"]), menus[-1] if menus else None)
    for p in pts:
        if p["menu"] is None:
            p["menu"] = fallback
    print(f"归类: 明确 {len(pts)-len(unmatched)}, 兜底[{fallback['menu_name'] if fallback else '?'}] {len(unmatched)}")

    # SQL
    L = [f"-- S2: vms_sys_db 岸端工况点库 (ship_id={a.ship_id}, 幂等)",
         f"-- 导航段{len(nav)} + EIM{len(eim)} + 业务点{len(pts)} = 全量 {len(nav)+len(eim)+len(pts)}",
         "SET @ship = %d;" % a.ship_id]
    for t in ("tab_engine_param_relationship", "tab_engine_param", "tab_engine_param_menu"):
        L.append(f"DELETE FROM {t} WHERE ship_id=@ship;")

    cols = ["ship_id", "device_key", "order_id", "device_name", "device_name_en",
            "device_id", "device_type", "unit", "status", "attention_level", "is_cycle"]
    has_range = any(p.get("min_value") or p.get("max_value") for p in pts)
    if has_range:
        cols += ["min_value", "max_value"]
    if a.menu_scheme == "shipseq":
        cols.append("device_father_id")
    L.append(f"INSERT INTO tab_engine_param ({', '.join(cols)}) VALUES")
    vals = []
    # ★ 通导段(公共导航段):英文名=中文名, attention_level=1, is_cycle=0
    #   业务点:英文名空, attention_level=NULL, is_cycle=NULL  (实船如此)
    for r in nav + eim:
        row = [f"@ship", q(r["keyvalue"]), str(int(r["orderkey"])), q(r["desc"]),
               q(r["desc"]), "NULL", "NULL", "NULL", "'1'", "1", "0"]
        if has_range: row += ["NULL", "NULL"]
        if a.menu_scheme == "shipseq": row.append("NULL")
        vals.append("(" + ", ".join(row) + ")")
    for p in pts:
        did = uuid.uuid4().hex
        p["device_id"] = did
        row = [f"@ship", q(p["device_key"]), str(int(p["order_id"])), q(p["cn"]),
               q(p.get("en")), f"'{did}'", q(p.get("dev_type_suggest")), q(p.get("unit")),
               "'1'", "NULL", "NULL"]
        if has_range: row += [num(p.get("min_value")), num(p.get("max_value"))]
        if a.menu_scheme == "shipseq":
            row.append(q(p["menu"]["uuid"]) if p["menu"] else "NULL")
        vals.append("(" + ", ".join(row) + ")")
    L.append(",\n".join(vals) + ";")

    # 菜单
    mc = ["menu_id", "ship_id", "menu_type", "device_name", "device_name_en", "device_id",
          "node_type", "device_father_id", "identification", "action_path", "resource_sort",
          "list_or_svg", "svg_old_new", "`key`", "status"]
    L.append(f"INSERT INTO tab_engine_param_menu ({', '.join(mc)}) VALUES")
    mv = []
    if a.menu_scheme == "shipseq":
        mv.append(f"({a.ship_id}00, @ship, '1', {q(OVERVIEW[0])}, {q(OVERVIEW[1])}, "
                  f"'{a.ship_id}0000', '0', '0', {q(OVERVIEW[2])}, '/engineMonitor', "
                  f"'102020100', '0', 0, 1, '1')")
    else:
        mv.append(f"({a.ship_id}00, @ship, '1', {q(OVERVIEW[0])}, {q(OVERVIEW[1])}, "
                  f"'{uuid.uuid4().hex}', '0', '0', {q(OVERVIEW[2])}, '/engineMonitor', "
                  f"'0', '0', 0, 1, '1')")
    for i, m in enumerate(menus):
        # resource_sort 实船规律: 10202{key:02d}00  (key: 总览=1, 第一个分类=2 ...)
        rs = f"10202{i+2:02d}00"
        # action_path 优先用规划里给的(前端路由,不是 identification 的机械变形);缺省才拼
        path = m.get("action_path") or ("/engineMonitor/" + m["identification"])
        mv.append(f"({m['menu_id']}, @ship, '1', {q(m['menu_name'])}, {q(m.get('menu_name_en'))}, "
                  f"'{m['uuid']}', '0', '0', {q(m['identification'])}, "
                  f"{q(path)}, {q(str(rs))}, '0', 2, {i+2}, '1')")
    L.append(",\n".join(mv) + ";")

    # 关系
    L.append("INSERT INTO tab_engine_param_relationship (ship_id, menu_device_id, engine_device_id, status) VALUES")
    rv = [f"(@ship, '{p['menu']['uuid']}', '{p['device_id']}', '1')" for p in pts if p["menu"]]
    L.append(",\n".join(rv) + ";")

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"✅ {a.out} ({os.path.getsize(a.out)//1024}KB): "
          f"param {len(vals)} + menu {len(mv)} + relationship {len(rv)}")


if __name__ == "__main__":
    main()
