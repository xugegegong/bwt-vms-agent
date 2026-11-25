#!/bin/bash
# deploy_kafka_fix.sh — 老算法环境(船端) Kafka实时链路 + nacos 修复 一键部署脚本
# 兼容 Ubuntu(测试环境) / RHEL CentOS 8(现场)
#
# 解决问题:
#   ① 消费进程"活着但内部卡死"导致实时数据中断,现场只能人工清Kafka+重启恢复
#   ② nacos 1.1.4 偶尔半死(健康检查正常但配置接口不响应),system连不上nacos,
#      必须人工先重启nacos再重启system才恢复
# 修复内容:
#   ① LAG看门狗: makeup消费组位点停滞5分钟 → 自动重启消费容器
#   ② broker retention 15分钟 → 24小时
#   ③ nacos看门狗(合并在同一看门狗脚本): 每分钟探测真实配置接口(健康检查会骗人),
#      连续3次失败 → 自动重启nacos → 等readiness真通过 → 等60秒验证system自愈
#      (127实测:nacos恢复后system约100秒自动重连重注册,无需重启system,只有没回来才兜底重启)
#
# 用法:  bash deploy_kafka_fix.sh              # 部署(可重复执行,幂等)
#        bash deploy_kafka_fix.sh rollback     # 回滚全部改动
#        bash deploy_kafka_fix.sh status       # 只查看当前状态,不改任何东西
#        bash deploy_kafka_fix.sh reset        # 消费位点跳到最新(不删数据,安全的"重新开始")
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

# ---- nacos 看门狗参数 ----
# 探针用真实配置接口(/v1/cs/configs 在 nacos.security.ignore.urls 内,免鉴权),
# 不用健康检查端点(1.1.4 健康检查只验JVM,验不出半死)
NACOS_PROBE_URL="http://nacos.hbluewhale.com:8848/nacos/v1/cs/configs?dataId=datasource.yml&group=DEFAULT_GROUP&tenant=b49aa71c-0566-43dc-a6a3-ad765e39a376"
NACOS_FAIL_LIMIT=3               # 连续失败次数阈值(分钟)
NACOS_COOLDOWN_SEC=1800          # 重启后冷却30分钟,防重启风暴
NACOS_STATE=/var/tmp/nacos_watchdog.state
NACOS_COOLDOWN=/var/tmp/nacos_watchdog.cooldown

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

    # nacos 容器(看门狗第二守护对象)
    NACOS_CTN=$(docker ps --format '{{.Names}}' | grep -m1 -ix 'nacos')
    [ -z "$NACOS_CTN" ] && NACOS_CTN=$(docker ps --format '{{.Names}}' | grep -m1 -i nacos)
    [ -n "$NACOS_CTN" ] || die "找不到 nacos 容器"
    log "nacos 容器(看门狗守护对象): $NACOS_CTN"
    log "nacos 探针: $(timeout 8 curl -s -m 5 -o /dev/null -w '%{http_code}' "$NACOS_PROBE_URL" 2>/dev/null || echo 超时)  (期望200)"

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
# kafka_lag_watchdog.sh — 服务看门狗 v3(deploy_kafka_fix.sh 生成)
# ① kafka: 生产端水位在涨 而 消费位点连续${STALL_LIMIT}分钟不动/查不到 => 重启消费容器
# ② nacos: 真实配置接口连续${NACOS_FAIL_LIMIT}次失败 => 重启nacos,等ready后重启依赖方
# 设计原则: 盯真实业务接口,不盯进程死活(容器restart:always已管);
#           健康检查端点会骗人(1.1.4半死时健康检查照样通过),探针必须走配置接口
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
GROUP=${CONSUMER_GROUP}
CONSUMER_CONTAINER=${CONSUMER_CTN}
STATE=${STATE}
LOG=${LOG}
STALL_LIMIT=${STALL_LIMIT}
KBIN=${KAFKA_BIN}
KCTN=${KAFKA_CTN}
NACOS_CTN=${NACOS_CTN}
NPROBE="${NACOS_PROBE_URL}"
NFAIL_LIMIT=${NACOS_FAIL_LIMIT}
NCOOLDOWN_SEC=${NACOS_COOLDOWN_SEC}
NSTATE=${NACOS_STATE}
NCOOLDOWN=${NACOS_COOLDOWN}

# flock 防止上一轮没跑完又叠加
exec 9>${LOCK}
flock -n 9 || exit 0

now="\$(date '+%F %T')"

