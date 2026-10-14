#!/bin/bash
# =============================================================================
#  ROM 商店 —— 卸载（双平台：ArkOS / AURKNIX-ROCKNIX）
#
#  用法：
#     sudo ./uninstall.sh                # 卸掉商店与 NAS 挂载服务，保留配置与索引
#     sudo ./uninstall.sh --keep-mount   # 只卸商店，保留 NAS 挂载点与挂载服务
#     sudo ./uninstall.sh --purge        # 连程序目录（索引/配置/日志/备份）一起删
#     sudo ./uninstall.sh --check        # 只列出会被删的东西，不真删
#     sudo ./uninstall.sh --platform rocknix   # 强制指定平台（一般不用）
#
#  平台是**自动探测**的：先看 /etc/os-release，再看结构性特征（是否有
#  /storage/.config/system.d、/storage/roms、/opt/system），最后回落到 arkos。
#  若程序目录里有 platform.json（安装器写的），以它为准 —— 那是最可靠的证据。
#
#  与 install.sh 一样，两条路径分得很开，别把 .126 上的命令拿到 .135 上跑：
#    ============ ================================ ============================
#    项目         arkos                            rocknix
#    ============ ================================ ============================
#    程序目录     /home/ark/.romstore               /storage/.romstore
#    NAS 挂载点   /mnt/Roms                        /storage/nas-roms
#    挂载脚本     /usr/local/sbin/nas-roms-mount.sh /storage/.romstore/nas-roms-mount.sh
#    ES 入口      /opt/system                      /storage/roms/ports
#    自启单元     /etc/systemd/system              /storage/.config/system.d（软链启用）
#    只读自愈     有（roms-fs-selfheal/watch）      无
#    ============ ================================ ============================
# =============================================================================

set -u

HERE=$(cd "$(dirname "$0")" && pwd)

TARGET_USER=""
PLATFORM=""
ES_DIR=""
KEEP_MOUNT=0
PURGE=0
CHECK_ONLY=0

STORE_DIR=""
TARGET_HOME=""
TARGET_UID=""
TARGET_GID=""

PLATFORM_LABEL=""
MOUNT_SCRIPT=""
UNIT_DIR=""
UNIT=""
MOUNT_POINT=""
ES_ROMS=""
SYSTEMD_ENABLE=""

# 只有 arkos 会装（见 install.sh ⑨）
HEAL_SH=""
HEAL_UNIT=""
WATCH_SH=""
WATCH_UNIT=""
DISPATCHER="/etc/NetworkManager/dispatcher.d/50-nas-roms"

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

# json_str <platform.json> <键>  —— 只取字符串值，取不到给空
json_str() {
    [ -f "$1" ] || return 0
    sed -n 's/.*"'"$2"'"[ ]*:[ ]*"\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1
}

detect_platform() {
    _env="${ROMSTORE_PLATFORM:-}"
    if [ -n "$_env" ]; then printf '%s' "$_env"; return; fi
    _rel=$(sed -n 's/^OS_NAME="\?\([^"]*\)"\?/\1/p' /etc/os-release 2>/dev/null | head -1)
    case "$(printf '%s' "$_rel" | tr 'A-Z' 'a-z')" in
        *rocknix*|*aurknix*|*jelos*|*batocera*) printf 'rocknix'; return ;;
    esac
    if [ -d /storage/.config/system.d ] || [ -d /storage/roms ]; then
        printf 'rocknix'; return
    fi
    printf 'arkos'
}

# ----------------------------------------------------------------- 参数
while [ $# -gt 0 ]; do
    case "$1" in
        --target-user) TARGET_USER="${2:-}"; shift 2 ;;
        --platform)    PLATFORM="${2:-}";    shift 2 ;;
        --es-dir)      ES_DIR="${2:-}";      shift 2 ;;
        --keep-mount)  KEEP_MOUNT=1; shift ;;
        --purge)       PURGE=1;      shift ;;
        --check)       CHECK_ONLY=1; shift ;;
        -h|--help)     sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "未知参数：$1（用 --help 看用法）" ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        # 用 $HERE 拼绝对路径（$0 在 `bash uninstall.sh` 下是相对路径，
        # sudo 换 cwd 后就找不到了）；没可执行位时显式带上解释器。
        SELF="$HERE/$(basename "$0")"
        if [ -x "$SELF" ]; then
            exec sudo -- "$SELF" "$@"
        else
            exec sudo -- /bin/bash "$SELF" "$@"
        fi
    fi
    die "请用 root 运行：sudo $0"
