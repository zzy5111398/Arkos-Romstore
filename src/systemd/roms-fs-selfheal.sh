#!/bin/bash
# roms-fs-selfheal.sh —— /roms (exFAT) 只读自愈
#
# 背景
#   /roms 是 exFAT 挂载，带 errors=remount-ro。TF 卡非正常关机（掉电/拔卡）
#   会截断 FAT 表，开机后首次遍历目录即报 bogus dentry，内核把整盘转只读：
#       exFAT-fs (mmcblk0p3): error, found bogus dentry(...) beyond unused empty group(...)
#       exFAT-fs (mmcblk0p3): Filesystem has been set read-only
#   表现是 ROM 商店所有写操作抛 errno 30 (EROFS)，装游戏装不上。
#
#   本脚本在开机阶段检测到只读就自动 umount + fsck.exfat -y + remount，
#   免去手工 SSH 排查。fsck 约 30 秒。
#
# 设计约束（重要，改动前先读）
#   1. **绝不能阻塞开机**。修不好就放弃，让 ES 照常启动（只是不能写 ROM），
#      而不是让机器卡在黑屏。所有步骤都有超时与兜底，脚本永远返回 0。
#   2. **只读检测要在 umount 之前**。一旦 umount，mount 表里就没有 ro 标记了。
#      判据：/roms 已挂载 + mount 输出含 "(ro," + 仍可列目录。
#   3. **必须先停占用者**。emulationstation / filebrowser 会持有 /roms，
#      不停就 umount 失败（EBUSY）。
#   4. **两个挂载点成对处理**。/opt/system/Tools 是独立 bind mount，
#      漏掉它会导致 umount /roms 失败。
#   5. **只做 exFAT 的修复**。ext4 用 e2fsck，不同参数，绝不能搞混。
#   6. 修复后用 systemctl restart roms.mount 让 systemd 重新拉起挂载，
#      这样 /opt/system/Tools 的 bind 和依赖它的服务会由 systemd 自己接回去。
#
# 日志：journalctl -u roms-fs-selfheal.service -n 50

set -u

LOG_TAG="roms-fs-selfheal"
DEV="/dev/mmcblk0p3"
ROMS="/roms"
TOOLS_MNT="/opt/system/Tools"
LOCK="/run/roms-fs-selfheal.lock"

log()  { logger -t "$LOG_TAG" -- "$*"; echo "[$LOG_TAG] $*"; }
warn() { logger -t "$LOG_TAG" -p user.warning -- "$*"; echo "[$LOG_TAG] WARN: $*"; }

# 并发保护：多个触发源（开机 + 手动）同时跑会互相 umount 打架
exec 9>"$LOCK" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then
    flock -n 9 || { log "已有实例在运行，跳过"; exit 0; }
fi

# ---------------------------------------------------------------- 1. 状态判定
# 没挂载：可能是启动时序还没到，也可能是上次异常掉电导致没挂上。
# 前者直接退出（等 systemd 自己挂），后者尝试补挂 —— 两种都很快返回。
if ! mountpoint -q "$ROMS" 2>/dev/null; then
    if [ -b "$DEV" ] || [ -e "$DEV" ]; then
        log "$ROMS 未挂载，尝试补挂"
        timeout 30 mount "$ROMS" 2>/dev/null || log "$ROMS 补挂失败，交给 systemd 处理"
    else
        log "$ROMS 未挂载且 $DEV 不存在，跳过"
    fi
    exit 0
fi

# 挂载选项里没有 ro 就是正常的，直接退出（99% 的开机走这条路，几毫秒返回）
if ! mount | grep -E "[[:space:]]${ROMS}[[:space:]]" | grep -q '(ro,'; then
    log "$ROMS 读写正常，无需修复"
    exit 0
fi

warn "$ROMS 处于只读状态，开始自动修复"

# 顺手留一份故障快照，便于事后判断损坏规模
if command -v dmesg >/dev/null 2>&1; then
    dmesg 2>/dev/null | grep -iE 'exfat|mmcblk0p3' | tail -20 > /var/log/roms-fs-selfheal-last.log 2>/dev/null || true
fi

# ---------------------------------------------------------------- 2. 停占用者
for svc in emulationstation filebrowser; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        log "停止 $svc"
        timeout 20 systemctl stop "$svc" >/dev/null 2>&1 || true
    fi
