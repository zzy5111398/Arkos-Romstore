#!/bin/bash
# =============================================================================
#  ROM 商店 —— 卸载
#
#  用法：
#     sudo ./uninstall.sh                # 卸掉商店与 NAS 挂载服务，保留配置与索引
#     sudo ./uninstall.sh --keep-mount   # 只卸商店，保留 /mnt/Roms 的挂载服务
#     sudo ./uninstall.sh --purge        # 连程序目录（索引/配置/日志/备份）一起删
#     sudo ./uninstall.sh --check        # 只列出会被删的东西，不真删
# =============================================================================

set -u

HERE=$(cd "$(dirname "$0")" && pwd)

TARGET_USER=""
ES_DIR="/opt/system"
KEEP_MOUNT=0
PURGE=0
CHECK_ONLY=0

STORE_DIR=""
TARGET_HOME=""
TARGET_UID=""
TARGET_GID=""

MOUNT_SCRIPT="/usr/local/sbin/nas-roms-mount.sh"
UNIT="/etc/systemd/system/nas-roms.service"
HEAL_SH="/opt/system/roms-fs-selfheal.sh"
HEAL_UNIT="/etc/systemd/system/roms-fs-selfheal.service"
WATCH_SH="/opt/system/roms-fs-watch.sh"
WATCH_UNIT="/etc/systemd/system/roms-fs-watch.service"
DISPATCHER="/etc/NetworkManager/dispatcher.d/50-nas-roms"
MOUNT_POINT="/mnt/Roms"
ES_GUARD="/etc/systemd/system/emulationstation.service.d/10-crash-limit.conf"

if [ -t 1 ]; then
    C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'
    C_0=$'\033[0m'; C_BOLD=$'\033[1m'
else
    C_R=""; C_G=""; C_Y=""; C_0=""; C_BOLD=""
fi
say()   { printf '%s\n' "$*"; }
ok()    { printf '  %s✔%s %s\n' "$C_G" "$C_0" "$*"; }
warn()  { printf '  %s!%s %s\n' "$C_Y" "$C_0" "$*"; }
skip()  { printf '  · %s（不存在，跳过）\n' "$*"; }
head_() { printf '\n%s── %s ──%s\n' "$C_BOLD" "$*" "$C_0"; }
die()   { printf '\n%s卸载中止：%s%s\n' "$C_R" "$*" "$C_0"; exit 1; }

usage() {
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --target-user) TARGET_USER="${2:-}"; shift 2 ;;
        --es-dir)      ES_DIR="${2:-}";      shift 2 ;;
        --keep-mount)  KEEP_MOUNT=1; shift ;;
        --purge)       PURGE=1;      shift ;;
        --check)       CHECK_ONLY=1; shift ;;
        -h|--help)     usage ;;
        *) die "未知参数：$1（用 --help 看用法）" ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        exec sudo -- "$0" "$@"
    fi
    die "请用 root 运行：sudo $0"
fi