fi

# ----------------------------------------------------------------- 平台
if [ -z "$PLATFORM" ]; then
    PLATFORM=$(detect_platform)
fi

case "$PLATFORM" in
    arkos)
        PLATFORM_LABEL="ArkOS4Clone（R36S 等 aarch64 掌机）"
        STORE_DIR="/home/ark/.romstore"
        MOUNT_POINT="/mnt/Roms"
        MOUNT_SCRIPT="/usr/local/sbin/nas-roms-mount.sh"
        UNIT_DIR="/etc/systemd/system"
        ES_ROMS="/roms"
        ES_DIR_DEFAULT="/opt/system"
        SYSTEMD_ENABLE="systemctl"
        HEAL_SH="/opt/system/roms-fs-selfheal.sh"
        HEAL_UNIT="/etc/systemd/system/roms-fs-selfheal.service"
        WATCH_SH="/opt/system/roms-fs-watch.sh"
        WATCH_UNIT="/etc/systemd/system/roms-fs-watch.service"
        ;;
    rocknix)
        PLATFORM_LABEL="AURKNIX / ROCKNIX（X35S 等 RK3566 掌机）"
        STORE_DIR="/storage/.romstore"
        MOUNT_POINT="/storage/nas-roms"
        MOUNT_SCRIPT="/storage/.romstore/nas-roms-mount.sh"
        UNIT_DIR="/storage/.config/system.d"
        ES_ROMS="/storage/roms"
        ES_DIR_DEFAULT="/storage/roms/ports"
        SYSTEMD_ENABLE="symlink"
        # 这两个自愈服务在 ROCKNIX 上**从来没被装过**（见 install.sh ⑨ 的分支），
        # 留空 = 后面所有清理步骤自动跳过。
        ;;
    *)  die "未知平台：$PLATFORM（只支持 arkos / rocknix）" ;;
esac

# ---- platform.json 是安装期的权威结论，有就用它校正上面这些默认值 ----
PJSON="$STORE_DIR/platform.json"
if [ -f "$PJSON" ]; then
    _p=$(json_str "$PJSON" platform)
    case "$_p" in
        arkos|rocknix)
            if [ "$_p" != "$PLATFORM" ]; then
                warn "探测到 $PLATFORM，但 $PJSON 说是 $_p —— 按 $_p 处理"
                exec env ROMSTORE_PLATFORM="$_p" "$0" \
                     $( [ -n "$TARGET_USER" ] && echo "--target-user $TARGET_USER" ) \
                     $( [ "$KEEP_MOUNT" = 1 ] && echo --keep-mount ) \
                     $( [ "$PURGE" = 1 ] && echo --purge ) \
                     $( [ "$CHECK_ONLY" = 1 ] && echo --check )
            fi
            ;;
    esac
    for _k in store_dir nas_mount mount_script systemd_dir es_entry_dir; do
        _v=$(json_str "$PJSON" "$_k")
        [ -n "$_v" ] || continue
        case "$_k" in
            store_dir)    STORE_DIR="$_v" ;;
            nas_mount)    MOUNT_POINT="$_v" ;;
            mount_script) MOUNT_SCRIPT="$_v" ;;
            systemd_dir)  UNIT_DIR="$_v" ;;
            es_entry_dir) ES_DIR_DEFAULT="$_v" ;;
        esac
    done
fi

UNIT="$UNIT_DIR/nas-roms.service"
TIMER="$UNIT_DIR/nas-roms.timer"
ES_GUARD_TMPL="$UNIT_DIR/emulationstation.service.d/10-crash-limit.conf"

# ----------------------------------------------------------------- 目标用户
if [ -z "$TARGET_USER" ]; then
    if [ "$PLATFORM" = "rocknix" ]; then
        TARGET_USER="root"          # 这台只有 root
    elif id ark >/dev/null 2>&1; then
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