check_kafka() {
    # ① 生产端水位:所有 makeup topic 的最新位点之和(与消费组状态无关)
    topics=\$(docker exec \$KCTN timeout 30 \$KBIN/kafka-topics.sh --list --zookeeper localhost:2181 2>/dev/null | grep 'data-org-makeup\$')
    end=0
    for t in \$topics; do
        r=""
        for i in 1 2; do
            r=\$(docker exec \$KCTN timeout 30 \$KBIN/kafka-run-class.sh kafka.tools.GetOffsetShell \\
                --broker-list localhost:9092 --topic "\$t" --time -1 2>/dev/null | awk -F: '{s+=\$NF} END {print s+0}')
            [ -n "\$r" ] && break
            sleep 2
        done
        end=\$((end + \${r:-0}))
    done

    # ② 消费位点:消费组已提交位点之和(0=组位点已过期被删或从未提交)
    cur=""
    for i in 1 2 3; do
        cur=\$(docker exec \$KCTN timeout 45 \$KBIN/kafka-consumer-groups.sh \\
            --bootstrap-server localhost:9092 --describe --group "\$GROUP" 2>/dev/null | \\
            awk '\$1 ~ /data-org-makeup/ {c+=\$3} END {print c+0}')
        [ -n "\$cur" ] && break
        sleep 2
    done
    if [ -z "\$cur" ]; then
        echo "\$now describe查询失败或超时,跳过本轮(生产端水位end=\$end)" >> \$LOG
        return 0
    fi

    prev=\$(cat \$STATE 2>/dev/null)
    prev_cur=\$(echo "\$prev" | awk '{print \$1}')
    prev_end=\$(echo "\$prev" | awk '{print \$2}')
    stall=\$(echo "\$prev" | awk '{print \$3}')
    [ -z "\$stall" ] && stall=0

    if [ "\$end" == "\$prev_end" ]; then
        stall=0            # 生产端没新数据,不算卡死(无数据环境正常)
    elif [ "\$cur" == "\$prev_cur" ]; then
        stall=\$((stall + 1))   # 位点不动,或组位点已被删(0==0),都算停滞
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
}

check_nacos() {
    NCODE=\$(curl -s -m 5 -o /dev/null -w '%{http_code}' "\$NPROBE" 2>/dev/null)
    NFAIL=\$(cat \$NSTATE 2>/dev/null)
    [ -z "\$NFAIL" ] && NFAIL=0
    COOL=\$(cat \$NCOOLDOWN 2>/dev/null)
    [ -z "\$COOL" ] && COOL=0

    if [ "\$NCODE" == "200" ]; then
        [ "\$NFAIL" -gt 0 ] && echo "\$now [nacos] 探针恢复200" >> \$LOG
        echo 0 > \$NSTATE
        return 0
    fi

    echo "\$now [nacos] 探针失败<\${NCODE:-超时}>,连续失败=\$NFAIL" >> \$LOG
    if [ "\$(date +%s)" -lt "\$COOL" ]; then
        echo "\$now [nacos] 冷却期内(剩 \$(( (COOL - \$(date +%s)) / 60 ))分钟),不动作" >> \$LOG
        return 0
    fi
    NFAIL=\$((NFAIL + 1))
    echo "\$NFAIL" > \$NSTATE
    if [ "\$NFAIL" -ge "\$NFAIL_LIMIT" ]; then
        echo "\$now !!! [nacos] 配置接口连续\${NFAIL}次失败,重启 \$NACOS_CTN" >> \$LOG
        docker restart "\$NACOS_CTN" >> \$LOG 2>&1
        ready=0
        for i in \$(seq 1 60); do
            sleep 2
            c=\$(curl -s -m 3 -o /dev/null -w '%{http_code}' "\$NPROBE" 2>/dev/null)
            [ "\$c" == "200" ] && ready=1 && break
        done
        echo \$(( \$(date +%s) + NCOOLDOWN_SEC )) > \$NCOOLDOWN
        echo 0 > \$NSTATE
        if [ "\$ready" == "1" ]; then
            # 2026-11-25 127实测: nacos恢复后system的nacos-client会自动重连重注册(约100秒),
            # gateway路由自动恢复,无需重启system(重启一次要十几分钟)。先等60秒探注册状态,
            # 只有确实没回来才兜底重启。
            echo "\$now [nacos] 已恢复(约 \$((i*2)) 秒),等待60秒验证依赖方自愈..." >> \$LOG
            sleep 60
            inst=\$(curl -s -m 5 "http://nacos.hbluewhale.com:8848/nacos/v1/ns/instance/list?serviceName=bwt-vms-system&namespaceId=b49aa71c-0566-43dc-a6a3-ad765e39a376" 2>/dev/null)
            if echo "\$inst" | grep -q '"healthy":true'; then
                echo "\$now [nacos] system 已自愈(重新注册healthy),跳过重启" >> \$LOG
            else
                echo "\$now !!! [nacos] system 未重新注册,兜底重启 system" >> \$LOG
                docker restart system >> \$LOG 2>&1
            fi
        else
            echo "\$now !!! [nacos] 重启后120秒探针仍未恢复,请人工检查" >> \$LOG
        fi
    fi
}