# ----------------------------------------------------------------- 目标用户
if [ -z "$TARGET_USER" ]; then
    if id ark >/dev/null 2>&1; then
        TARGET_USER="ark"
    else
        for d in /home/*; do
            [ -d "$d" ] || continue
            n=$(basename "$d")
            if id "$n" >/dev/null 2>&1 && [ -d "$d/.romstore" ]; then
                TARGET_USER="$n"; break
            fi
        done
    fi
fi
[ -n "$TARGET_USER" ] || die "探测不到程序归属用户，请用 --target-user 指定"
id "$TARGET_USER" >/dev/null 2>&1 || die "用户 $TARGET_USER 不存在"
TARGET_HOME=$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)
[ -n "$TARGET_HOME" ] || TARGET_HOME=$(eval echo "~$TARGET_USER")
TARGET_UID=$(id -u "$TARGET_USER")
TARGET_GID=$(id -g "$TARGET_USER")
STORE_DIR="$TARGET_HOME/.romstore"

say ""
say "${C_BOLD}ROM 商店 卸载${C_0}"
say ""
say "  目标用户  $TARGET_USER ($TARGET_UID:$TARGET_GID)"
say "  程序目录  $STORE_DIR"
if [ "$PURGE" = "1" ]; then
    say "  ${C_R}--purge：程序目录（含索引 / source.json / 日志 / 备份）会被删除${C_0}"
else
    say "  （程序目录保留；要删干净加 --purge）"
fi
if [ "$KEEP_MOUNT" = "1" ]; then
    say "  --keep-mount：/mnt/Roms 挂载服务会被保留"
else
    say "  /mnt/Roms 的挂载服务与挂载点会被一并清理"
fi

if [ "$CHECK_ONLY" = "1" ]; then
    head_ "会被删除的内容"
    for f in "$UNIT" "$DISPATCHER" "$MOUNT_SCRIPT" "$ES_DIR/RomStore.sh" "$ES_DIR/VideoPlayer.sh" "$ES_GUARD" "$HEAL_UNIT" "$HEAL_SH" "$WATCH_UNIT" "$WATCH_SH"; do
        [ -e "$f" ] && say "  删 $f" || say "  · $f（不存在）"
    done
    if [ "$PURGE" = "1" ]; then
        [ -d "$STORE_DIR" ] && say "  删 $STORE_DIR（整目录）"
    else
        say "  保留 $STORE_DIR"
    fi
    [ "$KEEP_MOUNT" = "1" ] || say "  卸载并删除挂载点 $MOUNT_POINT"
    say ""
    say "去掉 --check 才会真正执行。"
    exit 0
fi

# ----------------------------------------------------------------- 1. 停服务
head_ "① 停掉挂载服务"
if systemctl list-unit-files 2>/dev/null | grep -q '^nas-roms\.service'; then
    systemctl stop nas-roms.service 2>/dev/null && ok "已停止 nas-roms.service" \
                                                 || warn "停止失败（可能本来就未运行）"
    systemctl disable nas-roms.service >/dev/null 2>&1 && ok "已取消开机自启" \
                                                        || warn "disable 失败"
else
    skip "nas-roms.service"
fi

# ----------------------------------------------------------------- 2. 撤入口
head_ "② 撤销 EmulationStation 入口"
if [ -d "$STORE_DIR" ] && [ -f "$STORE_DIR/run.py" ]; then
    if sudo -u "$TARGET_USER" -H \
        env HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" \
            PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        python3 -m romstore.cli --unregister >/dev/null 2>&1; then
        ok "已从 gamelist.xml 移除「ROM 商店」条目"
    else
        warn "自动撤销失败，请手动删 $ES_DIR/gamelist.xml 里的 ./RomStore.sh 条目"
    fi
else
    warn "程序目录已不在，跳过自动撤销（如仍有条目请手动删）"
fi

if [ -e "$ES_DIR/RomStore.sh" ]; then
    rm -f "$ES_DIR/RomStore.sh" && ok "已删除 $ES_DIR/RomStore.sh"
else
    skip "$ES_DIR/RomStore.sh"
fi

# 旧版的独立「视频播放器」入口：0.4.2 早期装过，后来改成在商店详情页里播放。
# 保留这段清理逻辑 —— 用老版本装过的人，卸载时也得能卸干净。
if [ -e "$ES_DIR/VideoPlayer.sh" ]; then
    rm -f "$ES_DIR/VideoPlayer.sh" && ok "已删除 $ES_DIR/VideoPlayer.sh"
else
    skip "$ES_DIR/VideoPlayer.sh"
fi

# ----------------------------------------------------------------- 3. 挂载服务
if [ "$KEEP_MOUNT" = "1" ]; then
    head_ "③ 挂载服务"
    say "  --keep-mount：保留 $MOUNT_SCRIPT 与 $UNIT"
    say "  （注：$HEAL_UNIT / $HEAL_SH / $WATCH_UNIT / $WATCH_SH 属于机器维护，始终会清理）"
else
    head_ "③ 移除挂载服务"
    if [ -e "$MOUNT_SCRIPT.orig-bak" ]; then
        cp -p "$MOUNT_SCRIPT.orig-bak" "$MOUNT_SCRIPT" 2>/dev/null \
            && ok "已还原改造前的挂载脚本（$MOUNT_SCRIPT.orig-bak -> $MOUNT_SCRIPT）"
    elif [ -e "$MOUNT_SCRIPT" ]; then
        rm -f "$MOUNT_SCRIPT" && ok "已删除 $MOUNT_SCRIPT"
    else
        skip "$MOUNT_SCRIPT"
    fi

    if [ -e "$UNIT" ]; then
        rm -f "$UNIT" && ok "已删除 $UNIT"
        [ -d "$UNIT.d" ] && true
    else
        skip "$UNIT"
    fi

    if [ -e "$DISPATCHER" ]; then
        rm -f "$DISPATCHER" && ok "已删除 $DISPATCHER"
    else
        skip "$DISPATCHER"
    fi

    # 只读自愈：属于机器维护而非 ROM 商店本体，即使 --keep-mount 也要清掉
    if [ -e "$HEAL_UNIT" ] || [ -e "$HEAL_SH" ]; then
        systemctl disable roms-fs-selfheal.service >/dev/null 2>&1 || true
        rm -f "$HEAL_UNIT" "$HEAL_SH"
        ok "已删除 /roms 只读自愈"
        systemctl daemon-reload >/dev/null 2>&1 || true
    else
        skip "$HEAL_UNIT"
    fi
    systemctl daemon-reload

    if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        umount "$MOUNT_POINT" 2>/dev/null || umount -l "$MOUNT_POINT" 2>/dev/null
        if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
            warn "$MOUNT_POINT 仍被占用，未能卸载"
        else
            ok "已卸载 $MOUNT_POINT"
        fi
    else
        skip "$MOUNT_POINT（未挂载）"
    fi
    rmdir "$MOUNT_POINT" 2>/dev/null && ok "已删除空目录 $MOUNT_POINT"
fi

# ----------------------------------------------------------------- 4. ES 保护
if [ -e "$ES_GUARD" ]; then
    head_ "④ ES 崩溃循环保护"
    rm -f "$ES_GUARD" && ok "已删除 $ES_GUARD"
    rmdir /etc/systemd/system/emulationstation.service.d 2>/dev/null
    systemctl daemon-reload
fi

# ----------------------------------------------------------------- 5. 程序目录
head_ "⑤ 程序目录"
if [ "$PURGE" = "1" ]; then
    if [ -d "$STORE_DIR" ]; then
        say "  即将删除：$STORE_DIR"
        du -sh "$STORE_DIR" 2>/dev/null | sed 's/^/    /'
        rm -rf "$STORE_DIR" && ok "已删除 $STORE_DIR"
    else
        skip "$STORE_DIR"
    fi
else
    say "  保留 $STORE_DIR（含 source.json / index.db / 备份）"
    say "  想彻底删：sudo $0 --purge"
fi

# ----------------------------------------------------------------- 总结
head_ "卸载完成"

# 卸载后自检：确认真的干净了，避免「以为卸了其实还在」
LEFT=0
for f in "$UNIT" "$DISPATCHER" "$MOUNT_SCRIPT" "$ES_DIR/RomStore.sh" "$ES_DIR/VideoPlayer.sh" "$HEAL_UNIT" "$HEAL_SH" "$WATCH_UNIT" "$WATCH_SH"; do
    if [ -e "$f" ]; then
        printf '  %s✘%s 残留：%s\n' "$C_R" "$C_0" "$f"
        LEFT=1
    fi
done
if [ -f "$ES_DIR/gamelist.xml" ] && grep -q 'RomStore\.sh' "$ES_DIR/gamelist.xml" 2>/dev/null; then
    printf '  %s✘%s 残留：%s 里仍有「ROM 商店」条目\n' "$C_R" "$C_0" "$ES_DIR/gamelist.xml"
    LEFT=1
fi
if [ -f "$ES_DIR/gamelist.xml" ] && grep -q 'VideoPlayer\.sh' "$ES_DIR/gamelist.xml" 2>/dev/null; then
    printf '  %s✘%s 残留：%s 里仍有「视频播放器」条目\n' "$C_R" "$C_0" "$ES_DIR/gamelist.xml"
    LEFT=1
fi
if [ "$KEEP_MOUNT" != "1" ] && mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    printf '  %s✘%s 残留：%s 仍处于挂载状态\n' "$C_R" "$C_0" "$MOUNT_POINT"
    LEFT=1
fi
[ "$LEFT" = "0" ] && printf '  %s✔%s 已清理干净（无残留文件/挂载/菜单条目）\n' "$C_G" "$C_0"

say ""
say "  已移除：ES 入口、入口脚本$([ "$KEEP_MOUNT" = "1" ] || echo "、挂载服务")"
if [ "$PURGE" = "1" ]; then
    say "  已删除：程序目录"
else
    say "  仍保留：$STORE_DIR"
fi
say ""
say "  ${C_Y}已装/已下载的游戏文件不受影响${C_0}（它们在 /roms 下，本脚本不碰）"
say ""
