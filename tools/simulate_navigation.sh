#!/bin/bash
# simulate_navigation.sh — 通导模拟数据注入(142 测试环境)
# 用途:直接往 Redis other 写通导工况点值,触发 realTimeData 的 parseNavigation 链路
# 前提:142 上 tab_statistics_param 已配好导航信号路由(device_type 对应计程仪/罗经等)
# 用法: bash simulate_navigation.sh [shipId]  (默认 35701001)
SHIP_ID=${1:-35701001}
# Redis 密码不写死在仓库里（向交付负责人获取，见 knowledge/凭据获取说明.md）
REDIS_PW="${BWT_REDIS_PASSWORD:?请先设置环境变量 BWT_REDIS_PASSWORD(Redis 密码)}"

echo ">>> 模拟通导数据 ship_id=$SHIP_ID (每30秒刷新, Ctrl+C 停止)"
echo ">>> 注:此脚本只写 other key;parseNavigation 由 cleanv2 收到新消息时触发,"
echo ">>> 若直接写 Redis 不触发链路,改用方案B(Kafka 注入)"

while true; do
  # 读取现有 other,合并通导值(不覆盖 AMS 数据)
  OLD=$(redis-cli -a $REDIS_PW GET "list:ship:$SHIP_ID:other" 2>/dev/null | sed 's/^"//;s/"$//')

  # 如果 other 为空,用最小 JSON
  if [ -z "$OLD" ] || [ "$OLD" = "(nil)" ]; then
    NEW='{"1101001":"31.2304","1101002":"121.4737","1101022":"85.0","1101025":"8.5","1104007":"18.5","1105001":"173.0","1105004":"0.3","1106001":"120.0","1106002":"6.5","1106003":"6.8","1106004":"130.0","1107001":"8.2","1107003":"0.1","1102001":"AIS","1103001":"8.4","1103002":"86.0","1103003":"173.0","1103004":"0.2","1103007":"563338500","1103008":"0","1103009":"31.2304","1103010":"121.4737","1103011":"8.4"}'
  else
    # 用 python 合并(保留原有 AMS 键)
    NEW=$(echo "$OLD" | python3 -c "
import json,sys
d = json.load(sys.stdin)
nav = {
  '1101001':'31.2304','1101002':'121.4737',  # GPS 纬度/经度
  '1101022':'85.0','1101025':'8.5',           # GPS COG/SOG
  '1104007':'18.5',                            # 测深
  '1105001':'173.0','1105004':'0.3',          # GYRO 艏向/回转率
  '1106001':'120.0','1106002':'6.5',          # 风向/风速
  '1106003':'6.8','1106004':'130.0',          # 真风速/真风向
  '1107001':'8.2','1107003':'0.1',            # 计程仪 STW/横向
  '1102001':'AIS',
  '1103001':'8.4','1103002':'86.0','1103003':'173.0','1103004':'0.2',
  '1103007':'563338500','1103008':'0','1103009':'31.2304','1103010':'121.4737',
  '1103011':'8.4'
}
d.update(nav)
print(json.dumps(d,separators=(',',':')))
" 2>/dev/null)
    [ -z "$NEW" ] && NEW="$OLD"
  fi

  redis-cli -a $REDIS_PW SET "list:ship:$SHIP_ID:other" "$NEW" > /dev/null 2>&1

  # 值缓慢变化(模拟船在动)
  echo "$(date '+%H:%M:%S') 已注入 (艏向173° 航速8.2kn 位置31.23,121.47)"
  sleep 30
done
