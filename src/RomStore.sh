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
# 界面想播预览视频时，就写这个文件并以 EXIT_PLAY(42) 退出，本脚本接手播放。
# 两行：第 1 行视频绝对路径，第 2 行游戏 id（播完用它把界面恢复到那一页）。
PLAY_REQ="/tmp/romstore-play.req"
EXIT_PLAY=42

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

# ---------------------------------------------------------------
#  界面与「预览视频」的接力循环
#
#  为什么要在脚本层做：本进程的 SDL/KMS 窗口占着 DRM 显示，没法和 mpv 并存，
#  而掌机没有 X11/Wayland，mpv 只能 DRM 直出。所以让界面先退出让出显示，
#  由本脚本播完再把界面拉回来 —— 而且能精确回到刚才那一款游戏的详情页。
#
#      run.py 退出码 0            -> 正常退出商店，收摊
#      run.py 退出码 EXIT_PLAY(42) -> 播 PLAY_REQ 里那个视频，然后带着 --resume 重开界面
# ---------------------------------------------------------------
rm -f "$PLAY_REQ"
ARGS=""
while :; do
    python3 "$APP_DIR/run.py" $ARGS >> "$LOG" 2>&1 &
    CHILD=$!
    wait "$CHILD"
    RC=$?
    CHILD=""
    echo "=== $(date '+%F %T') 子进程退出码 $RC ===" >> "$LOG" 2>&1
    ARGS=""

    [ "$RC" = "$EXIT_PLAY" ] || break
    [ -f "$PLAY_REQ" ] || break

    VIDEO=$(sed -n '1p' "$PLAY_REQ")
    GID=$(sed -n '2p' "$PLAY_REQ")
    rm -f "$PLAY_REQ"

    if [ -n "$VIDEO" ] && [ -f "$VIDEO" ]; then
        echo "=== $(date '+%F %T') 播放预览: $VIDEO ===" >> "$LOG" 2>&1
        # 直接播 NAS 上的源文件（CIFS 挂载点），不复制到掌机。
        #
        # --hwdec=auto 只是"有硬件就用"，但实测某些情况下（SSH 会话抢不到
        # /dev/dri）会硬解失败并直接放弃：报 "h264_v4l2m2m: Could not find a
        # valid device" + "Could not open codec" → 一个字都放不出来。
        # 所以必须显式给出软解回落：hwdec=auto-safe，硬件不可用时自动降级。
        #
        # --length=180 是兜底上限（NAS 上个别片源没有明确时长，否则会一直播）。
        mpv --vo=drm --ao=alsa --hwdec=auto-safe \
            --msg-level=all=warn --no-osc \
            --cache=yes --demuxer-max-bytes=16MiB \
            --length=180 "$VIDEO" >> "$LOG" 2>&1
        echo "=== $(date '+%F %T') 播放结束 rc=$? ===" >> "$LOG" 2>&1
    else
        echo "=== $(date '+%F %T') 视频不存在，跳过: '$VIDEO' ===" >> "$LOG" 2>&1
    fi

    ARGS="--resume $GID"
done

cleanup
