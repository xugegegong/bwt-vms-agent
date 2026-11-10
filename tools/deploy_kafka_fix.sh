#!/bin/bash
# deploy_kafka_fix.sh — 老算法环境(船端) Kafka 实时链路修复 一键部署脚本
# 兼容 Ubuntu(测试环境) / RHEL CentOS 8(现场)
#
# 解决问题: 消费进程"活着但内部卡死"导致实时数据中断,现场只能人工清Kafka+重启恢复
# 修复内容(两件):
#   ① LAG 看门狗: 每分钟检查 makeup 消费组位点,生产端有数据而位点连续5分钟不动
#      → 自动重启消费容器(实验环境实测:故障→自愈5分钟,零人工)
#   ② broker retention 15分钟 → 24小时(消费端可停更久,不会因停摆>15min丢位点上下文)
#
# 用法:  bash deploy_kafka_fix.sh              # 部署(可重复执行,幂等)
#        bash deploy_kafka_fix.sh rollback     # 回滚全部改动
#        bash deploy_kafka_fix.sh status       # 只查看当前状态,不改任何东西
#
# 前提: root 执行;docker 已装;kafka 容器在跑;老算法栈(datareceive-analysis 在消费 makeup)
# 注意: 部署过程会重启一次 kafka 容器(改 broker 配置必须重启才生效),
#       期间约1~3分钟数据中断,消费进程会自动重连(实验已验证),无需人工干预
#
# 定时机制: 优先 /etc/cron.d(Ubuntu的cron/CentOS的cronie都支持,不依赖用户crontab);
#           cron 服务不可用时自动降级为 setsid 后台循环守护
set -u

RETENTION_MINUTES=1440          # 24小时
KAFKA_DIR=/opt/kafka/kafka_2.12-2.1.1     # 现场/测试环境统一的 kafka 安装目录(不做自动探测)
WATCHDOG_PATH=$KAFKA_DIR/kafka_lag_watchdog.sh
LOOP_PATH=$KAFKA_DIR/kafka_lag_watchdog_loop.sh
CROND_FILE=/etc/cron.d/kafka_lag_watchdog    # 注意: cron.d 文件名不能带点号
STATE=/var/tmp/kafka_watchdog.state
LOG=/var/log/kafka_watchdog.log
LOCK=/var/tmp/kafka_watchdog.lock
PIDFILE=/var/tmp/kafka_watchdog_loop.pid
CONSUMER_GROUP=makeup_data
STALL_LIMIT=5                    # 连续停滞分钟数阈值
SERVER_PROPS_BAK=/tmp/server.properties.bak.$(date +%s)

log()  { echo -e "\033[32m[deploy]\033[0m $*"; }
warn() { echo -e "\033[33m[警告]\033[0m $*" >&2; }
die()  { echo -e "\033[31m[失败]\033[0m $*" >&2; exit 1; }

# ---------- 自动探测环境 ----------
detect_env() {
    KAFKA_CTN=$(docker ps --format '{{.Names}}' | grep -m1 -ix 'kafka')
    [ -z "$KAFKA_CTN" ] && KAFKA_CTN=$(docker ps --format '{{.Names}}' | grep -m1 -i kafka)
    [ -n "$KAFKA_CTN" ] || die "找不到 kafka 容器(docker ps 里没有名字带 kafka 的)"
    log "kafka 容器: $KAFKA_CTN"

    CONSUMER_CTN=$(docker ps --format '{{.Names}}' | grep -m1 'datareceive-analysis')
    [ -z "$CONSUMER_CTN" ] && CONSUMER_CTN=$(docker ps --format '{{.Names}}' | grep -m1 'datareceive')
    [ -n "$CONSUMER_CTN" ] || die "找不到 datareceive 消费容器"
    log "消费容器(看门狗守护对象): $CONSUMER_CTN"

    KAFKA_BIN=$(docker exec "$KAFKA_CTN" sh -c 'ls -d /opt/kafka/kafka_*/bin 2>/dev/null' | head -1)
    [ -n "$KAFKA_BIN" ] || die "容器内找不到 /opt/kafka/kafka_*/bin"
    log "kafka 工具目录: $KAFKA_CTN:$KAFKA_BIN"

    SERVER_PROPS=$(docker exec "$KAFKA_CTN" sh -c 'ls /opt/kafka/kafka_*/config/server.properties 2>/dev/null' | head -1)
    [ -n "$SERVER_PROPS" ] || die "容器内找不到 server.properties"
    log "server.properties: $KAFKA_CTN:$SERVER_PROPS (当前 retention: $(docker exec "$KAFKA_CTN" grep -E '^log.retention.minutes' "$SERVER_PROPS" 2>/dev/null || echo 未配置=默认168h))"

    # 宿主机侧 kafka 目录(现场统一路径,不做探测)
    [ -d "$KAFKA_DIR" ] || die "宿主机不存在 $KAFKA_DIR(看门狗的安装目录)"

    # 磁盘体检: 24h retention 每船约 700MB/天,预留不足要提醒
    AVAIL_GB=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
    log "磁盘可用: ${AVAIL_GB}G"
    [ "${AVAIL_GB:-0}" -lt 10 ] && warn "磁盘可用不足10G,retention放大后请密切关注磁盘占用"
}

