#!/bin/bash
# ---------------------------------------------------------------
#  ROM 商店 —— 局域网 NAS 一键下载安装到掌机
#
#  由 EmulationStation 调用：
#    ArkOS    -> Options 系统，入口 @@ES_DIR@@/RomStore.sh
#    ROCKNIX  -> ports 系统，入口 @@ES_DIR@@/RomStore.sh
#                （ports 的命令是 runemu.sh，它会替我们停/收 ES，与官方
#                  「在线商店」EmuDrop 用的是同一条路 —— 实测可用）
#
#  本文件里的 @@...@@ 由 install.sh 在安装时按平台替换成实机值，
#  安装后不应残留任何占位符（install.sh 会检查）。
#
#  与固件其它工具（351Files / Wifi-Toggle 等）的差别：
#    它们用 dialog（控制台 UI）+ gptokeyb（手柄→键盘）；
#    本程序是 SDL2 图形界面，直接读手柄事件，因此不需要 gptokeyb，
#    也就能正常显示中文（控制台字体无 CJK 字形）。
#
#  两个平台真正的差别只有三处，都按 $PLATFORM 分支了：
#    1. 提权    arkos 用 sudo；rocknix 只有 root 且没装 sudo
#    2. 显示    arkos=SDL/KMS(DRM 独占)；rocknix=SDL/Wayland(合成器多窗口)
#    3. 收尾    arkos 不动 ES；rocknix 必须 pkill emulationstatio
#               让 essway.service(Restart=always) 拉起新 ES 以重读 gamelist
# ---------------------------------------------------------------
CURR_TTY="/dev/tty1"
APP_DIR="@@STORE_DIR@@"
LOG="$APP_DIR/log/run.log"
# 界面想播预览视频时，就写这个文件并以 EXIT_PLAY(42) 退出，本脚本接手播放。
# 两行：第 1 行视频绝对路径，第 2 行游戏 id（播完用它把界面恢复到那一页）。
PLAY_REQ="/tmp/romstore-play.req"
# libgbm 修复脚本（装在 /opt/system/，ArkOS 与 DarkOS 通用）。不存在就跳过自愈。
SELFHELP_GBM="/opt/system/mpv-libgbm-fix.sh"
EXIT_PLAY=42

# 平台参数（安装时渲染）
PLATFORM="@@PLATFORM@@"
NAS_MOUNT="@@MOUNT_POINT@@"
MOUNT_SCRIPT="@@MOUNT_SCRIPT@@"
SDL_DRIVER="@@SDL_VIDEO_DRIVER@@"
GAMEPAD_DB="@@GAMEPAD_DB@@"
MPV_VO="@@MPV_VO@@"
MPV_AO="@@MPV_AO@@"
EXTRA_PATH="@@EXTRA_PATH@@"

# ArkOS 上是 ark 用户跑商店，要 sudo 提权；ROCKNIX 只有一个 root 用户，
# 而且**没装 sudo** —— 不看 uid 直接 exec sudo 会得到 "sudo: not found"。
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        # 必须解析成绝对路径：$0 在 `bash RomStore.sh` 下是相对路径，
        # sudo 换个 cwd 就 `command not found` 了。
        SELF=$(readlink -f "$0" 2>/dev/null || echo "$0")
        if [ -x "$SELF" ]; then
            exec sudo -- "$SELF" "$@"
        else
            exec sudo -- /bin/bash "$SELF" "$@"
        fi
    fi
    echo "需要 root 权限运行（本机没有 sudo，请直接用 root）" >> "$LOG" 2>&1
    exit 1
fi

[ -n "$EXTRA_PATH" ] && PATH="$EXTRA_PATH:$PATH"
# 补 sbin：普通用户 PATH 不含它（126 实测），mount.cifs/fsck.exfat 都在里面。
case ":$PATH:" in
    *:/sbin:*) ;;
    *) PATH="$PATH:/sbin:/usr/sbin" ;;
esac
export PATH
# ⚠️ 专给 mpv 用的私有库目录（Mali 抢占 libgbm.so.1 的修复，见 mpv-libgbm-fix.sh）。
# 绝不能 export 到全局 —— 里面的 libgbm 是 Mesa 的，而 Mali（ES/GPU 加速）
# 要的是系统那份 libMali.so。全局污染会黑屏。只在起 mpv 的那一条命令上带。
MPV_LIB_DIR="$APP_DIR/lib/mpv"

mkdir -p "$APP_DIR/log" 2>/dev/null

CHILD=""
STAY_AWAKE=0

