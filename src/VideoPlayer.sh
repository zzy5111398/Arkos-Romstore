#!/bin/bash
# ---------------------------------------------------------------
#  视频播放器 —— 在掌机上看视频（本地 / NAS 上的视频文件）
#
#  由 EmulationStation 的 Options 系统调用。
#  入口路径：/opt/system/VideoPlayer.sh
#
#  设计要点：
#    * 菜单用 dialog（纯控制台 UI），手柄经 gptokeyb 映射成方向键/回车。
#      dialog 只能显示 Latin 字体 —— 中文文件名会显示成方块，这是固件限制，
#      不是本脚本的 bug。我们把菜单标签做成英文，让操作始终可读。
#    * 播放用 mpv + DRM 直出（本机没有 X11/Wayland）。
#    * 进播放器前必须停 emulationstation：ES 独占 DRM 显示与 ALSA 音频，
#      不停掉 mpv 抢不到设备。退出时无论怎样都要把 ES 拉回来。
#
#  ⚠️ 关于「把 ES 拉回来」：
#  ArkOS 的 emulationstation.service 里跑的是一个 wrapper 脚本，它 while 循环常驻，
#  监控 /tmp/es-restart 标记文件；标记出现就重启主程序。所以：
#    - 想重启 ES：touch /tmp/es-restart 然后 kill 主进程（不要 systemctl restart）。
#    - 我们这里为了独占 DRM，只能 systemctl stop（wrapper 一起退）。
#      退出时再 systemctl start 把整套（wrapper + 主程序）重新拉起来，
#      并顺手删掉可能残留的 es-restart 标记，避免拉起后立刻又重启一次。
# ---------------------------------------------------------------
CURR_TTY="/dev/tty1"
GPTOKEYB="/opt/inttools/gptokeyb"
KEYS_GPTK="/opt/inttools/keys.gptk"
ES_SERVICE="emulationstation"
ES_RESTART_FLAG="/tmp/es-restart"

# 放视频的目录：可以在下面两个里挑（不存在就跳过）
VIDEO_DIRS=(
    "/roms/videos"
    "/roms/ports"
    "/roms/pc"
)

if [ "$(id -u)" -ne 0 ]; then
    exec sudo -- "$0" "$@"
fi

export TERM=linux
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

