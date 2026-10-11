#!/bin/bash
# ---------------------------------------------------------------
#  ROM 商店 —— 局域网 NAS 一键下载安装到掌机
#
#  由 EmulationStation 的 Options 系统调用。
#  入口路径：@@ES_DIR@@/RomStore.sh（位于 rootfs，切换 SD 卡不影响）
#
#  本文件里的 @@STORE_DIR@@ / @@ES_DIR@@ 由 install.sh 在安装时替换成实机路径，
#  安装后不应残留任何占位符（install.sh 会检查）。
#
#  与固件其它工具（351Files / Wifi-Toggle 等）的差别：
#    它们用 dialog（控制台 UI）+ gptokeyb（手柄→键盘）；
#    本程序是 SDL2 图形界面，直接读手柄事件，因此不需要 gptokeyb，
#    也就能正常显示中文（控制台字体最大仅 14.6 KB，无 CJK 字形）。
# ---------------------------------------------------------------
CURR_TTY="/dev/tty1"
APP_DIR="@@STORE_DIR@@"
LOG="$APP_DIR/log/run.log"

if [ "$(id -u)" -ne 0 ]; then
    exec sudo -- "$0" "$@"
fi

mkdir -p "$APP_DIR/log" 2>/dev/null

CHILD=""

# 关键：必须连子进程一起收掉。
# 否则只杀本脚本而 python 仍在跑，会有一只手柄/显示被占住的孤儿进程。
cleanup() {
    if [ -n "$CHILD" ] && kill -0 "$CHILD" 2>/dev/null; then
        kill -TERM "$CHILD" 2>/dev/null
        sleep 1
        kill -KILL "$CHILD" 2>/dev/null
    fi
    printf "\033c" > "$CURR_TTY" 2>/dev/null
    printf "\e[?25h" > "$CURR_TTY" 2>/dev/null
    exit 0
}
trap cleanup EXIT INT TERM HUP

# NAS 没挂上就补挂一次（幂等，带 flock）
if ! mountpoint -q /mnt/Roms 2>/dev/null; then
    /usr/local/sbin/nas-roms-mount.sh >/dev/null 2>&1 || true
fi

# SDL 需要手柄映射库才能认出 R36S 的 Gamepad
export SDL_GAMECONTROLLERCONFIG_FILE="/opt/inttools/gamecontrollerdb.txt"
export SDL_VIDEODRIVER="kmsdrm"

cd "$APP_DIR" || cleanup

{
    echo "=== $(date '+%F %T') 启动 ==="
    echo "NAS  : $(mountpoint -q /mnt/Roms && echo 已挂载 || echo 未挂载)"
    echo "手柄 : $(ls /dev/input/js* 2>/dev/null | tr '\n' ' ')"
} >> "$LOG" 2>&1

python3 "$APP_DIR/run.py" >> "$LOG" 2>&1 &
CHILD=$!
wait "$CHILD"
RC=$?
echo "=== $(date '+%F %T') 子进程退出码 $RC ===" >> "$LOG" 2>&1

cleanup