# 关键：必须连子进程一起收掉。
# 否则只杀本脚本而 python 仍在跑，会有一只手柄/显示被占住的孤儿进程。
cleanup() {
    if [ -n "$CHILD" ] && kill -0 "$CHILD" 2>/dev/null; then
        kill -TERM "$CHILD" 2>/dev/null
        sleep 1
        kill -KILL "$CHILD" 2>/dev/null
    fi
    # KMS/DRM 那台要清屏；Wayland 那台没有 tty1 控制台，写不进去就算了
    if [ -w "$CURR_TTY" ]; then
        printf "\033c" > "$CURR_TTY" 2>/dev/null
        printf "\e[?25h" > "$CURR_TTY" 2>/dev/null
    fi
    # 松掉「别休眠」闸门（只有本脚本立起来的才拆，别误伤别人的）
    [ "$STAY_AWAKE" = "1" ] && rm -f /tmp/stay_awake 2>/dev/null

    # ---- 收掉遗留的 mpv 帧服务进程 -----------------------------------------
    # 界面进程正常退出时会自己收（shutdown -> _kill）；这里兜两种情况：
    #   1. 界面进程吃到 SIGKILL（SIGTERM 已由 run.py 的处理器接管）；
    #   2. 本脚本自己被强杀后又被重新拉起。
    # 残留的 mpv 会一直往 /tmp 灌 jpg（实测 10 分钟 2741 个 / 22MB）
    # 并且**占着声卡**，让新开的预览变静音。详见 vplayer.sweep_stale。
    python3 "$APP_DIR/run.py" --sweep >/dev/null 2>&1

    # ---- 收尾：把 EmulationStation 拉回来，并让它重读 gamelist ----------------
    #
    # 为什么必须自己做：ES 是**只在启动时读一次** gamelist.xml 的，
    # 而商店刚往 gamelist 里塞了新游戏 —— 不重启 ES 用户就看不到。
    #
    # ROCKNIX 侧的事实（与 ArkOS 完全不同，实测确认）：
    #   * Sway 是合成器，ES 并没有被停掉，只是被压在商店窗口底下继续活着，
    #     所以**不能**指望 runemu.sh 或 set_kill 替我们做什么
    #     （set_kill 只是给全局强杀热键登记名字，见 /etc/profile.d/001-functions）；
    #   * 真正让 ES 回来的是 essway.service 的 `Restart=always`：
    #     杀掉 ES → systemd 立刻重新拉起一个全新的 → 顺带重读 gamelist。
    #   * 官方「在线商店」EmuDrop 结尾也是同一招（pkill emulationstatio）。
    #
    # 注意 pkill 的匹配口径：它默认比的是 /proc/<pid>/comm，**只有 15 个字符**，
    # 所以必须写 "emulationstatio"（少一个 n）—— 写全名一个都匹配不到。
    if [ "$PLATFORM" = "rocknix" ]; then
        if command -v pkill >/dev/null 2>&1; then
            pkill emulationstatio 2>/dev/null && \
                echo "=== $(date '+%F %T') 已杀 ES，等 essway.service 拉起 ===" >> "$LOG" 2>&1
        fi
    fi
    exit 0
}
trap cleanup EXIT INT TERM HUP

# NAS 没挂上就补挂一次（幂等，带 flock）
if ! mountpoint -q "$NAS_MOUNT" 2>/dev/null; then
    "$MOUNT_SCRIPT" >/dev/null 2>&1 || true
fi

# 逛商店/装游戏时别让屏幕黑掉。
# ROCKNIX 的固件惯例是往 /tmp/stay_awake 写个标记（官方在线商店 EmuDrop 也这么做），
# ArkOS 上没有这个机制，写了也就是个无害的临时文件 —— 两个平台统一处理。
echo 1 > /tmp/stay_awake 2>/dev/null && STAY_AWAKE=1

# SDL 需要手柄映射库才能认出掌机的 Gamepad
[ -n "$GAMEPAD_DB" ] && [ -e "$GAMEPAD_DB" ] && \
    export SDL_GAMECONTROLLERCONFIG_FILE="$GAMEPAD_DB"
if [ -n "$SDL_DRIVER" ]; then
    export SDL_VIDEODRIVER="$SDL_DRIVER"
fi

# Wayland（ROCKNIX）下这两个变量从 ES 启动时是继承来的；
# 从 SSH 手动跑时没有，补上默认值，否则 SDL 会落到 offscreen 上（什么都看不见）。
if [ "$SDL_DRIVER" = "wayland" ]; then
    if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
        for d in /var/run/0-runtime-dir /run/user/0 /run/user/1000; do
            [ -d "$d" ] && { XDG_RUNTIME_DIR="$d"; break; }
        done
        export XDG_RUNTIME_DIR
    fi
    if [ -z "${WAYLAND_DISPLAY:-}" ]; then
        for w in wayland-1 wayland-0; do
            [ -e "${XDG_RUNTIME_DIR:-/nonexistent}/$w" ] && {
                WAYLAND_DISPLAY="$w"; break; }
        done
        export WAYLAND_DISPLAY
    fi