check_kafka
check_nacos
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
    log "首轮自检: kafka位点=$(cat $STATE 2>/dev/null)  (cur=已消费 end=最新 stall=停滞计数)"
    log "首轮自检: nacos探针=$(timeout 8 curl -s -m 5 -o /dev/null -w '%{http_code}' "$NACOS_PROBE_URL" 2>/dev/null || echo 超时)  (期望200)"
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
    log "broker 已恢复(耗时约 $((i*5)) 秒)"

    # 继续等组协调器就绪:光看broker能答offset不够(2026-11-25现场:脚本结束后system
    # 立刻刷 'Group coordinator ... id:2147483647 unavailable' 和
    # 'This server does not host this topic-partition' 共2~3分钟)。
    # 判据:describe 任一消费组能在30秒内返回(组不存在也是有效响应,超时才算未就绪)
    local coord_ok=""
    for j in $(seq 1 12); do
        cg=$(docker exec "$KAFKA_CTN" timeout 30 "$KAFKA_BIN/kafka-consumer-groups.sh" \
            --bootstrap-server localhost:9092 --describe --group "$CONSUMER_GROUP" 2>&1)
        if [ -n "$cg" ]; then coord_ok=1; break; fi
        sleep 5
    done
    if [ -n "$coord_ok" ]; then
        log "组协调器已就绪(追加约 $((j*5-5)) 秒),消费者重连风暴应不再出现"
    else
        warn "组协调器 60 秒内仍不可用,system 等消费者可能短暂报 coordinator 错误,通常数分钟自愈"
    fi
}

# ---------- 位点重置(不删数据的安全"重新开始") ----------
reset_offsets() {
    log "重置模式:停消费者 → 位点跳到最新 → 重启。不删除任何 Kafka 数据"
    docker stop "$CONSUMER_CTN" >/dev/null
    log "等待消费组会话超时(约40秒,组内成员掉线后 reset 才被允许)..."
    sleep 40
    local topics
    topics=$(docker exec "$KAFKA_CTN" timeout 30 "$KAFKA_BIN/kafka-topics.sh" --list --zookeeper localhost:2181 2>/dev/null | grep 'data-org-makeup$')
    if [ -n "$topics" ]; then
        for t in $topics; do
            for i in 1 2 3; do
                out=$(docker exec "$KAFKA_CTN" timeout 45 "$KAFKA_BIN/kafka-consumer-groups.sh" \
                    --bootstrap-server localhost:9092 --group "$CONSUMER_GROUP" --topic "$t" \
                    --reset-offsets --to-latest --execute 2>&1)
                if echo "$out" | grep -q "NEW-OFFSET"; then
                    log "已重置 $t → 最新位点"
                    break
                fi
                sleep 15
            done
        done
    else
        warn "没有 makeup topic(可能从未有数据),仅重启消费者"
    fi
    docker start "$CONSUMER_CTN" >/dev/null
    rm -f "$STATE"
    log "完成:消费者从最新数据开始消费;历史数据未删除,保留窗口内数据仍可查"
}

# ---------- 状态查看 / 回滚 ----------
show_status() {
    echo "== 看门狗 =="
    [ -f "$WATCHDOG_PATH" ] && echo "已安装: $WATCHDOG_PATH" || echo "未安装"
    if [ -f "$CROND_FILE" ]; then echo "定时: $CROND_FILE"; cat "$CROND_FILE"; fi
    [ -f "$PIDFILE" ] && echo "定时: 后台循环(pid=$(cat $PIDFILE))"
    [ -f "$LOG" ] && tail -5 "$LOG"
    echo "== kafka retention =="
    docker exec "$KAFKA_CTN" grep -E '^log.retention.(minutes|bytes)' "$SERVER_PROPS" 2>/dev/null || echo "未配置(默认168h)"
    echo "== nacos 探针 =="
    echo "HTTP $(timeout 8 curl -s -m 5 -o /dev/null -w '%{http_code}' "$NACOS_PROBE_URL" 2>/dev/null || echo 超时)  (期望200)  冷却状态: $(cat $NACOS_COOLDOWN 2>/dev/null || echo 无)"
    df -BG / | tail -1
}

rollback() {
    log "回滚: 移除定时"
    rm -f "$CROND_FILE"
    if [ -f "$PIDFILE" ]; then kill "$(cat $PIDFILE)" 2>/dev/null; rm -f "$PIDFILE"; fi
    rm -f "$WATCHDOG_PATH" "$LOOP_PATH" "$STATE" "$LOCK" "$NACOS_STATE" "$NACOS_COOLDOWN"
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
        # kafka 重启可能触发过期消费组清理(停摆>24h的组会被删),顺手重启消费者
        # 清除历史卡死状态;新组按 earliest 从保留窗口起点开始消费
        log "重启消费容器 $CONSUMER_CTN(清除可能的卡死状态,立即恢复消费)"
        docker restart "$CONSUMER_CTN" >/dev/null 2>&1 || true
        rm -f "$STATE"
        echo
        log "部署完成。日常观察: tail -f $LOG"
        log "看门狗每次自愈会记录一行 '!!! 重启';位点不动${STALL_LIMIT}分钟即触发"
        log "回滚命令: bash $0 rollback"
        ;;
    rollback) detect_env >/dev/null 2>&1; rollback ;;
    status)   detect_env >/dev/null 2>&1; show_status ;;
    reset)    detect_env; reset_offsets ;;
    *) echo "用法: bash $0 [deploy|rollback|status|reset]"; exit 1 ;;
esac