# 用户用 --target-user 指定了别人时，arkos 侧程序目录跟着 HOME 走；
# rocknix 只有 /storage 可写，不按 HOME 拼（与 install.sh 同一规则）。
if [ "$PLATFORM" = "arkos" ] \
   && [ "$TARGET_USER" != "ark" ] && [ "$TARGET_USER" != "root" ]; then
    STORE_DIR="$TARGET_HOME/.romstore"
fi

[ -n "$ES_DIR" ] || ES_DIR="$ES_DIR_DEFAULT"

# 以 root 身份跑 python（rocknix 上就是 root，没有 sudo 可借）
run_as_target() {
    if [ "$TARGET_UID" = "0" ] || ! command -v sudo >/dev/null 2>&1; then
        env HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" \
            PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
            python3 -m romstore.cli "$@"
    else
        sudo -u "$TARGET_USER" -H \
            env HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" \
                PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
            python3 -m romstore.cli "$@"
    fi
}

say ""
say "${C_BOLD}ROM 商店 卸载${C_0}"
say ""
say "  平台      $PLATFORM（$PLATFORM_LABEL）"
say "  目标用户  $TARGET_USER ($TARGET_UID:$TARGET_GID)"
say "  程序目录  $STORE_DIR"
say "  ES 入口   $ES_DIR"
say "  NAS 挂载  $MOUNT_POINT"
if [ "$PURGE" = "1" ]; then
    say "  ${C_R}--purge：程序目录（含索引 / source.json / 日志 / 备份）会被删除${C_0}"
else
    say "  （程序目录保留；要删干净加 --purge）"
fi
if [ "$KEEP_MOUNT" = "1" ]; then
    say "  --keep-mount：$MOUNT_POINT 的挂载与挂载服务会被保留"
else
    say "  $MOUNT_POINT 的挂载服务与挂载点会被一并清理"
fi

# 会被处理的文件清单（--check 与收尾自检共用同一份）
FILES="$UNIT $TIMER $ES_DIR/RomStore.sh $ES_DIR/VideoPlayer.sh"
[ -n "$HEAL_UNIT" ]  && FILES="$FILES $HEAL_UNIT $HEAL_SH"
[ -n "$WATCH_UNIT" ] && FILES="$FILES $WATCH_UNIT $WATCH_SH"
[ "$PLATFORM" = "arkos" ] && FILES="$FILES $DISPATCHER $ES_GUARD_TMPL"

if [ "$CHECK_ONLY" = "1" ]; then
    head_ "会被删除的内容"
    for f in $FILES; do
        [ -e "$f" ] && say "  删 $f" || say "  · $f（不存在）"
    done
    if [ "$KEEP_MOUNT" = "1" ]; then
        say "  · 保留 $MOUNT_SCRIPT 与 $UNIT"
    else
        [ -e "$MOUNT_SCRIPT" ] && say "  删 $MOUNT_SCRIPT"
    fi
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
# 先停 timer —— 否则它可能在我们删掉 service 之后又把它拉起来
if systemctl list-unit-files 2>/dev/null | grep -q '^nas-roms\.timer'; then
    systemctl stop nas-roms.timer 2>/dev/null && ok "已停止 nas-roms.timer" \
                                              || warn "停止 timer 失败（可能本来就未运行）"
fi
if systemctl list-unit-files 2>/dev/null | grep -q '^nas-roms\.service'; then
    systemctl stop nas-roms.service 2>/dev/null && ok "已停止 nas-roms.service" \
                                                 || warn "停止失败（可能本来就未运行）"
fi
# disable：常驻单元目录的用 systemctl；ROCKNIX 只有软链，删软链即可。
# ⚠️ 自启现在挂在 timers.target.wants（新版）；旧版残留挂在 multi-user.target.wants，两个都清。
if [ "$SYSTEMD_ENABLE" = "symlink" ]; then
    for _lnk in "$UNIT_DIR/timers.target.wants/nas-roms.timer" \
                "$UNIT_DIR/multi-user.target.wants/nas-roms.service"; do
        if [ -L "$_lnk" ] || [ -e "$_lnk" ]; then
            rm -f "$_lnk" && ok "已删自启软链 $_lnk"
        else
            skip "$_lnk"
        fi
    done