done
sleep 2

# ---------------------------------------------------------------- 3. 卸载（成对）
# /opt/system/Tools 是 bind mount，必须先于 /roms 卸
if mountpoint -q "$TOOLS_MNT" 2>/dev/null; then
    if ! timeout 15 umount "$TOOLS_MNT" 2>/dev/null; then
        warn "卸载 $TOOLS_MNT 失败，尝试 lazy umount"
        timeout 10 umount -l "$TOOLS_MNT" 2>/dev/null || true
    fi
fi

if ! mountpoint -q "$ROMS" 2>/dev/null; then
    warn "$ROMS 已意外卸载，直接进入修复"
else
    if ! timeout 30 umount "$ROMS" 2>/dev/null; then
        warn "卸载 $ROMS 失败（可能仍有进程占用），尝试 lazy umount"
        timeout 10 umount -l "$ROMS" 2>/dev/null || true
    fi
fi

sleep 1

# 卸载失败就别硬来了 —— 强 fsck 正在挂载的分区比不修更危险
if mountpoint -q "$ROMS" 2>/dev/null; then
    warn "$ROMS 仍处于挂载状态，出于安全放弃修复。请 SSH 手工执行："
    warn "  systemctl stop emulationstation filebrowser && umount $ROMS && fsck.exfat -y $DEV"
    exit 0
fi

# ---------------------------------------------------------------- 4. fsck
if ! command -v fsck.exfat >/dev/null 2>&1; then
    warn "系统没有 fsck.exfat，无法自动修复"
    exit 0
fi

log "运行 fsck.exfat -y $DEV（约 30 秒）"
FSCK_OUT="$(timeout 600 fsck.exfat -y "$DEV" 2>&1)"
FSCK_RC=$?
echo "$FSCK_OUT" | grep -vE '^ERROR: cluster .* is not allocated' | tail -12 | while read -r l; do log "  fsck: $l"; done

if [ "$FSCK_RC" -ne 0 ]; then
    warn "fsck 返回非 0（$FSCK_RC），继续尝试挂载"
fi

sync

# ---------------------------------------------------------------- 5. 重新挂载
# 用 systemctl 而不是 umount/mount 裸命令：让 systemd 感知挂载状态变化，
# 自动把 /opt/system/Tools 的 bind mount 和依赖服务接回去。
log "重新挂载 $ROMS"
if ! timeout 60 systemctl restart roms.mount 2>/dev/null; then
    warn "systemctl restart roms.mount 失败，回退到裸 mount"
    timeout 30 mount "$ROMS" 2>/dev/null || warn "裸 mount 也失败了"
fi

sleep 2

# Tools 是 bind mount，systemd 通常会跟着起；没起就手动补
if ! mountpoint -q "$TOOLS_MNT" 2>/dev/null; then
    if [ -d "$ROMS/tools" ] || [ -d "$ROMS/Tools" ]; then
        log "补挂 $TOOLS_MNT"
        timeout 30 mount "$TOOLS_MNT" 2>/dev/null || true
    fi
fi

# ---------------------------------------------------------------- 6. 验证
RO_STATE="$(mount | grep -E "[[:space:]]${ROMS}[[:space:]]" | head -1)"
if echo "$RO_STATE" | grep -q '(rw,'; then
    # 真正写一次才算数 —— mount 说 rw 不代表 superblock 接受了写入
    if touch "$ROMS/.selfheal-write-test" 2>/dev/null; then
        rm -f "$ROMS/.selfheal-write-test" 2>/dev/null
        log "修复成功：$ROMS 已恢复读写"
    else
        warn "$ROMS 显示 rw 但实际写入仍失败，可能是卡本身已损坏，请更换 TF 卡"
    fi
else
    warn "$ROMS 仍为只读，修复未成功。当前挂载：${RO_STATE:-未挂载}"
    warn "若反复失败，说明 TF 卡已到寿命末期，建议换卡"
fi

# ---------------------------------------------------------------- 7. 恢复服务
log "恢复服务"
timeout 60 systemctl start emulationstation >/dev/null 2>&1 || true
systemctl start filebrowser >/dev/null 2>&1 || true

# ES 已在运行时不做重启（它只开机读一次 gamelist），交给下次开机生效
log "自愈流程结束"
exit 0