# ---- 非交互：只列清单 -----------------------------------------------------
# 用法：sudo VideoPlayer.sh --list    （排查用：不碰 ES、不开 UI）
if [ "$1" = "--list" ]; then
    for _d in "${VIDEO_DIRS[@]}"; do
        [ -d "$_d" ] || continue
        find "$_d" -maxdepth 4 -type f \
            \( -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.avi' \
            -o -iname '*.mov' -o -iname '*.webm' -o -iname '*.mpg' \
            -o -iname '*.mpeg' -o -iname '*.m4v' -o -iname '*.ts' \) 2>/dev/null
    done | sort
    exit 0
fi

ES_STOPPED=0
GDTOOL_PID=""

# ---- 把 ES 拉回来（幂等） --------------------------------------------------
restore_es() {
    [ "$ES_STOPPED" -eq 1 ] || return 0

    # 清掉可能残留的重启标记，否则刚拉起来 wrapper 会立刻又重启一次主程序
    rm -f "$ES_RESTART_FLAG" 2>/dev/null

    systemctl start "$ES_SERVICE" >/dev/null 2>&1

    # 等 ES 真正起来（最多 ~10s），起不来再补一枪
    local n=0
    while [ "$n" -lt 20 ]; do
        systemctl is-active --quiet "$ES_SERVICE" && break
        sleep 0.5
        n=$((n + 1))
    done
    if ! systemctl is-active --quiet "$ES_SERVICE"; then
        systemctl start "$ES_SERVICE" >/dev/null 2>&1
        sleep 2
    fi

    ES_STOPPED=0
}

# ---- 收尾：恢复 ES、退出 gptokeyb、清屏 -----------------------------------
cleanup() {
    [ -n "$GDTOOL_PID" ] && kill -TERM "$GDTOOL_PID" 2>/dev/null
    pkill -9 -f "$GPTOKEYB -1 VideoPlayer.sh" 2>/dev/null

    # 播放期间可能被 Ctrl-C 打断，把 ES 拉回来
    restore_es

    dialog --clear > "$CURR_TTY" 2>/dev/null
    printf "\033c" > "$CURR_TTY" 2>/dev/null
    printf "\e[?25h" > "$CURR_TTY" 2>/dev/null
    exit 0
}
trap cleanup EXIT INT TERM HUP

# ---- 初始化终端 -----------------------------------------------------------
printf "\033c" > "$CURR_TTY"
printf "\e[?25l" > "$CURR_TTY"
dialog --clear > "$CURR_TTY" 2>/dev/null

# 字体：中文字幕/文件名不支持，选一个容器能容下的
if [ -e /usr/share/consolefonts/Lat7-TerminusBold22x11.psf.gz ]; then
    setfont /usr/share/consolefonts/Lat7-TerminusBold22x11.psf.gz 2>/dev/null
else
    setfont /usr/share/consolefonts/Lat7-Terminus16.psf.gz 2>/dev/null
fi

mkdir -p /var/log 2>/dev/null

# ---- 让出显示与音频 -------------------------------------------------------
# ES 独占 DRM 与 ALSA。整个播放器会话期间保持 ES 停止，退出时由 cleanup 恢复。
if systemctl is-active --quiet "$ES_SERVICE"; then
    systemctl stop "$ES_SERVICE" >/dev/null 2>&1
    ES_STOPPED=1
    sleep 2
fi

# ---- 手柄映射守护 ---------------------------------------------------------
pkill -9 -f "gptokeyb -1 VideoPlayer.sh" 2>/dev/null
if [ -x "$GPTOKEYB" ]; then
    "$GPTOKEYB" -1 "VideoPlayer.sh" -c "$KEYS_GPTK" >/dev/null 2>&1 &
    GDTOOL_PID=$!
fi

# ---- 收集视频列表 ---------------------------------------------------------
list_videos() {
    local d
    for d in "${VIDEO_DIRS[@]}"; do
        [ -d "$d" ] || continue
        find "$d" -maxdepth 4 -type f \
            \( -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.avi' \
            -o -iname '*.mov' -o -iname '*.webm' -o -iname '*.mpg' \
            -o -iname '*.mpeg' -o -iname '*.m4v' -o -iname '*.ts' \) 2>/dev/null
    done | sort
}

# ---- 播放一个文件 ---------------------------------------------------------
play_file() {
    local f="$1"
    printf "\033c" > "$CURR_TTY"

    # 顺手挂一下 NAS（幂等），这样能直接播 NAS 上的视频
    mountpoint -q /mnt/Roms 2>/dev/null || \
        /usr/local/sbin/nas-roms-mount.sh >/dev/null 2>&1 || true

    mpv --vo=drm --ao=alsa --really-quiet --hwdec=auto \
        --force-window=yes "$f" > "$CURR_TTY" 2>&1

    printf "\033c" > "$CURR_TTY"
}

# ---- 主循环 ---------------------------------------------------------------
while :; do
    VIDEOS=()
    while IFS= read -r line; do
        [ -n "$line" ] && VIDEOS+=("$line")
    done < <(list_videos)

    if [ "${#VIDEOS[@]}" -eq 0 ]; then
        dialog --title " Video Player " --msgbox \
            "\nNo video files were found.\n\nPut your videos in one of:\n  /roms/videos\n  /roms/ports\n  /roms/pc" \
            12 60 > "$CURR_TTY" 2>&1
        break
    fi

    MENU_ARGS=()
    i=1
    for f in "${VIDEOS[@]}"; do
        # 菜单显示：序号 + 文件名（相对路径尽量短）
        label="$(printf '%02d' "$i")  $(basename "$f")"
        MENU_ARGS+=("$i" "$label")
        i=$((i + 1))
    done

    CHOICE=$(dialog --title " Video Player - select a file " \
        --menu "\nUp/Down = move   A = Play   B = Exit" \
        0 0 0 "${MENU_ARGS[@]}" \
        3>&1 1>&2 2>&3 > "$CURR_TTY")
    RC=$?

    [ "$RC" -ne 0 ] && break          # 按 B / Esc 退出
    [ -z "$CHOICE" ] && break

    IDX=$((CHOICE - 1))
    play_file "${VIDEOS[$IDX]}"
done

cleanup