else
    systemctl disable nas-roms.timer   >/dev/null 2>&1 || true
    systemctl disable nas-roms.service >/dev/null 2>&1 || true
    ok "已取消开机自启（nas-roms.timer / nas-roms.service）"
fi

# ----------------------------------------------------------------- 2. 撤入口
head_ "② 撤销 EmulationStation 入口"
if [ -f "$STORE_DIR/run.py" ]; then
    if run_as_target --unregister >/dev/null 2>&1; then
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
    else
        skip "$UNIT"
    fi

    if [ -e "$TIMER" ]; then
        rm -f "$TIMER" && ok "已删除 $TIMER"
    else
        skip "$TIMER"
    fi

    if [ "$PLATFORM" = "arkos" ] && [ -e "$DISPATCHER" ]; then
        rm -f "$DISPATCHER" && ok "已删除 $DISPATCHER"
    elif [ "$PLATFORM" = "arkos" ]; then
        skip "$DISPATCHER"
    fi

    # 只读自愈：属于机器维护而非 ROM 商店本体，即使 --keep-mount 也要清掉
    if [ -n "$HEAL_UNIT" ] && { [ -e "$HEAL_UNIT" ] || [ -e "$HEAL_SH" ]; }; then
        systemctl disable roms-fs-selfheal.service >/dev/null 2>&1 || true
        systemctl disable roms-fs-watch.service >/dev/null 2>&1 || true
        rm -f "$HEAL_UNIT" "$HEAL_SH" "$WATCH_UNIT" "$WATCH_SH"
        ok "已删除只读自愈与只读监控"
    elif [ -n "$HEAL_UNIT" ]; then
        skip "$HEAL_UNIT"
    fi
    systemctl daemon-reload >/dev/null 2>&1 || true

    if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        # rclone 的 FUSE 挂载用 fusermount 卸更干净（umount 常常 EBUSY）
        if [ "$PLATFORM" = "rocknix" ]; then
            fusermount -u -z "$MOUNT_POINT" 2>/dev/null || true
        fi
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
if [ "$PLATFORM" = "arkos" ] && [ -e "$ES_GUARD_TMPL" ]; then
    head_ "④ ES 崩溃循环保护"
    rm -f "$ES_GUARD_TMPL" && ok "已删除 $ES_GUARD_TMPL"
    rmdir "$UNIT_DIR/emulationstation.service.d" 2>/dev/null
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

systemctl daemon-reload >/dev/null 2>&1 || true

# ----------------------------------------------------------------- 总结
head_ "卸载完成"

# 卸载后自检：确认真的干净了，避免「以为卸了其实还在」
LEFT=0
for f in $FILES; do
    if [ -e "$f" ]; then
        printf '  %s✘%s 残留：%s\n' "$C_R" "$C_0" "$f"
        LEFT=1
    fi
done
if [ "$SYSTEMD_ENABLE" = "symlink" ]; then
    for _lnk in "$UNIT_DIR/timers.target.wants/nas-roms.timer" \
                "$UNIT_DIR/multi-user.target.wants/nas-roms.service"; do
        if [ -e "$_lnk" ]; then
            printf '  %s✘%s 残留：%s\n' "$C_R" "$C_0" "$_lnk"
            LEFT=1
        fi
    done
fi
for ent in 'RomStore\.sh|ROM 商店' 'VideoPlayer\.sh|视频播放器'; do
    _pat="${ent%%|*}"; _name="${ent##*|}"
    if [ -f "$ES_DIR/gamelist.xml" ] && grep -q "$_pat" "$ES_DIR/gamelist.xml" 2>/dev/null; then
        printf '  %s✘%s 残留：%s 里仍有「%s」条目\n' "$C_R" "$C_0" \
               "$ES_DIR/gamelist.xml" "$_name"
        LEFT=1
    fi
done
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
say "  ${C_Y}已装/已下载的游戏文件不受影响${C_0}（它们在 $ES_ROMS 下，本脚本不碰）"
say ""
