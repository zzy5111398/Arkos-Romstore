#!/bin/bash
# roms-fs-watch.sh —— /roms 只读的**运行期**监控（自愈脚本的补充）
#
# 为什么需要它
#   roms-fs-selfheal.sh 只在**开机**那一刻跑一次。但实测第三次事故（2026-10-12）
#   是这样发生的：开机时分区完全正常，界面和商店都能用，**运行了 42 分钟后**
#   目录表被读炸，内核 remount-ro：
#       [2521.048] exFAT-fs (mmcblk0p3): error, found bogus dentry(551) ...
#       [2521.060] exFAT-fs (mmcblk0p3): Filesystem has been set read-only
#   开机自愈完全没机会介入（journal 里一条记录都没有），只能等用户发现装不上游戏
#   再手工 SSH 排查一遍。这个服务补上运行期的缺口。
#
# 设计约束
#   1. **只读不改任何东西**。检测到 ro 就转交自愈脚本，自己不碰挂载。
#      两边职责分开：本脚本只负责「发现」，自愈脚本负责「修」。
#   2. **绝不阻塞开机**。Type=simple + 循环，systemd 立刻就绪。
#   3. **探针要便宜，更不能给要监控的分区添乱**。挂载标志检查每 60 秒做一次
#      （纯读，微秒级）；写探针**节流到 5 分钟一次**，因为 touch 会在 exFAT
#      根目录真的建+删文件，而本分区的事故根因正是 FAT 目录表截断。
#   4. **日志要克制**。状态不变时不打任何日志（否则一天 1440 条会把
#      journal 淹掉，真正的故障反而看不见）。
#   5. **触发后不重复触发**。自愈期间会一直是 ro，用退避避免疯狂重试。
#
# 手工排查：journalctl -u roms-fs-watch.service -n 50

set -u

ROMS="/roms"
HEAL="/opt/system/roms-fs-selfheal.sh"
INTERVAL="${WATCH_INTERVAL:-60}"

log() { logger -t roms-fs-watch -- "$*"; echo "[roms-fs-watch] $*"; }

# 已知处在「自愈处理中」的状态：交给脚本后就不再催，留足时间给 fsck
cooldown=0
# 写探针的节流计数。mount 表检查每轮都做（纯读，微秒级）；
# 但 touch 要真的在 exFAT 上建+删文件，见下面 PROBE_EVERY 的说明。
PROBE_EVERY=5
tick=0

while true; do
    sleep "$INTERVAL"

    # 没挂载不算故障（可能正在被别的东西重挂），下一轮再看
    mountpoint -q "$ROMS" 2>/dev/null || continue

    # 判据一：挂载选项表里 rw/ro 那一项
    #   ⚠️ 这里必须用 awk 取第 4 字段，**不要用正则去匹配 " on /roms .*ro"**。
    #      /proc/self/mounts 的格式是「设备 挂载点 类型 选项 0 0」，**没有括号**；
    #      只有 `mount` 命令的输出才是 " on /xxx type yyy (rw,...)"。
    #      按 `mount` 的格式写正则，在真机上会**永远命中不了**（0.5.0 开发时踩过）。
    #   用 awk 定位字段、cut 取第一项，只认「首选项正好是 ro」：
    #      - rw 状态下本分区 mount 行本来就带 errors=remount-ro，
    #        正则子串匹配会 100% 误判成故障，必须精确等于 ro 才算。
    is_ro=0
    _mnt_opt="$(awk -v m="$ROMS" '$2 == m { print $4; exit }' /proc/self/mounts 2>/dev/null)"
    if [ "${_mnt_opt%%,*}" = "ro" ]; then
        is_ro=1
    fi

    # 判据二：实际写不进去（mount 表偶尔会骗人，以行为准）。
    #   **每 PROBE_EVERY 轮才做一次**（默认 5 分钟）。这是刻意节流：
    #   touch 要在 exFAT 根目录真的创建+删除一个文件，一天 1440 次
    #   就是 1440 次目录项改写。而 /roms 三次事故的根因恰恰是
    #   **FAT 目录表截断** —— 监控自己给要监控的分区添风险，这不对。
    #   而判据一已经能覆盖内核 remount-ro 这个真实场景（内核改的是挂载标志），
    #   所以降频不会漏掉我们真正要防的故障。
    tick=$((tick + 1))
    if [ "$is_ro" -eq 0 ] && [ $((tick % PROBE_EVERY)) -eq 0 ]; then
        probe="$ROMS/.fswatch-$$"
        if ! touch "$probe" 2>/dev/null; then
            is_ro=1
        else
            rm -f "$probe" 2>/dev/null
        fi
    fi

    if [ "$is_ro" -eq 0 ]; then
        # 恢复正常。清一次退避即可，不必刷日志。
        cooldown=0
        continue
    fi

    # 还在冷却期就安静等着，别把 journal 刷爆
    if [ "$cooldown" -gt 0 ]; then
        cooldown=$((cooldown - 1))
        continue
    fi

    log "检测到 $ROMS 只读，转交自愈脚本修复"
    if [ -x "$HEAL" ]; then
        # 放后台跑：本脚本是 simple 服务，不能被 fsck 的 30 秒拖住
        setsid nohup "$HEAL" >/dev/null 2>&1 </dev/null &
        # 自愈 + 观察窗口大约 5 分钟
        cooldown=5
    else
        log "找不到可执行的 $HEAL，无法自动修复（请手工 SSH 排查）"
        # 脚本缺失时不要空转，退避更久（10 分钟）
        cooldown=10
    fi
done