# ---------- ① 部署看门狗 ----------
install_watchdog() {
    log "部署看门狗 -> $WATCHDOG_PATH"
    cat > "$WATCHDOG_PATH" << WDEOF
#!/bin/bash
# kafka_lag_watchdog.sh — 数据流看门狗(deploy_kafka_fix.sh 生成)
# 逻辑: 不盯进程死活(容器restart:always已管),盯消费位点头;
#       生产端有新数据 而 消费位点连续${STALL_LIMIT}分钟不动 => 重启消费容器
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GROUP=${CONSUMER_GROUP}
CONSUMER_CONTAINER=${CONSUMER_CTN}
STATE=${STATE}
LOG=${LOG}
STALL_LIMIT=${STALL_LIMIT}
KBIN=${KAFKA_BIN}

# flock 防止上一轮没跑完又叠加
exec 9>${LOCK}
flock -n 9 || exit 0

now="\$(date '+%F %T')"
out=""
for i in 1 2 3; do
    out=\$(docker exec ${KAFKA_CTN} timeout 45 \$KBIN/kafka-consumer-groups.sh \\
        --bootstrap-server localhost:9092 --describe --group "\$GROUP" 2>/dev/null | \\
        awk '\$1 ~ /data-org-makeup/ {cur+=\$3; end+=\$4} END {print cur, end}')
    [ -n "\$out" ] && break
    sleep 2
done
if [ -z "\$out" ] || [ "\$out" = "  " ]; then
    echo "\$now describe查询失败或超时,跳过本轮" >> \$LOG
    exit 0
fi
cur=\$(echo \$out | awk '{print \$1}')
end=\$(echo \$out | awk '{print \$2}')

prev=\$(cat \$STATE 2>/dev/null)
prev_cur=\$(echo \"\$prev\" | awk '{print \$1}')
prev_end=\$(echo \"\$prev\" | awk '{print \$2}')
stall=\$(echo \"\$prev\" | awk '{print \$3}')
[ -z "\$stall" ] && stall=0

if [ "\$end" == "\$prev_end" ]; then
    stall=0            # 生产端没新数据,不算卡死(无数据环境正常)
elif [ "\$cur" == "\$prev_cur" ]; then
    stall=\$((stall + 1))
else
    stall=0
fi
echo "\$cur \$end \$stall" > \$STATE
echo "\$now cur=\$cur end=\$end stall=\$stall" >> \$LOG

if [ "\$stall" -ge "\$STALL_LIMIT" ]; then
    echo "\$now !!! 重启 \$CONSUMER_CONTAINER (位点停滞\${stall}分钟, LAG=\$((end-cur)))" >> \$LOG
    docker restart "\$CONSUMER_CONTAINER" >> \$LOG 2>&1
    echo "0 0 0" > \$STATE
fi
WDEOF
    chmod +x "$WATCHDOG_PATH"
    bash -n "$WATCHDOG_PATH" || die "看门狗脚本语法错误"

    # 清理旧版位置(看门狗早期装在 /opt/ 下,换位置后避免双跑)
    [ "$WATCHDOG_PATH" != "/opt/kafka_lag_watchdog.sh" ] && rm -f /opt/kafka_lag_watchdog.sh /opt/kafka_lag_watchdog_loop.sh

    # 清理可能存在的旧版用户级 crontab 安装(按脚本名匹配,不区分路径,避免双跑)
    if command -v crontab >/dev/null 2>&1; then
        (crontab -l 2>/dev/null | grep -v 'kafka_lag_watchdog') | crontab - 2>/dev/null || true
    fi

    # ---- 定时: 优先 /etc/cron.d ----
    local timed="cron"
    if command -v systemctl >/dev/null 2>&1; then
        if ! systemctl is-active crond >/dev/null 2>&1 && ! systemctl is-active cron >/dev/null 2>&1; then
            systemctl start crond >/dev/null 2>&1 || systemctl start cron >/dev/null 2>&1 || true
        fi
    fi
    if pgrep -x crond >/dev/null 2>&1 || pgrep -x cron >/dev/null 2>&1; then
        mkdir -p /etc/cron.d
        printf 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n* * * * * root %s\n' "$WATCHDOG_PATH" > "$CROND_FILE"
        chmod 644 "$CROND_FILE"
        log "定时已装: $CROND_FILE (cron 服务运行中)"
    else
        # ---- 降级: setsid 后台循环 ----
        timed="loop"
        cat > "$LOOP_PATH" << LEOF
#!/bin/bash
while true; do ${WATCHDOG_PATH}; sleep 60; done
LEOF
        chmod +x "$LOOP_PATH"
        # 停旧循环(如有)
        [ -f "$PIDFILE" ] && kill "$(cat $PIDFILE)" 2>/dev/null
        setsid "$LOOP_PATH" >> /var/log/kafka_watchdog_loop.out 2>&1 < /dev/null &
        echo $! > "$PIDFILE"
        log "cron 服务不可用,已降级为后台循环守护(pid=$(cat $PIDFILE)),重启机器后需重新执行本部署脚本"
    fi

    # 手动跑一轮验证
    bash "$WATCHDOG_PATH"
    log "首轮自检: $(cat $STATE 2>/dev/null)  (cur=已消费位点 end=最新位点 stall=停滞计数)"
}

# ---------- ② 修改 retention 并重启 kafka ----------
set_retention() {
    docker exec "$KAFKA_CTN" cp "$SERVER_PROPS" "$SERVER_PROPS_BAK"
    CUR=$(docker exec "$KAFKA_CTN" grep -c "^log.retention.minutes" "$SERVER_PROPS" || true)
    if [ "${CUR:-0}" -ge 1 ]; then
        docker exec "$KAFKA_CTN" sed -i "s/^log.retention.minutes=.*/log.retention.minutes=$RETENTION_MINUTES/" "$SERVER_PROPS"
    else
        docker exec "$KAFKA_CTN" sh -c "echo 'log.retention.minutes=$RETENTION_MINUTES' >> $SERVER_PROPS"
    fi
    log "retention 已改为 $RETENTION_MINUTES 分钟(备份: $SERVER_PROPS_BAK)"

    log "重启 kafka 使配置生效(约1~3分钟,期间数据中断,消费者会自动重连)..."
    docker restart "$KAFKA_CTN" >/dev/null

    # 等待 broker 恢复(GetOffsetShell 是该版本最稳的探针)
    local ok=""
    for i in $(seq 1 40); do
        sleep 5
        r=$(docker exec "$KAFKA_CTN" timeout 20 "$KAFKA_BIN/kafka-run-class.sh" \
            kafka.tools.GetOffsetShell --broker-list localhost:9092 --topic __consumer_offsets --time -1 2>/dev/null)
        [ -n "$r" ] && ok=1 && break
    done
    [ -n "$ok" ] || die "kafka 重启后 200 秒内未恢复,请人工检查 docker logs $KAFKA_CTN"
    log "kafka 已恢复(耗时约 $((i*5)) 秒)"
}

# ---------- 状态查看 / 回滚 ----------
show_status() {
    echo "== 看门狗 =="
    [ -f "$WATCHDOG_PATH" ] && echo "已安装: $WATCHDOG_PATH" || echo "未安装"
    if [ -f "$CROND_FILE" ]; then echo "定时: $CROND_FILE"; cat "$CROND_FILE"; fi
    [ -f "$PIDFILE" ] && echo "定时: 后台循环(pid=$(cat $PIDFILE))"
    [ -f "$LOG" ] && tail -3 "$LOG"
    echo "== kafka retention =="
    docker exec "$KAFKA_CTN" grep -E '^log.retention.(minutes|bytes)' "$SERVER_PROPS" 2>/dev/null || echo "未配置(默认168h)"
    df -BG / | tail -1
}

rollback() {
    log "回滚: 移除定时"
    rm -f "$CROND_FILE"
    if [ -f "$PIDFILE" ]; then kill "$(cat $PIDFILE)" 2>/dev/null; rm -f "$PIDFILE"; fi
    rm -f "$WATCHDOG_PATH" "$LOOP_PATH" "$STATE" "$LOCK"
    rm -f /opt/kafka_lag_watchdog.sh /opt/kafka_lag_watchdog_loop.sh   # 旧版位置兜底清理
    if command -v crontab >/dev/null 2>&1; then
        (crontab -l 2>/dev/null | grep -v 'kafka_lag_watchdog') | crontab - 2>/dev/null || true
    fi
    log "回滚: 恢复 server.properties"
    LATEST_BAK=$(ls -t /tmp/server.properties.bak.* 2>/dev/null | head -1)
    if [ -n "$LATEST_BAK" ]; then
        docker cp "$LATEST_BAK" "$KAFKA_CTN:$SERVER_PROPS"
        docker restart "$KAFKA_CTN" >/dev/null && log "kafka 已用备份配置重启,回滚完成"
    else
        warn "没找到配置备份,仅回滚了看门狗;retention 请手动改回"
    fi
}

case "${1:-deploy}" in
    deploy)
        detect_env
        install_watchdog
        set_retention
        echo
        log "部署完成。日常观察: tail -f $LOG"
        log "看门狗每次自愈会记录一行 '!!! 重启';位点不动${STALL_LIMIT}分钟即触发"
        log "回滚命令: bash $0 rollback"
        ;;
    rollback) detect_env >/dev/null 2>&1; rollback ;;
    status)   detect_env >/dev/null 2>&1; show_status ;;
    *) echo "用法: bash $0 [deploy|rollback|status]"; exit 1 ;;
esac