fi

cd "$APP_DIR" || cleanup

{
    echo "=== $(date '+%F %T') 启动 ==="
    echo "平台  : @@PLATFORM_LABEL@@  [$PLATFORM]"
    echo "NAS   : $(mountpoint -q "$NAS_MOUNT" && echo 已挂载 || echo 未挂载)  $NAS_MOUNT"
    echo "显示  : SDL_VIDEODRIVER=${SDL_VIDEODRIVER:-（未设）} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-（未设）}"
    echo "手柄  : $(ls /dev/input/js* 2>/dev/null | tr '\n' ' ')"
    # ES 到底还在不在？ROCKNIX(Wayland) 上它通常**还活着**（被压在商店窗口下面），
    # ArkOS(KMS/DRM) 上它让出显示后一般已经退出。收尾逻辑两者不同，这里留个证据。
    echo "ES    : $(pgrep -f '[e]mulationstation' 2>/dev/null | tr '\n' ' ')"
} >> "$LOG" 2>&1

# ---------------------------------------------------------------
#  界面与「预览视频」的接力循环
#
#  为什么要在脚本层做：界面进程占着显示（ArkOS 上是 SDL/KMS 独占 DRM，
#  ROCKNIX 上是 Wayland 的 toplevel），而 mpv 也要同一块屏。让界面先退出
#  让出显示，由本脚本播完再把界面拉回来 —— 而且能精确回到刚才那一款游戏的详情页。
#
#      run.py 退出码 0            -> 正常退出商店，收摊
#      run.py 退出码 EXIT_PLAY(42) -> 播 PLAY_REQ 里那个视频，然后带 --resume 重开界面
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
        # 直接播 NAS 上的源文件（挂载点里），不复制到掌机。
        #
        # --hwdec=auto 只是"有硬件就用"，但实测某些情况下（SSH 会话抢不到
        # /dev/dri）会硬解失败并直接放弃：报 "h264_v4l2m2m: Could not find a
        # valid device" + "Could not open codec" → 一个字都放不出来。
        # 所以必须显式给出软解回落：hwdec=auto-safe，硬件不可用时自动降级。
        #
        # vo/ao 按平台给（ArkOS: drm/alsa；ROCKNIX: gpu/pulse 或让 mpv 自己选）。
        # --length=180 是兜底上限（NAS 上个别片源没有明确时长，否则会一直播）。
        MPV_ARGS=(--hwdec=auto-safe --msg-level=all=warn --no-osc
                  --cache=yes --demuxer-max-bytes=16MiB --length=180)
        [ -n "$MPV_VO" ] && MPV_ARGS+=(--vo="$MPV_VO")
        [ -n "$MPV_AO" ] && MPV_ARGS+=(--ao="$MPV_AO")
        # ⚠️ LD_LIBRARY_PATH 只作用于这一条 mpv 命令（不 export）。
        #    126 的 Mali 固件把 libgbm.so.1 抢去当自己的了，mpv 加载时会报
        #    `undefined symbol: gbm_surface_create_with_modifiers` —— 那是
        #    **加载期**错误，换 --vo 也没用，整个 mpv 直接起不来。
        #    $MPV_LIB_DIR 里是安装器放好的 Mesa 真库（见 mpv-libgbm-fix.sh）。
        #    ⚠️ 绝不能写进全局 PATH/LD_LIBRARY_PATH：Mali 是 ES/GPU 加速的依赖，
        #    让它加载 Mesa 的 libgbm 会黑屏。
        LD_LIBRARY_PATH="$MPV_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
            mpv "${MPV_ARGS[@]}" "$VIDEO" >> "$LOG" 2>&1
        MPV_RC=$?
        echo "=== $(date '+%F %T') 播放结束 rc=$MPV_RC ===" >> "$LOG" 2>&1

        # 自愈：第一次遇到 libgbm 符号错误就修一次（Mali 抢软链的机器必踩）
        if grep -q 'undefined symbol: gbm_' "$LOG" 2>/dev/null && [ "$MPV_RC" != "0" ]; then
            echo "=== $(date '+%F %T') 疑似 libgbm 符号缺失，尝试自动修复 ===" >> "$LOG" 2>&1
            if [ -x "$SELFHELP_GBM" ] || [ -r "$SELFHELP_GBM" ]; then
                bash "$SELFHELP_GBM" >> "$LOG" 2>&1
            else
                echo "找不到 mpv-libgbm-fix.sh（$SELFHELP_GBM），请重新运行安装器" >> "$LOG" 2>&1
            fi
        fi
    else
        echo "=== $(date '+%F %T') 视频不存在，跳过: '$VIDEO' ===" >> "$LOG" 2>&1
    fi

    ARGS="--resume $GID"
done

cleanup
