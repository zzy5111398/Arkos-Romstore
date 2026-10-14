#!/bin/bash
# =============================================================================
#  ROM 商店（局域网 SMB 版）—— 一键安装 / 升级 —— 三平台
#
#  支持的平台（**安装时自动区分**，见下方「平台探测」）：
#    arkos    ArkOS4Clone（R36S / R36 Ultra 等，用户 ark，根文件系统可写）
#    rocknix  AURKNIX / ROCKNIX（X35S 等 RK3566，用户 root，根是只读 squashfs）
#    darkos   DarkOS / dArkOS（R36S 等，根可写、**ES 是 Batocera 风格命名**）
#
#  用法：
#     sudo ./install.sh --host 10.168.1.100 --share Roms
#     sudo ./install.sh                      # 沿用已有 source.json（全新装则用内置默认）
#     sudo ./install.sh --check              # 只做前置检查，不动任何文件
#     sudo ./install.sh --platform darkos    # 强制指定平台（一般不用）
#     sudo ./install.sh --help
#
#  本脚本是**幂等**的：重复运行 = 升级（源码会被覆盖，source.json 与索引会保留）。
#  卸载用 ./uninstall.sh
#
# ## 三个平台的差别（脚本里每一处都按 PLATFORM 分支）
#
#   ============ ==================== ============================ ====================
#   项目         arkos                rocknix                     darkos
#   ============ ==================== ============================ ====================
#   根文件系统   可写                 **只读 squashfs**            可写（Debian 13 根）
#   持久分区     /                    **只有 /storage**           /
#   目标用户     ark (uid 1002)       **root**                    ark (uid 1000)
#   程序目录     /home/ark/.romstore  /storage/.romstore          /home/ark/.romstore
#   NAS 挂载点   /mnt/Roms            /storage/nas-roms           /mnt/Roms
#   NAS 后端     **内核 cifs**        **rclone FUSE**             **内核 cifs**
#   ES 配置      /etc/emulationstation /storage/.config/emulationstation /etc/emulationstation
#   ES 入口      options→/opt/system  ports→/storage/roms/ports   ports→/roms/ports
#   ES 系统名    ArkOS 风格           Batocera 风格               **Batocera 风格**
#   systemd 单元 /etc/systemd/system  /storage/.config/system.d   /etc/systemd/system
#   中文字体     系统自带 NotoCJK     **无**，从 scummvm 借        系统自带 NotoCJK
#   /roms 自愈   装（exFAT 掉电只读） 跳过                         装（exFAT 掉电只读）
#   ============ ==================== ============================ ====================
#
# ⚠️ darkos 与 arkos 的**文件系统布局几乎相同**（都写 /roms、都有 /opt/system、
#    都有 sudo 与 kmsdrm），唯一实质差异是 **ES 系统名走 Batocera 风格**
#    （见 src/romstore/mapp.py 的 MAP_DARKOS_RAW 与注释里的三列对照表）。
#    所以 darkos 复用 arkos 的绝大部分分支，只是入口系统与映射表不同。
# =============================================================================

set -u

HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/src"
SYSD="$HERE/systemd"
EXTRAS="$HERE/extras"

if [ -r "$HERE/VERSION" ]; then
    VERSION=$(cat "$HERE/VERSION")
else
    VERSION="unknown"
fi

# ----------------------------------------------------------------- 默认值
TARGET_USER=""          # 空 = 按平台自动探测
PLATFORM=""             # 空 = 自动探测；arkos | rocknix | darkos
ES_DIR=""               # ES 入口目录（空 = 按平台/配置探测）
ES_DIR_GIVEN=0          # 用户是否显式 --es-dir
RS_HOST=""
RS_SHARE=""
RS_SMBUSER=""
RS_SMBPASS=""
DO_MOUNT=1
DO_INDEX=1
WITH_ES_GUARD=0
CHECK_ONLY=0
NO_AUTO_APT=0

STORE_DIR=""
TARGET_HOME=""
TARGET_UID=""
TARGET_GID=""
MOUNT_SCRIPT=""
MOUNT_POINT=""
UNIT=""
UNIT_TEMPLATE=""
UNIT_DIR=""
TIMER=""
TIMER_TEMPLATE=""
BACKEND=""
SAMBA_VERS=""
RCLONE_BIN="/usr/bin/rclone"
EXTRA_PATH=""
ES_ROMS=""
ES_CFG=""
ES_ENTRY_NAME="RomStore.sh"
ES_ENTRY_SYSTEM=""
SDL_DRIVER=""
GAMEPAD_DB=""
MPV_VO=""
MPV_AO=""
PREVIEW_AO=""
PREVIEW_TMP=""
PLATFORM_LABEL=""

# /roms 只读自愈（只装 arkos —— 见文件头表格）
HEAL_SH=""
HEAL_UNIT=""
WATCH_SH=""
WATCH_UNIT=""

# ----------------------------------------------------------------- 输出
if [ -t 1 ]; then
    C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'
    C_B=$'\033[36m'; C_0=$'\033[0m'; C_BOLD=$'\033[1m'
else
    C_R=""; C_G=""; C_Y=""; C_B=""; C_0=""; C_BOLD=""
fi
say()   { printf '%s\n' "$*"; }
ok()    { printf '  %s✔%s %s\n' "$C_G" "$C_0" "$*"; }
warn()  { printf '  %s!%s %s\n' "$C_Y" "$C_0" "$*"; }
bad()   { printf '  %s✘%s %s\n' "$C_R" "$C_0" "$*"; }
head_() { printf '\n%s── %s ──%s\n' "$C_BOLD" "$*" "$C_0"; }
die()   { printf '\n%s安装中止：%s%s\n' "$C_R" "$*" "$C_0"; exit 1; }

usage() {
    cat <<EOF
ROM 商店安装器 v$VERSION（双平台：ArkOS / AURKNIX-ROCKNIX）

用法:
  sudo ./install.sh [选项]      # ArkOS（ark 用户，本脚本自己会提权）
  ./install.sh [选项]           # AURKNIX/ROCKNIX（本来就是 root，且**没装 sudo**）
                                # 提示 Permission denied 时用 bash install.sh


商店源（可省；省了则沿用已有 source.json 或内置默认 10.168.1.100/Roms 匿名）:
  --host H            NAS 主机（IP 或主机名），例：10.168.1.100
  --share S           共享名（不带 // 与主机），例：Roms
  --smbuser U         SMB 账号（留空 = 匿名 guest）
  --smbpass P         SMB 密码

安装位置（一般不用改）:
  --platform P        强制平台：arkos | rocknix（默认自动探测）
  --target-user U     程序归属的 Linux 用户（默认按平台：arkos=ark，rocknix=root）
  --es-dir D          ES 入口目录（默认按平台：arkos=/opt/system，rocknix=/storage/roms/ports）
  --no-mount          装完不立刻挂载
  --skip-index        装完不建索引
  --with-es-guard     额外安装 ES 崩溃循环上限保护（仅 arkos，见 extras/）
  --no-auto-apt       缺依赖时不自动 apt-get 补装，只报错停下（仅 arkos）
  --check             只做前置检查，不写任何文件
  -h, --help          显示本帮助

装完怎么用:
  ArkOS    EmulationStation → Options → 「ROM 商店」
  ROCKNIX  EmulationStation → Ports   → 「ROM 商店」
  十字键/左摇杆 移动   右摇杆 滚简介   A 安装   X 播预览   Y 卸载
  L1/R1 翻页   Start 功能菜单   B 返回

改商店源:
  在商店里按 Start →「修改商店源」；或
  python3 -m romstore.cli --source-set 主机,共享[,账号[,密码]]
EOF
}

# ----------------------------------------------------------------- 参数
while [ $# -gt 0 ]; do
    case "$1" in
        --host)        RS_HOST="${2:-}";     shift 2 ;;
        --share)       RS_SHARE="${2:-}";    shift 2 ;;
        --smbuser)     RS_SMBUSER="${2:-}";  shift 2 ;;
        --smbpass)     RS_SMBPASS="${2:-}";  shift 2 ;;
        --platform)    PLATFORM="${2:-}";    shift 2 ;;
        --target-user) TARGET_USER="${2:-}"; shift 2 ;;
        --es-dir)      ES_DIR="${2:-}"; ES_DIR_GIVEN=1; shift 2 ;;
        --no-mount)    DO_MOUNT=0;    shift ;;
        --skip-index)  DO_INDEX=0;    shift ;;
        --with-es-guard) WITH_ES_GUARD=1; shift ;;
        --no-auto-apt) NO_AUTO_APT=1; shift ;;
        --check)       CHECK_ONLY=1;  shift ;;
        -h|--help)     usage; exit 0 ;;
        *) die "未知参数：$1（用 --help 看用法）" ;;
    esac
done

# ----------------------------------------------------------------- 0. 权限
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        say "需要 root 权限，正在通过 sudo 重新执行……"
        # ⚠️ 必须用绝对路径（$HERE），不能用 "$0" —— 用户多半是 `bash install.sh`
        # 这样起的，$0 就是相对路径 `install.sh`，sudo 换个 cwd 后找不到
        # （126 实测报 `sudo: install.sh: command not found`）。
        # 还要带上解释器：$0 没有可执行位时 sudo 会当二进制跑。
        SELF="$HERE/$(basename "$0")"
        [ -f "$SELF" ] || die "找不到自身路径：$SELF"
        if [ -x "$SELF" ]; then
            exec sudo -- "$SELF" "$@"
        else
            exec sudo -- /bin/bash "$SELF" "$@"
        fi
    fi
    die "请用 root 运行：sudo $0"
fi

say ""
say "${C_BOLD}ROM 商店 安装器${C_0}  v$VERSION"

# =============================================================================
# ① 平台探测
# =============================================================================
# 判据（顺序即优先级）：
#   1. --platform 显式指定
#   2. /etc/os-release 自报 OS_NAME 属于 ROCKNIX 家族（ROCKNIX/AURKNIX/JELOS/Batocera）
#      或 DarkOS 家族（darkos / darkosre）
#   3. 结构性特征：/storage/.config/system.d 或 /storage/roms 真的存在
#      （**不能只看 /storage 有没有** —— 有些发行版会挂一个空的 /storage）
#   4. hostname 含 darkos（DarkOS 自报的是 Debian，os-release 里认不出来）
#   5. 否则按 ArkOS
#
# ⚠️ ROCKNIX 的判据**必须排在 DarkOS 之前**：darkos 有 /opt/system（ArkOS 特征）
#    但没有 /storage，两者本来不冲突；万一将来出现两族特征共存的机器，
#    先判 ROCKNIX 更保守（只读根 + rclone 那套在 ArkOS 特征机上也能跑，反过来不行）。
#
# 探测结论会写进 <STORE_DIR>/platform.json，运行期由 romstore/platform.py 读取。
head_ "① 平台探测"

if [ -z "$PLATFORM" ]; then
    _rel=""
    if [ -r /etc/os-release ]; then
        _rel=$(sed -n 's/^OS_NAME="\?\([^"]*\)"\?/\1/p' /etc/os-release | head -1)
        [ -n "$_rel" ] || _rel=$(sed -n 's/^NAME="\?\([^"]*\)"\?/\1/p' /etc/os-release | head -1)
    fi
    _low=$(printf '%s' "$_rel" | tr 'A-Z' 'a-z')
    case "$_low" in
        *rocknix*|*aurknix*|*jelos*|*batocera*)
            PLATFORM=rocknix
            ok "/etc/os-release 自报 OS_NAME=$_rel → rocknix"
            ;;
        *darkos*|*dark\ os*)
            PLATFORM=darkos
            ok "/etc/os-release 自报 OS_NAME=$_rel → darkos"
            ;;
    esac
fi

if [ -z "$PLATFORM" ]; then
    if [ -d /storage/.config/system.d ] || [ -d /storage/roms ]; then
        PLATFORM=rocknix
        ok "结构性特征命中（/storage/.config/system.d 或 /storage/roms）→ rocknix"
    else
        # DarkOS 自报 Debian 13，os-release 里没有任何标记，只能看 hostname
        # （实测 darkosre-r36）。放最后一条，命中就认 darkos。
        _host=$(cat /proc/sys/kernel/hostname 2>/dev/null | tr 'A-Z' 'a-z')
        case "$_host" in
            *darkos*|*dark-os*)
                PLATFORM=darkos
                ok "hostname=$_host → darkos"
                ;;
        esac
    fi
fi

if [ -z "$PLATFORM" ]; then
    if [ -d /opt/system ] || [ -e /etc/emulationstation/es_systems.cfg ]; then
        PLATFORM=arkos
        ok "结构性特征命中（/opt/system 或 /etc/emulationstation）→ arkos"
    else
        PLATFORM=arkos
        warn "认不出这台机器，按 arkos 处理（可用 --platform 强制）"
    fi
else
    ok "平台由 --platform 指定：$PLATFORM"
fi

case "$PLATFORM" in
    arkos)
        PLATFORM_LABEL="ArkOS4Clone（R36S 等 aarch64 掌机）"
        STORE_DIR="/home/ark/.romstore"
        MOUNT_POINT="/mnt/Roms"
        MOUNT_SCRIPT="/usr/local/sbin/nas-roms-mount.sh"
        UNIT_DIR="/etc/systemd/system"
        UNIT_TEMPLATE="$SYSD/nas-roms.service"
        BACKEND="cifs"
        SAMBA_VERS="2.1"
        EXTRA_PATH=""
        ES_ROMS="/roms"
        ES_CFG="/etc/emulationstation/es_systems.cfg"
        ES_ENTRY_SYSTEM="options"
        DEFAULT_ES_DIR="/opt/system"
        SDL_DRIVER="kmsdrm"
        GAMEPAD_DB="/opt/inttools/gamecontrollerdb.txt"
        MPV_VO="drm"
        MPV_AO="alsa"
        PREVIEW_AO="alsa"
        PREVIEW_TMP="/tmp"
        SYSTEMD_ENABLE="systemctl"
        ;;
    rocknix)
        PLATFORM_LABEL="AURKNIX / ROCKNIX（X35S 等 RK3566 掌机）"
        STORE_DIR="/storage/.romstore"
        MOUNT_POINT="/storage/nas-roms"
        MOUNT_SCRIPT="/storage/.romstore/nas-roms-mount.sh"
        UNIT_DIR="/storage/.config/system.d"
        UNIT_TEMPLATE="$SYSD/nas-roms-rocknix.service"
        BACKEND="rclone"
        SAMBA_VERS="3.1.1"
        EXTRA_PATH="/storage/.romstore/bin"
        ES_ROMS="/storage/roms"
        ES_CFG="/storage/.config/emulationstation/es_systems.cfg"
        ES_ENTRY_SYSTEM="ports"
        DEFAULT_ES_DIR="/storage/roms/ports"
        SDL_DRIVER="wayland"
        GAMEPAD_DB="/storage/.config/SDL-GameControllerDB/gamecontrollerdb.txt"
        MPV_VO="gpu"
        MPV_AO=""
        # 这台**没有 ffmpeg 命令行**，右栏内嵌预览改由 mpv 出帧
        # （vplayer.MpvFramePlayer）。ES 用的是 pulseaudio，所以 pulse 当备选；
        # vplayer 会自动在末尾补 null 兜底。
        PREVIEW_AO="alsa,pulse"
        PREVIEW_TMP="/tmp"      # 实测 tmpfs（488MB）
        SYSTEMD_ENABLE="symlink"
        ;;
    darkos)
        # DarkOS 的文件系统布局与 arkos 几乎一致（根可写、/home/ark 属主是 ark、
        # /etc/systemd/system 可写、有 sudo、kmsdrm 直出），只有两处不同：
        PLATFORM_LABEL="DarkOS / dArkOS（R36S 等 aarch64 掌机，ES 走 Batocera 风格）"
        STORE_DIR="/home/ark/.romstore"
        MOUNT_POINT="/mnt/Roms"
        MOUNT_SCRIPT="/usr/local/sbin/nas-roms-mount.sh"
        UNIT_DIR="/etc/systemd/system"
        UNIT_TEMPLATE="$SYSD/nas-roms.service"
        BACKEND="cifs"
        SAMBA_VERS="2.1"
        EXTRA_PATH=""
        ES_ROMS="/roms"
        ES_CFG="/etc/emulationstation/es_systems.cfg"
        # ⚠ 这台**没有 options 系统**（实测 grep options = 0），所以走 ports。
        # ports 的 command 是 `%ROM%` 直接执行（perfmax/nice 包装），
        # 不像 ArkOS 那样被固件 runemu.sh 接管 —— 与 ROCKNIX 同一条路。
        ES_ENTRY_SYSTEM="ports"
        DEFAULT_ES_DIR="/roms/ports"
        SDL_DRIVER="kmsdrm"
        GAMEPAD_DB="/opt/inttools/gamecontrollerdb.txt"
        MPV_VO="drm"
        MPV_AO="alsa"
        PREVIEW_AO="alsa"
        PREVIEW_TMP="/tmp"
        SYSTEMD_ENABLE="systemctl"
        ;;
    *)  die "未知平台：$PLATFORM（只支持 arkos / rocknix / darkos）" ;;
esac
SYSTEMD_ENABLE="${SYSTEMD_ENABLE:-systemctl}"

# 单元路径与自启软链位置（两个平台同一个服务名，只是住的地方不同）。
# ⚠️ 必须在这里算出来 —— 早期版本漏了赋值，导致 render 写到一个空路径、
#    chmod "" 报错，但又被 grep 的 "No such file" 吞掉，一路静默跑到最后
#    才在自检里变成"单元未启用"。所以 render() 现在会拒绝空目标。
UNIT="$UNIT_DIR/nas-roms.service"
TIMER="$UNIT_DIR/nas-roms.timer"
TIMER_TEMPLATE="$SYSD/nas-roms.timer"
# ⚠️ 自启**不再**把 service 挂到 multi-user.target.wants —— 见 systemd/nas-roms.timer 的
#    注释：oneshot 服务一旦挂在那里，systemd 会加 Before=multi-user.target，
#    开机就会等挂载脚本跑完（NAS 不在线时实测拖 2 分半）。改用 timer 异步触发。
#    这里保留路径只为升级时**清理旧版残留**的软链。
LEGACY_UNIT_WANTS="$UNIT_DIR/multi-user.target.wants/nas-roms.service"

ok "平台 = $PLATFORM  （$PLATFORM_LABEL）"
say "    · 程序目录   $STORE_DIR"
say "    · NAS 挂载点 $MOUNT_POINT   （后端 $BACKEND）"
say "    · ES ROM 根  $ES_ROMS"
say "    · ES 入口    进入 $ES_ENTRY_SYSTEM 系统"
say "    · 自启单元   $UNIT_DIR"

# ----------------------------------------------------------------- 文件自检
head_ "② 安装包完整性"
[ -d "$SRC" ] || die "找不到 src/ 目录（安装包不完整？当前目录 $HERE）"
for f in run.py RomStore.sh nas-roms-mount.sh; do
    [ -r "$SRC/$f" ] || die "缺少 src/$f"
done
[ -r "$SRC/romstore/ui.py" ] || die "缺少 src/romstore/ui.py"
[ -r "$SRC/romstore/platform.py" ] || die "缺少 src/romstore/platform.py"
[ -r "$UNIT_TEMPLATE" ] || die "缺少 $UNIT_TEMPLATE"
ok "安装包完整（$HERE）"

# ----------------------------------------------------------------- 目标用户
head_ "③ 目标用户"
if [ -z "$TARGET_USER" ]; then
    if [ "$PLATFORM" = "rocknix" ]; then
        TARGET_USER="root"          # 这台只有 root（PermitRootLogin yes）
    elif id ark >/dev/null 2>&1; then
        TARGET_USER="ark"
    else
        for d in /home/*; do
            [ -d "$d" ] || continue
            n=$(basename "$d")
            if id "$n" >/dev/null 2>&1; then TARGET_USER="$n"; break; fi
        done
    fi
fi
[ -n "$TARGET_USER" ] || die "探测不到目标用户，请用 --target-user 指定"
id "$TARGET_USER" >/dev/null 2>&1 || die "用户 $TARGET_USER 不存在"

TARGET_HOME=$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)
[ -n "$TARGET_HOME" ] || TARGET_HOME=$(eval echo "~$TARGET_USER")
TARGET_UID=$(id -u "$TARGET_USER")
TARGET_GID=$(id -g "$TARGET_USER")

# 用户可能用 --target-user 指定了别人；程序目录跟着走（除非平台是 rocknix，
# 那台只有 /storage 可写，不能按 HOME 拼）
if [ "$PLATFORM" = "rocknix" ] && [ "$TARGET_USER" != "root" ]; then
    warn "ROCKNIX 上只有 /storage 可写，程序目录固定 $STORE_DIR（不看 HOME）"
else
    if [ "$TARGET_USER" != "root" ] && [ "$TARGET_USER" != "ark" ]; then
        STORE_DIR="$TARGET_HOME/.romstore"
    fi
fi

ok "用户 = $TARGET_USER (uid=$TARGET_UID gid=$TARGET_GID)"
ok "程序目录 = $STORE_DIR"

# ---- ES 入口目录 ----------------------------------------------------------
# ArkOS：入口能不能出现在菜单里，唯一取决于 **options 系统** 的 <path>。
#        与其硬编码默认值再事后警告，不如直接问配置文件要。
# ROCKNIX：没有 options 系统；用 ports 系统（<path>/storage/roms/ports</path>），
#        它的命令是 runemu.sh —— 会替我们打理 ES 的停/收，与官方「在线商店」
#        (EmuDrop) 同路。这里同样从配置里核对一遍。
if [ "$ES_DIR_GIVEN" != "1" ]; then
    ES_DIR=""
    if [ -f "$ES_CFG" ]; then
        ES_DIR=$(awk -v want="$ES_ENTRY_SYSTEM" '
            /<system>/ { inblk=1; name=""; path="" }
            inblk && /<name>/ {
                n=$0; sub(/.*<name>/, "", n); sub(/<\/name>.*/, "", n); name=n
            }
            inblk && /<path>/ {
                p=$0; sub(/.*<path>/, "", p); sub(/<\/path>.*/, "", p); path=p
            }
            inblk && /<\/system>/ {
                if (name == want) { print path; exit }
                inblk=0
            }
        ' "$ES_CFG" 2>/dev/null)
        ES_DIR="${ES_DIR%/}"
    fi
    if [ -n "$ES_DIR" ]; then
        ok "ES $ES_ENTRY_SYSTEM 系统目录自动探测：$ES_DIR"
    else
        ES_DIR="$DEFAULT_ES_DIR"
        warn "没能从 $ES_CFG 解析出 $ES_ENTRY_SYSTEM 系统，用默认 $ES_DIR"
    fi
else
    ok "ES 入口目录（--es-dir 指定）：$ES_DIR"
fi

# ----------------------------------------------------------------- ④ 依赖
head_ "④ 依赖检查"
FAIL=0
MISSING_PKGS=""

need_cmd() {
    local cmd="$1" why="$2" pkg="${3:-}"
    if command -v "$cmd" >/dev/null 2>&1; then
        ok "$cmd  $(command -v "$cmd")"
        return 0
    fi
    # 只有 arkos / darkos 能补装（有 apt，根可写）；rocknix 上别提示 apt-get，那东西不存在
    if [ -n "$pkg" ] && [ "$PLATFORM" != "rocknix" ]; then
        MISSING_PKGS="$MISSING_PKGS $pkg"
        if [ "$AUTO_APT" = "1" ]; then
            warn "$cmd 未安装 —— $why（将自动补装 $pkg）"
            return 0
        fi
        bad "$cmd 未安装 —— $why"
        bad "  补装：apt-get install $pkg"
    else
        bad "$cmd 未安装 —— $why"
        [ "$PLATFORM" = "rocknix" ] && \
            bad "  本平台根是只读 squashfs，没有包管理器 —— 请手工放一个到 PATH 里"
    fi
    FAIL=1
    return 1
}

need_file() {   # need_file <路径> <用途> [<替代路径>...]
    local f="$1" why="$2"; shift 2
    local c
    for c in "$f" "$@"; do
        if [ -e "$c" ]; then ok "$c"; return 0; fi
    done
    bad "$f 不存在 —— $why"
    FAIL=1
    return 1
}

# ⚠️ mount.cifs / mountpoint / fsck.exfat 都住在 /usr/sbin，而普通用户的 PATH
# 通常**不含 sbin**（126 实测 ark 的 PATH = /usr/local/bin:/usr/bin:/bin:/usr/games）。
# 不补这一段，install.sh 会对着已装好的 cifs-utils 报「mount.cifs 未安装」。
# 必须放在**所有 need_cmd 之前**。重复添加无害。
case ":$PATH:" in
    *:/sbin:*) ;;
    *) PATH="$PATH:/sbin:/usr/sbin"; export PATH ;;
esac

# 自动补装只对 arkos / darkos 有意义（rocknix 上没有 apt，根还是只读的）
AUTO_APT="${AUTO_APT:-0}"
if [ "$NO_AUTO_APT" != "1" ] && [ "$CHECK_ONLY" != "1" ] && [ "$PLATFORM" != "rocknix" ] \
   && command -v apt-get >/dev/null 2>&1; then
    AUTO_APT=1
fi

need_cmd python3 "本程序是 Python 写的"
need_cmd flock   "挂载并发串行化（util-linux）" util-linux
need_cmd mountpoint "判断挂载状态（util-linux）" util-linux
need_cmd nc      "探测 NAS 的 445 端口（netcat）" netcat-openbsd

if [ "$BACKEND" = "cifs" ]; then
    need_cmd mount.cifs "挂载 SMB 共享（cifs 内核 + mount.cifs 用户态 helper）" cifs-utils
else
    need_cmd rclone "挂 SMB 共享（本机内核没有 cifs，只能走 rclone 的 FUSE）"
    # rclone 的 FUSE 挂载必须有 fusermount；1.71 只找 fusermount3，
    # 而本机只有 fusermount(v2) —— 没装 fusermount3 的话下面会给桥接方案
    if ! command -v fusermount3 >/dev/null 2>&1 && ! command -v fusermount >/dev/null 2>&1; then
        bad "既没有 fusermount3 也没有 fusermount —— rclone 挂不上（fuse 工具链缺失）"
        FAIL=1
    elif ! command -v fusermount3 >/dev/null 2>&1; then
        warn "只有 fusermount(v2)，没有 fusermount3 —— 安装时会建一个同名桥接"
    else
        ok "fusermount3  $(command -v fusermount3)"
    fi
fi

if python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,5) else 1)' 2>/dev/null; then
    ok "Python $(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')（仅用标准库）"
else
    bad "Python 版本过旧，需要 >= 3.5"; FAIL=1
fi
if python3 -c 'import sqlite3,ctypes,json,threading' 2>/dev/null; then
    ok "标准库 sqlite3 / ctypes / json / threading 齐备"
else
    bad "Python 标准库不完整（缺 sqlite3 或 ctypes）"; FAIL=1
fi

# ---- 中文字体 --------------------------------------------------------------
# ArkOS / DarkOS：系统自带 Noto CJK。
# ROCKNIX：**一个系统级 CJK 字体都没有** —— 得从 scummvm 主题里借一份拷进
#         store 目录（/storage 持久分区），否则界面中文全是方块。
FONT_SRC=""
FONT_DST=""
if [ "$PLATFORM" != "rocknix" ]; then
    for f in /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
             /usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc; do
        [ -f "$f" ] && { FONT_SRC="$f"; break; }
    done
else
    for f in /usr/config/scummvm/themes/themes/fonts-cjk/NotoSansSC-Regular.otf \
             /usr/config/scummvm/themes/themes/fonts-cjk/NotoSansTC-Regular.otf \
             /usr/config/commander-baidupcs/res/DroidSansFallback.ttf \
             /usr/config/drastic/resources/font/font.ttf; do
        [ -f "$f" ] && { FONT_SRC="$f"; break; }
    done
fi
if [ -z "$FONT_SRC" ]; then
    if [ "$PLATFORM" != "rocknix" ]; then
        warn "没找到 Noto CJK 字体 —— 界面中文会显示为空白"
        warn "  补装：apt-get install fonts-noto-cjk"
    else
        bad "找不到任何可用的中文字体 —— 界面中文会显示为方块"
        FAIL=1
    fi
else
    ok "中文字体源 $(basename "$FONT_SRC")"
fi
if [ "$PLATFORM" = "rocknix" ] && [ -n "$FONT_SRC" ]; then
    FONT_DST="$STORE_DIR/fonts/$(basename "$FONT_SRC")"
fi

# ---- SDL2 ----------------------------------------------------------------
SDL2=""
SDL2TTF=""
SDL2IMG=""
SDL_LIB_DIRS="/usr/lib/aarch64-linux-gnu /usr/lib/arm-linux-gnueabihf /usr/lib /usr/lib64 /usr/lib/compat"
for d in $SDL_LIB_DIRS; do
    for f in "$d"/libSDL2-2.0.so.* "$d"/libSDL2.so; do
        [ -e "$f" ] && { SDL2="$f"; break; }
    done
    [ -n "$SDL2" ] && break
done
for d in $SDL_LIB_DIRS; do
    for f in "$d"/libSDL2_ttf-2.0.so.* "$d"/libSDL2_ttf.so; do
        [ -e "$f" ] && { SDL2TTF="$f"; break; }
    done
    [ -n "$SDL2TTF" ] && break
done
for d in $SDL_LIB_DIRS; do
    for f in "$d"/libSDL2_image-2.0.so.* "$d"/libSDL2_image.so; do
        [ -e "$f" ] && { SDL2IMG="$f"; break; }
    done
    [ -n "$SDL2IMG" ] && break
done
for pair in "libSDL2-2.0:$SDL2" "libSDL2_ttf-2.0:$SDL2TTF" "libSDL2_image-2.0:$SDL2IMG"; do
    nm="${pair%%:*}"; ff="${pair#*:}"
    if [ -n "$ff" ]; then ok "$nm  $(basename "$ff")"; else bad "$nm 缺失 —— 图形界面起不来"; FAIL=1; fi
done

need_file "$GAMEPAD_DB" "手柄可能认不出来" \
    "/storage/.config/SDL-GameControllerDB/gamecontrollerdb.txt"

if [ ! -f "$ES_CFG" ]; then
    warn "找不到 $ES_CFG —— 无法确认入口目录，请核对 --es-dir"
else
    ES_OPT_PATH=$(awk -v want="$ES_ENTRY_SYSTEM" '
        /<system>/ { inblk=1; name=""; path="" }
        inblk && /<name>/ {
            n=$0; sub(/.*<name>/, "", n); sub(/<\/name>.*/, "", n); name=n
        }
        inblk && /<path>/ {
            p=$0; sub(/.*<path>/, "", p); sub(/<\/path>.*/, "", p); path=p
        }
        inblk && /<\/system>/ {
            if (name == want) { print path; exit }
            inblk=0
        }
    ' "$ES_CFG" 2>/dev/null)
    ES_OPT_PATH="${ES_OPT_PATH%/}"
    if [ -n "$ES_OPT_PATH" ] && [ "$ES_OPT_PATH" = "$ES_DIR" ]; then
        ok "es_systems.cfg 的 $ES_ENTRY_SYSTEM 系统 <path> = $ES_DIR/ ✓"
    elif [ -n "$ES_OPT_PATH" ]; then
        bad "es_systems.cfg 的 $ES_ENTRY_SYSTEM 系统指向 $ES_OPT_PATH/，与 --es-dir=$ES_DIR 不一致"
        bad "  入口脚本会放进 $ES_DIR，但 ES 只会去 $ES_OPT_PATH/ 找 —— 菜单里看不到"
        bad "  改用：sudo ./install.sh --es-dir $ES_OPT_PATH ..."
        FAIL=1
    else
        warn "es_systems.cfg 里没解析到 $ES_ENTRY_SYSTEM 系统"
    fi
fi

if [ -d "$ES_DIR" ]; then ok "ES 入口目录 $ES_DIR"
else
    bad "$ES_DIR 不存在"; FAIL=1
fi

# ---- 内核 cifs（只有 cifs 后端才需要）---------------------------------------
if [ "$BACKEND" = "cifs" ]; then
    if grep -qw cifs /proc/filesystems 2>/dev/null; then
        ok "内核 cifs 支持（/proc/filesystems 已列出）"
    elif modprobe cifs >/dev/null 2>&1 && grep -qw cifs /proc/filesystems 2>/dev/null; then
        ok "内核 cifs 模块已加载（modprobe 成功）"
    elif modprobe cifs >/dev/null 2>&1; then
        ok "内核 cifs 模块已加载"
    else
        bad "内核不支持 cifs —— 没有 cifs.ko，也没内建"
        bad "  这台机器的内核无法挂 SMB 共享（换固件/内核）"
        FAIL=1
    fi
else
    # ROCKNIX：明确确认「内核没有 cifs」是**预期**的，免得后面有人以为是故障
    if grep -qw cifs /proc/filesystems 2>/dev/null; then
        ok "内核 cifs 可用（本平台默认仍走 rclone，二者都可以）"
    else
        ok "内核没有 cifs（预期内）→ 使用 rclone FUSE 后端"
    fi
fi

# ---- 写权限 ---------------------------------------------------------------
TESTDIR="$STORE_DIR"
while [ -n "$TESTDIR" ] && [ ! -d "$TESTDIR" ]; do
    p=$(dirname "$TESTDIR"); [ "$p" = "$TESTDIR" ] && break; TESTDIR="$p"
done
if [ -w "$TESTDIR" ]; then
    ok "程序目录可写（$TESTDIR）"
else
    bad "程序目录不可写：$TESTDIR"
    [ "$PLATFORM" = "rocknix" ] && \
        bad "  ROCKNIX 的根是只读 squashfs，程序只能装到 /storage 下面"
    FAIL=1
fi

# ---- 自动补装（仅 arkos）---------------------------------------------------
if [ "$AUTO_APT" = "1" ] && [ -n "${MISSING_PKGS// /}" ]; then
    head_ "④b 自动补装缺失依赖"
    say "  缺：$MISSING_PKGS"
    # shellcheck disable=SC2086
    if apt-get install -y $MISSING_PKGS; then
        ok "补装完成"
        STILL=""
        for c in flock mountpoint nc; do
            command -v "$c" >/dev/null 2>&1 || STILL="$STILL $c"
        done
        [ "$BACKEND" = "cifs" ] && { command -v mount.cifs >/dev/null 2>&1 || STILL="$STILL mount.cifs"; }
        if [ -n "${STILL// /}" ]; then
            bad "补装后仍缺失：$STILL"; FAIL=1
        else
            ok "依赖复检通过"
        fi
    else
        bad "自动补装失败 —— 请手动执行：apt-get install $MISSING_PKGS"; FAIL=1
    fi
fi

if [ "$FAIL" -ne 0 ]; then
    if [ "$CHECK_ONLY" = "1" ]; then
        die "前置检查未通过（--check 是干跑，不会自动补装）。正式安装时安装器会自动补装；或现在手动装：apt-get install$MISSING_PKGS"
    fi
    die "前置检查未通过，请先补齐上面标 ✘ 的依赖"
fi

if [ "$CHECK_ONLY" = "1" ]; then
    say ""
    say "${C_G}前置检查全部通过。${C_0}去掉 --check 即可真正安装。"
    exit 0
fi

# ----------------------------------------------------------------- ⑤ 备份
head_ "⑤ 备份会被覆盖的文件"
STAMP=$(date '+%Y%m%d-%H%M%S')
BAK_DIR="$STORE_DIR/backup/$STAMP"
mkdir -p "$BAK_DIR"
chown "$TARGET_UID:$TARGET_GID" "$BAK_DIR" 2>/dev/null
BAK_COUNT=0

bak() {
    local f="$1" b="$2"
    [ -e "$f" ] || return 0
    if cp -p "$f" "$b" 2>/dev/null; then
        ok "备份 $f"
        BAK_COUNT=$((BAK_COUNT + 1))
    else
        warn "备份失败 $f"
    fi
}

if [ "$PLATFORM" != "rocknix" ]; then
    if [ -e "$MOUNT_SCRIPT" ] && [ ! -e "$MOUNT_SCRIPT.orig-bak" ]; then
        cp -p "$MOUNT_SCRIPT" "$MOUNT_SCRIPT.orig-bak" && ok "备份 $MOUNT_SCRIPT -> .orig-bak"
    elif [ -e "$MOUNT_SCRIPT.orig-bak" ]; then
        ok "$MOUNT_SCRIPT.orig-bak 已存在，保留最早的原件"
    fi
fi
bak "$UNIT"               "$BAK_DIR/$(basename "$UNIT")"
bak "$ES_DIR/RomStore.sh" "$BAK_DIR/RomStore.sh"
bak "$ES_DIR/gamelist.xml" "$BAK_DIR/gamelist.xml"
if [ -f "$STORE_DIR/source.json" ]; then
    cp -p "$STORE_DIR/source.json" "$BAK_DIR/source.json"
    ok "备份 source.json（本次未指定 --host 时不会改动它）"
    BAK_COUNT=$((BAK_COUNT + 1))
fi
if [ "$BAK_COUNT" -eq 0 ]; then
    say "  · 全新安装，没有需要备份的旧文件（升级时这里会列出被覆盖的文件）"
    rmdir "$BAK_DIR" 2>/dev/null || true
fi

# ----------------------------------------------------------------- ⑥ 程序
head_ "⑥ 安装程序文件"
mkdir -p "$STORE_DIR/romstore" "$STORE_DIR/log"

NPY=$(ls -1 "$SRC/romstore/"*.py | wc -l)
install -m 644 "$SRC/romstore/"*.py "$STORE_DIR/romstore/"
ok "romstore/*.py  （$NPY 个模块）"

install -m 755 "$SRC/run.py" "$STORE_DIR/run.py"
ok "run.py"

if [ -f "$SRC/../VERSION" ]; then
    install -m 644 "$SRC/../VERSION" "$STORE_DIR/VERSION"
    ok "VERSION（$(cat "$SRC/../VERSION")）"
fi

# 老版本（<=0.5.x）装的 Python 模块已经不需要了；新模块（platform.py）是必需的，
# 这里不删任何 .py，只清 __pycache__（旧 .pyc 会遮蔽新源码，实机踩过）
find "$STORE_DIR" -type d -name __pycache__ -prune -exec rm -rf -- {} + 2>/dev/null

# ---- 平台档案：运行期一切路径的来源 -----------------------------------------
cat > "$STORE_DIR/platform.json" <<JSONEOF
{
  "platform": "$PLATFORM",
  "platform_label": "$PLATFORM_LABEL",
  "store_dir": "$STORE_DIR",
  "nas_mount": "$MOUNT_POINT",
  "es_roms": "$ES_ROMS",
  "es_systems_cfg": "$ES_CFG",
  "es_entry_dir": "$ES_DIR",
  "es_entry_name": "$ES_ENTRY_NAME",
  "es_entry_system": "$ES_ENTRY_SYSTEM",
  "es_cache_db": "$([ "$PLATFORM" != "rocknix" ] && echo "$TARGET_HOME/.emulationstation/imagecache.db" || echo "/storage/.emulationstation/imagecache.db")",
  "es_settings_cfg": "$([ "$PLATFORM" != "rocknix" ] && echo "$TARGET_HOME/.emulationstation/es_settings.cfg" || echo "/storage/.emulationstation/es_settings.cfg")",
  "font": "$([ "$PLATFORM" = "rocknix" ] && [ -n "$FONT_DST" ] && echo "$FONT_DST" || echo "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")",
  "font_alt": "$([ "$PLATFORM" = "rocknix" ] && echo "$STORE_DIR/fonts/DroidSansFallback.ttf" || echo "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc")",
  "gamepad_db": "$GAMEPAD_DB",
  "sdl_video_driver": "$SDL_DRIVER",
  "mpv_vo": "$MPV_VO",
  "mpv_ao": "$MPV_AO",
  "preview_ao": "$PREVIEW_AO",
  "preview_tmp": "$PREVIEW_TMP",
  "mount_backend": "$BACKEND",
  "mount_script": "$MOUNT_SCRIPT",
  "systemd_dir": "$UNIT_DIR",
  "systemd_enable": "$SYSTEMD_ENABLE",
  "target_user": "$TARGET_USER",
  "roms_fs_selfheal": $([ "$PLATFORM" != "rocknix" ] && echo true || echo false),
  "extra_path": "$EXTRA_PATH",
  "samba_vers": "$SAMBA_VERS"
}
JSONEOF
chmod 644 "$STORE_DIR/platform.json"
ok "platform.json（$PLATFORM / 后端 $BACKEND / 挂载点 $MOUNT_POINT）"

# ---- CJK 字体：ROCKNIX 上一个系统字体都没有 ---------------------------------
if [ -n "$FONT_DST" ] && [ -n "$FONT_SRC" ]; then
    mkdir -p "$(dirname "$FONT_DST")"
    install -m 644 "$FONT_SRC" "$FONT_DST"
    ok "中文字体已装 $FONT_DST（$(basename "$FONT_SRC")）"
fi

# ---- rclone 的 fusermount3 名字桥接 -----------------------------------------
# rclone 1.71 挂 FUSE 时只找 "fusermount3"，而 ROCKNIX 只带了 fusermount(v2)。
# 实测：直接 mount 会报
#   CRITICAL: Fatal error: failed to mount FUSE fs: fusermount: exec:
#             "fusermount3": executable file not found in $PATH
# 放一个同名软链把名字补上即可（fusermount v2 和 v3 的 -o/-u 参数在这一用法下兼容）。
if [ "$BACKEND" = "rclone" ] && [ -n "$EXTRA_PATH" ]; then
    if ! command -v fusermount3 >/dev/null 2>&1; then
        _f2=$(command -v fusermount 2>/dev/null || true)
        if [ -n "$_f2" ]; then
            mkdir -p "$EXTRA_PATH"
            ln -sf "$_f2" "$EXTRA_PATH/fusermount3"
            ok "已建 fusermount3 桥接：$EXTRA_PATH/fusermount3 -> $_f2"
        else
            warn "没有 fusermount 可桥接 —— rclone 挂载会失败"
        fi
    else
        ok "本机已有 fusermount3，无需桥接"
    fi
fi

chown -R "$TARGET_UID:$TARGET_GID" "$STORE_DIR"
ok "目录归属已设为 $TARGET_USER"

# ----------------------------------------------------------------- ⑦ 模板替换
head_ "⑦ 生成入口脚本与挂载脚本"

# sed 的替换串里 & 和 \ 有特殊含义 —— 路径里通常没有，但别赌
sed_esc() { printf '%s' "$1" | sed -e 's/[&\\]/\\&/g'; }

render() {  # render <模板> <目标>
    # 空参数必须当场炸掉。踩过：UNIT 忘了赋值，sed 写到 ""、chmod ""、grep ""
    # 全都在报错但没一个能中止脚本，最后只在自检里留下一个莫名其妙的 ✘。
    [ -n "${1:-}" ] || die "render：模板参数为空"
    [ -n "${2:-}" ] || die "render：目标参数为空（模板 $1）"
    [ -r "$1" ]     || die "render：模板读不到 $1"
    sed -e "s|@@STORE_DIR@@|$(sed_esc "$STORE_DIR")|g" \
        -e "s|@@ES_DIR@@|$(sed_esc "$ES_DIR")|g" \
        -e "s|@@ES_ENTRY_NAME@@|$(sed_esc "$ES_ENTRY_NAME")|g" \
        -e "s|@@UID@@|$TARGET_UID|g" \
        -e "s|@@GID@@|$TARGET_GID|g" \
        -e "s|@@PLATFORM_LABEL@@|$(sed_esc "$PLATFORM_LABEL")|g" \
        -e "s|@@PLATFORM@@|$(sed_esc "$PLATFORM")|g" \
        -e "s|@@MOUNT_POINT@@|$(sed_esc "$MOUNT_POINT")|g" \
        -e "s|@@MOUNT_SCRIPT@@|$(sed_esc "$MOUNT_SCRIPT")|g" \
        -e "s|@@BACKEND@@|$(sed_esc "$BACKEND")|g" \
        -e "s|@@SAMBA_VERS@@|$(sed_esc "$SAMBA_VERS")|g" \
        -e "s|@@RCLONE_BIN@@|$(sed_esc "$RCLONE_BIN")|g" \
        -e "s|@@EXTRA_PATH@@|$(sed_esc "$EXTRA_PATH")|g" \
        -e "s|@@SDL_VIDEO_DRIVER@@|$(sed_esc "$SDL_DRIVER")|g" \
        -e "s|@@GAMEPAD_DB@@|$(sed_esc "$GAMEPAD_DB")|g" \
        -e "s|@@MPV_VO@@|$(sed_esc "$MPV_VO")|g" \
        -e "s|@@MPV_AO@@|$(sed_esc "$MPV_AO")|g" "$1" > "$2"
    [ -s "$2" ] || die "render：渲染结果为空（$1 -> $2）"
    if grep -q '@@[A-Z_]\{1,\}@@' "$2"; then
        bad "占位符未替换完：$2"
        grep -n '@@[A-Z_]\{1,\}@@' "$2" | head
        rm -f "$2"
        die "模板替换失败"
    fi
}

render "$SRC/RomStore.sh" "$ES_DIR/$ES_ENTRY_NAME"
chmod 755 "$ES_DIR/$ES_ENTRY_NAME"
chown "$TARGET_UID:$TARGET_GID" "$ES_DIR/$ES_ENTRY_NAME"
ok "$ES_DIR/$ES_ENTRY_NAME"

# 0.4.2 早期版本装过一个独立的「视频播放器」入口，后来改成商店详情页里播放。
# 升级时把旧入口清掉，免得菜单里留一个已经没用的条目。
if [ -e "$ES_DIR/VideoPlayer.sh" ]; then
    rm -f "$ES_DIR/VideoPlayer.sh" && ok "已移除旧版独立入口 $ES_DIR/VideoPlayer.sh"
fi

render "$SRC/nas-roms-mount.sh" "$MOUNT_SCRIPT.new"
chmod 755 "$MOUNT_SCRIPT.new"
chown 0:0 "$MOUNT_SCRIPT.new"
mkdir -p "$(dirname "$MOUNT_SCRIPT")"
mv -f "$MOUNT_SCRIPT.new" "$MOUNT_SCRIPT"
ok "$MOUNT_SCRIPT"

bash -n "$MOUNT_SCRIPT"        || die "挂载脚本语法错误"
ok "挂载脚本语法检查通过"
bash -n "$ES_DIR/$ES_ENTRY_NAME" || die "入口脚本语法错误"
ok "入口脚本语法检查通过"

# ----------------------------------------------------------------- ⑧ 商店源
head_ "⑧ 商店源配置"
as_user() {
    if [ "$TARGET_USER" = "root" ]; then
        env HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" \
            PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            python3 -m romstore.cli "$@"
    else
        sudo -u "$TARGET_USER" -H \
            env HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" \
                PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
            python3 -m romstore.cli "$@"
    fi
}

# 跑**内联脚本**（读 stdin）—— 用的时候必须配 heredoc：
#     as_user_run <<'PYEOF' ... PYEOF
# ⚠️ 别拿它跑 --probe 这类"参数"命令：`python3 -` 会去读 stdin，
#    没有 heredoc 时会一直等输入（0.6.0 改双平台时踩过）。
as_user_run() {
    if [ "$TARGET_USER" = "root" ]; then
        env HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" \
            RS_HOST="$RS_HOST" RS_SHARE="$RS_SHARE" \
            RS_SMBUSER="$RS_SMBUSER" RS_SMBPASS="$RS_SMBPASS" \
            python3 - "$@"
    else
        sudo -u "$TARGET_USER" -H \
            env HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" \
                RS_HOST="$RS_HOST" RS_SHARE="$RS_SHARE" \
                RS_SMBUSER="$RS_SMBUSER" RS_SMBPASS="$RS_SMBPASS" \
            python3 - "$@"
    fi
}

# 跑一个**磁盘上的入口文件**（run.py --probe / --display-test 等）。
# 这里的 SDL 环境变量必须与 RomStore.sh 渲染出来的那套一致，
# 否则 ROCKNIX 上探针会落到 offscreen 驱动上，报出假的失败。
as_user_py() {   # as_user_py <入口文件> [参数...]
    local entry="$1"; shift
    local _path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    [ -n "$EXTRA_PATH" ] && _path="$EXTRA_PATH:$_path"
    local -a ENVV=(
        HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" PATH="$_path"
    )
    [ -n "$SDL_DRIVER" ]  && ENVV+=("SDL_VIDEODRIVER=$SDL_DRIVER")
    [ -n "$GAMEPAD_DB" ]  && [ -e "$GAMEPAD_DB" ] && \
        ENVV+=("SDL_GAMECONTROLLERCONFIG_FILE=$GAMEPAD_DB")
    if [ "$SDL_DRIVER" = "wayland" ]; then
        local xd="${XDG_RUNTIME_DIR:-}"
        if [ -z "$xd" ]; then
            for d in /var/run/0-runtime-dir /run/user/0 /run/user/1000; do
                [ -d "$d" ] && { xd="$d"; break; }
            done
        fi
        [ -n "$xd" ] && ENVV+=("XDG_RUNTIME_DIR=$xd")
        if [ -n "$xd" ]; then
            for w in wayland-1 wayland-0; do
                [ -e "$xd/$w" ] && { ENVV+=("WAYLAND_DISPLAY=$w"); break; }
            done
        fi
    fi
    cd "$STORE_DIR" || return 1
    if [ "$TARGET_USER" = "root" ]; then
        env "${ENVV[@]}" python3 "$entry" "$@"
    else
        sudo -u "$TARGET_USER" -H env "${ENVV[@]}" python3 "$entry" "$@"
    fi
}

if [ -n "$RS_HOST" ]; then
    as_user_run <<'PYEOF'
import os
from romstore import source
cfg = source.load()
cfg['host'] = os.environ['RS_HOST']
if os.environ.get('RS_SHARE'):
    cfg['share'] = os.environ['RS_SHARE']
cfg['user'] = os.environ.get('RS_SMBUSER', '')
cfg['password'] = os.environ.get('RS_SMBPASS', '')
cfg = source.save(cfg)
print('  已写入 %s' % source.SOURCE_PATH)
print('  共享 = %s' % source.unc_path(cfg))
print('  账号 = %s' % (cfg['user'] or '（匿名 guest）'))
PYEOF
    ok "source.json 已更新"
else
    if [ -f "$STORE_DIR/source.json" ]; then
        ok "沿用已有 source.json（未指定 --host）"
    else
        warn "没有指定 --host，先用内置默认：//10.168.1.100/Roms 匿名"
        warn "  装完可在商店里按 Start →「修改商店源」改"
    fi
fi
as_user --source-show 2>/dev/null | sed 's/^/  /' || true

# ----------------------------------------------------------------- ⑨ systemd
head_ "⑨ 挂载服务（systemd）"
if [ ! -d "$UNIT_DIR" ]; then
    mkdir -p "$UNIT_DIR" 2>/dev/null || die "建不了单元目录 $UNIT_DIR"
fi
render "$UNIT_TEMPLATE" "$UNIT"
chmod 644 "$UNIT"
ok "$UNIT（$(basename "$UNIT_TEMPLATE")，后端 $BACKEND）"
# 挂载改由 timer 在开机后异步触发（见 systemd/nas-roms.timer 的说明）：
# 不让 oneshot 服务挂在 multi-user.target 上，避免开机被 NAS 超时拖住。
render "$TIMER_TEMPLATE" "$TIMER"
chmod 644 "$TIMER"
ok "$TIMER（$(basename "$TIMER_TEMPLATE")，开机后异步挂载，不阻塞启动）"

# /roms 只读自愈：exFAT 无 journal，掉电会截断 FAT 表导致整盘只读（Errno 30）
# ⚠️ 只装 arkos / darkos：ROCKNIX 的 /storage/roms 是 mmcblk1p3，由固件的 udev 自动挂载，
#    没有 roms.mount 单元可重启，自愈脚本那套 umount/fsck/remount 用不上。
#    （darkos 的 /roms 是 mmcblk0p3，同样带 errors=remount-ro，实测确认过。）
if [ "$PLATFORM" != "rocknix" ]; then
    HEAL_SH="/opt/system/roms-fs-selfheal.sh"
    HEAL_UNIT="/etc/systemd/system/roms-fs-selfheal.service"
    WATCH_SH="/opt/system/roms-fs-watch.sh"
    WATCH_UNIT="/etc/systemd/system/roms-fs-watch.service"

    if [ -r "$SRC/systemd/roms-fs-selfheal.sh" ] && [ -r "$SYSD/roms-fs-selfheal.service" ]; then
        bak "$HEAL_SH" "$BAK_DIR/roms-fs-selfheal.sh"
        bak "$HEAL_UNIT" "$BAK_DIR/roms-fs-selfheal.service"
        install -m 755 "$SRC/systemd/roms-fs-selfheal.sh" "$HEAL_SH"
        install -m 644 "$SYSD/roms-fs-selfheal.service" "$HEAL_UNIT"
        ok "只读自愈：$HEAL_SH"
    else
        warn "分发包缺少自愈脚本，跳过（不影响主功能）"
    fi

    if [ -r "$SRC/systemd/roms-fs-watch.sh" ] && [ -r "$SYSD/roms-fs-watch.service" ]; then
        bak "$WATCH_SH" "$BAK_DIR/roms-fs-watch.sh"
        bak "$WATCH_UNIT" "$BAK_DIR/roms-fs-watch.service"
        install -m 755 "$SRC/systemd/roms-fs-watch.sh" "$WATCH_SH"
        install -m 644 "$SYSD/roms-fs-watch.service" "$WATCH_UNIT"
        ok "只读监控：$WATCH_SH"
    else
        warn "分发包缺少只读监控脚本，跳过（不影响主功能）"
    fi

    if [ -r "$SYSD/50-nas-roms" ]; then
        if [ -d /etc/NetworkManager ]; then
            bak "/etc/NetworkManager/dispatcher.d/50-nas-roms" "$BAK_DIR/50-nas-roms"
            install -m 755 -o root -g root "$SYSD/50-nas-roms" \
                /etc/NetworkManager/dispatcher.d/50-nas-roms
            ok "/etc/NetworkManager/dispatcher.d/50-nas-roms（网络重连时自动补挂）"
        else
            warn "系统没有 NetworkManager，跳过 dispatcher"
        fi
    fi
else
    warn "跳过 /roms 只读自愈（ROCKNIX 的 ROM 分区由固件 udev 挂载，路径与机制不同）"
    warn "  → 真出问题时手工：停 essway、umount /storage/roms、fsck.exfat -y、重新挂载"
fi

# fsck.exfat 不在基础镜像里（arkos 与 darkos 都一样），自愈要靠它，先确保装上
if [ "$PLATFORM" != "rocknix" ] && ! command -v fsck.exfat >/dev/null 2>&1; then
    if [ "$AUTO_APT" = "1" ]; then
        warn "fsck.exfat 未安装 —— /roms 只读自愈需要它，正在补装 exfatprogs"
        if DEBIAN_FRONTEND=noninteractive apt-get install -y exfatprogs >/dev/null 2>&1; then
            ok "已补装 exfatprogs"
        else
            warn "  补装失败（离线？）—— 掉电后需手工 fsck"
        fi
    else
        warn "fsck.exfat 未安装 —— /roms 只读自愈需要它（apt-get install exfatprogs）"
    fi
fi

systemctl daemon-reload

enable_unit() {   # enable_unit <单元名>
    local u="$1"
    local src="$UNIT_DIR/$u"
    # wants 目录随单元类型走：.timer → timers.target，其余 → multi-user.target
    local want="multi-user.target"
    case "$u" in
        *.timer) want="timers.target" ;;
    esac
    if [ ! -f "$src" ]; then
        bad "$u 的单元文件不存在（$src）—— 跳过自启设置"
        warn "  这通常是安装器内部错误（单元没生成成功），请把上面 ⑨ 的输出发出来"
        return 1
    fi
    if [ "$SYSTEMD_ENABLE" = "symlink" ]; then
        # ROCKNIX：/etc/systemd/system 根本不存在（根是只读 squashfs），
        # `systemctl enable` 无处写软链。固件自己的做法就是在 unit 目录下
        # 建 <target>.wants/ 软链（sshd.service 就是这么启用的）。
        mkdir -p "$UNIT_DIR/$want.wants"
        ln -sf "$src" "$UNIT_DIR/$want.wants/$u"
        systemctl daemon-reload >/dev/null 2>&1
        if systemctl is-enabled "$u" >/dev/null 2>&1; then
            ok "$u 已设为开机自启（$UNIT_DIR/$want.wants/ 软链）"
        else
            warn "$u 软链已建但 systemd 未认（稍后 systemctl enable $u 试试）"
        fi
    else
        if systemctl enable "$u" >/dev/null 2>&1; then
            ok "$u 已设为开机自启"
        else
            warn "$u enable 失败，可稍后手动：systemctl enable ${u%.*}"
        fi
    fi
}

# ⚠️ 启用的是 timer，不是 service —— 见 systemd/nas-roms.timer 的说明。
#    先把旧版残留的开机软链清掉（它会带来 Before=multi-user.target，拖慢开机）。
if [ -L "$LEGACY_UNIT_WANTS" ]; then
    rm -f "$LEGACY_UNIT_WANTS"
    ok "已清理旧版开机软链（multi-user.target.wants/nas-roms.service）"
fi
enable_unit "nas-roms.timer"
[ -f "$HEAL_UNIT" ]  && enable_unit "roms-fs-selfheal.service"
[ -f "$WATCH_UNIT" ] && enable_unit "roms-fs-watch.service"

# ----------------------------------------------------------------- ⑩ ES 崩溃保护
if [ "$WITH_ES_GUARD" = "1" ]; then
    head_ "⑩ ES 崩溃循环上限保护（可选）"
    if [ "$PLATFORM" != "arkos" ]; then
        warn "该保护只适配 ArkOS 的 emulationstation.service，本平台跳过"
    elif [ -r "$EXTRAS/10-crash-limit.conf" ]; then
        install -D -m 644 "$EXTRAS/10-crash-limit.conf" \
            /etc/systemd/system/emulationstation.service.d/10-crash-limit.conf
        systemctl daemon-reload
        ok "已装 10-crash-limit.conf（ES 连崩 5 次即停下，不再无限重启）"
    else
        warn "找不到 extras/10-crash-limit.conf，跳过"
    fi
fi

# ----------------------------------------------------------------- ⑪ 挂载
if [ "$DO_MOUNT" = "1" ]; then
    head_ "⑪ 挂载 NAS"
    # 走 systemd 而不是直接调脚本 —— 实机踩到过：
    # 直接跑脚本会把挂载建起来，但 nas-roms.service 仍显示 inactive(dead)，
    # 于是「服务状态」和「实际挂载」对不上，验收时误判成故障。
    systemctl start nas-roms.service 2>/dev/null || true
    # 等服务把挂载建起来（rclone 首次挂载 3~10 秒）**再**决定要不要人工补一刀。
    # 别一上来就再跑一遍脚本 —— 两个实例会撞在 /run/nas-roms.lock 上，
    # 后到的那个只能报"让位"，输出看着像失败其实一切正常。
    _i=0
    while [ "$_i" -lt 30 ]; do
        mountpoint -q "$MOUNT_POINT" 2>/dev/null && break
        sleep 1; _i=$((_i + 1))
    done
    if ! mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        say "  服务没挂上，直接调脚本看详细报错："
        "$MOUNT_SCRIPT" --force 2>&1 | sed 's/^/  /' || true
        systemctl start nas-roms.service >/dev/null 2>&1 || true
    fi
    if mountpoint -q "$MOUNT_POINT" 2>/dev/null && ls "$MOUNT_POINT" >/dev/null 2>&1; then
        ok "$MOUNT_POINT 已挂载（$(ls -A "$MOUNT_POINT" 2>/dev/null | wc -l) 个条目，后端 $BACKEND）"
        if systemctl is-active nas-roms.service >/dev/null 2>&1; then
            ok "nas-roms.service = active（与挂载状态一致）"
        else
            warn "nas-roms.service 显示 $(systemctl is-active nas-roms.service 2>/dev/null)"
            warn "  挂载已生效，但服务未跟踪 —— 不影响使用"
        fi
    else
        warn "$MOUNT_POINT 未能挂载"
        warn "  确认 NAS 已开机、共享名正确，然后在商店里 Start →「修改商店源」改对"
    fi
else
    head_ "⑪ 挂载 NAS"
    say "  按 --no-mount 跳过"
fi

# ----------------------------------------------------------------- ⑫ 索引
if [ "$DO_INDEX" = "1" ] && mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    head_ "⑫ 建立索引（首次约 10–60 秒）"
    as_user --build 2>&1 | sed 's/^/  /'
    if as_user --stats >/dev/null 2>&1; then
        ok "索引就绪"
    else
        warn "索引可能不完整，商店首次启动时会自动重建"
    fi
elif [ "$DO_INDEX" = "1" ]; then
    head_ "⑫ 建立索引"
    warn "NAS 未挂载，跳过建索引（商店首次启动时会自动重建）"
fi

# ----------------------------------------------------------------- ⑬ ES 入口
head_ "⑬ 注册 EmulationStation 入口"
if as_user --register >/dev/null 2>&1; then
    ok "$ES_ENTRY_SYSTEM → 「ROM 商店」已注册（$ES_DIR/gamelist.xml）"
else
    warn "注册失败，可手动跑：python3 -m romstore.cli --register"
fi

# ----------------------------------------------------------------- ⑭ 自检
head_ "⑭ 安装后自检"
VERIFY_FAIL=0
v_ok()   { printf '  %s✔%s %s\n' "$C_G" "$C_0" "$*"; }
v_bad()  { printf '  %s✘%s %s\n' "$C_R" "$C_0" "$*"; VERIFY_FAIL=1; }
v_warn() { printf '  %s!%s %s\n' "$C_Y" "$C_0" "$*"; }

if [ -r "$STORE_DIR/run.py" ] && [ -r "$STORE_DIR/romstore/ui.py" ]; then
    v_ok "程序本体就位（run.py + romstore/）"
else
    v_bad "程序本体不完整（$STORE_DIR）"
fi

if [ -r "$STORE_DIR/romstore/platform.py" ] && [ -r "$STORE_DIR/platform.json" ]; then
    v_ok "平台档案就位（platform.json + platform.py）"
else
    v_bad "缺少平台档案（$STORE_DIR/platform.json）"
fi

if [ -r "$STORE_DIR/romstore/media.py" ]; then
    v_ok "封面模块就位（romstore/media.py）"
else
    v_bad "缺少 romstore/media.py（封面导入不可用）"
fi

if [ -r "$STORE_DIR/VERSION" ]; then
    v_ok "版本号已记录（$(cat "$STORE_DIR/VERSION")）"
else
    v_bad "缺少 VERSION（不影响功能）"
fi

if [ -x "$ES_DIR/$ES_ENTRY_NAME" ]; then
    v_ok "ES 入口脚本可执行（$ES_DIR/$ES_ENTRY_NAME）"
else
    v_bad "ES 入口脚本缺失或不可执行（$ES_DIR/$ES_ENTRY_NAME）"
fi
if [ -f "$ES_DIR/gamelist.xml" ] && grep -q "$ES_ENTRY_NAME" "$ES_DIR/gamelist.xml"; then
    v_ok "gamelist.xml 里有「ROM 商店」条目"
else
    v_bad "gamelist.xml 里没找到入口条目"
fi

if command -v mpv >/dev/null 2>&1; then
    v_ok "mpv 可用（按 X 的全屏预览，vo=${MPV_VO:-默认}）"
else
    v_warn "没找到 mpv —— 商店按 X 无法播放全屏预览视频"
fi

# 右栏「内嵌预览」需要**解码器**，两个平台的路子不一样：
#   arkos   有 ffmpeg 命令行 -> ffmpeg 管道
#   rocknix **没有 ffmpeg**，只有 mpv -> mpv 帧服务（带硬解）
# 两个都没有是硬伤（右栏只剩封面），但**不能算失败** —— 安装与游戏本身
# 都照常可用，所以这里只警告。
if command -v ffmpeg >/dev/null 2>&1; then
    v_ok "右栏内嵌预览可用：ffmpeg 管道（$(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f1)）"
elif command -v mpv >/dev/null 2>&1; then
    v_ok "右栏内嵌预览可用：mpv 帧服务（本机没有 ffmpeg 命令行，改用 mpv --vo=image）"
else
    v_warn "右栏内嵌预览不可用：既没有 ffmpeg 也没有 mpv（右栏只显示封面，不影响安装）"
fi

if [ -f "$STORE_DIR/source.json" ]; then
    v_ok "source.json 存在（$(as_user --source-show 2>/dev/null | sed -n 's/^共享 *: *//p' | head -1)）"
else
    v_bad "source.json 不存在 —— 商店会回落到内置默认源"
fi

if mountpoint -q "$MOUNT_POINT" 2>/dev/null && ls "$MOUNT_POINT" >/dev/null 2>&1; then
    v_ok "NAS 已挂载（$MOUNT_POINT，$(ls -A "$MOUNT_POINT" 2>/dev/null | wc -l) 个条目）"
else
    v_bad "NAS 未挂载 —— 商店会显示空库（进商店后 Start →「修改商店源」可改）"
fi

if [ -f "$STORE_DIR/index.db" ] && as_user --stats >/dev/null 2>&1; then
    v_ok "索引可读（$(as_user --stats 2>/dev/null | sed -n 's/^游戏条目（去重后）: *//p' | head -1) 款）"
else
    v_bad "索引缺失或不可读 —— 商店启动时会自动重建"
fi

if systemctl is-enabled nas-roms.timer >/dev/null 2>&1; then
    v_ok "nas-roms.timer 开机自启已启用（挂载异步执行，不阻塞开机）"
else
    v_bad "nas-roms.timer 未启用 —— 开机不会自动挂载 NAS"
fi
# 反向校验：旧版把 service 挂在 multi-user.target.wants 的残留必须不在，
# 否则 systemd 会给它加 Before=multi-user.target，开机要等挂载跑完。
if [ -L "$LEGACY_UNIT_WANTS" ]; then
    v_warn "残留旧版软链 multi-user.target.wants/nas-roms.service —— 会让开机等待挂载，建议删除"
fi

if [ "$PLATFORM" != "rocknix" ]; then
    if [ -f "$HEAL_SH" ] && command -v fsck.exfat >/dev/null 2>&1; then
        if systemctl is-enabled roms-fs-selfheal.service >/dev/null 2>&1; then
            v_ok "roms-fs-selfheal.service 已启用（掉电只读自动修复）"
        else
            v_warn "roms-fs-selfheal.service 未启用，可手动：systemctl enable roms-fs-selfheal"
        fi
    else
        v_warn "未安装 /roms 只读自愈（或系统无 fsck.exfat）—— 掉电后需手工 fsck"
    fi
    if [ -f "$WATCH_UNIT" ]; then
        if systemctl is-enabled roms-fs-watch.service >/dev/null 2>&1; then
            v_ok "roms-fs-watch.service 已启用（运行中只读自动发现）"
        else
            v_warn "roms-fs-watch.service 未启用，可手动：systemctl enable roms-fs-watch"
        fi
    else
        v_warn "未安装 /roms 只读监控 —— 运行中损坏需手工 SSH 排查"
    fi
else
    v_warn "本平台未装 ROM 分区自愈（机制不同），掉电后需手工 fsck.exfat"
fi

# ---- 图形环境（无头探针，不占屏）------------------------------------------
# ⚠️ 走 run.py 而不是 romstore.cli —— --probe 在 run.py 里。
#    （as_user_run 是给内联 heredoc 用的，拿它跑 --probe 会让 python3 空等 stdin）
PROBE=$( as_user_py run.py --probe 2>&1 ) || true
if printf '%s' "$PROBE" | grep -q 'SDL_Init(VIDEO): OK'; then
    v_ok "SDL2 视频初始化 OK（$(printf '%s' "$PROBE" | sed -n 's/.*当前驱动 = //p' | head -1)）"
else
    v_bad "SDL2 视频初始化失败 —— 图形界面可能起不来"
fi
CTRL=$(printf '%s' "$PROBE" | sed -n 's/^手柄数量[^:]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -1)
if [ "${CTRL:-0}" -gt 0 ] 2>/dev/null; then
    v_ok "手柄识别到 $CTRL 个"
else
    v_bad "没识别到手柄 —— 检查 $GAMEPAD_DB"
fi
if printf '%s' "$PROBE" | grep -q '中文字体.*存在'; then
    v_ok "中文字体可用（$(printf '%s' "$PROBE" | sed -n 's/^中文字体 *: *//p' | awk '{print $1}' | head -1)）"
else
    v_bad "中文字体缺失 —— 界面中文会显示为方块"
fi

say ""
if [ "$VERIFY_FAIL" = "0" ]; then
    say "  ${C_G}${C_BOLD}自检全部通过 —— 可以直接在掌机上用了。${C_0}"
else
    say "  ${C_Y}${C_BOLD}自检有未通过项（上面标 ✘）—— 不影响已装好的部分，按提示处理即可。${C_0}"
fi

# ----------------------------------------------------------------- 全屏预览体检
# ⚠️ Mali 固件（RK3326/RK3566 掌机常见）会把 libgbm.so.1 / .so.1.0.0 全抢去
#    指向闭源 libMali.so，导致 mpv 加载即报
#       undefined symbol: gbm_surface_create_with_modifiers
#    那是**加载期**错误，换 --vo 也没用 —— 表现为「按 X 一点反应都没有」，
#    很容易被误判成视频格式不支持。这里主动体检 + 修。
head_ "⑮ 全屏预览（mpv）体检"
GBM_FIX_SH="/opt/system/mpv-libgbm-fix.sh"
if [ "$PLATFORM" = "rocknix" ]; then
    say "  本平台不走 mpv 全屏接力（右栏用 mpv 帧服务），跳过"
elif command -v mpv >/dev/null 2>&1; then
    mkdir -p /opt/system
    if [ -r "$SRC/systemd/mpv-libgbm-fix.sh" ]; then
        # 模板里有 @@STORE_DIR@@ 占位符（要渲染，跟 systemd 单元同一套机制）
        sed "s|@@STORE_DIR@@|$STORE_DIR|g" "$SRC/systemd/mpv-libgbm-fix.sh" > "$GBM_FIX_SH"
        chmod 755 "$GBM_FIX_SH"
        bak "$GBM_FIX_SH" "$BAK_DIR/mpv-libgbm-fix.sh"
        ok "$GBM_FIX_SH"
        if bash "$GBM_FIX_SH"; then
            # 修出来的库归 ark：商店是以 $TARGET_USER 跑的，
            # root 属主虽然只读也能用，但自愈重写时（非 root 场景）会 EPERM。
            chown -R "$TARGET_UID:$TARGET_GID" "$STORE_DIR/lib" 2>/dev/null || true
            ok "mpv 全屏预览可用"
        else
            warn "mpv 仍起不来 —— 全屏预览不可用（不影响安装/浏览/游玩）"
            warn "  详细原因：journalctl -t romstore-libgbm-fix -n 30"
        fi
    else
        warn "分发包缺少 mpv-libgbm-fix.sh，跳过体检"
    fi
else
    say "  没装 mpv —— 按 X 无全屏预览（浏览/安装/游玩不受影响）"
    say "  补装：apt-get install mpv"
fi

# ----------------------------------------------------------------- 总结
head_ "完成"
say "  版本      $VERSION"
say "  平台      $PLATFORM（$PLATFORM_LABEL）"
say "  程序目录  $STORE_DIR"
say "  入口      $ES_DIR/$ES_ENTRY_NAME"
say "  挂载脚本  $MOUNT_SCRIPT（后端 $BACKEND）"
say "  挂载点    $MOUNT_POINT"
say "  单元目录  $UNIT_DIR"
say "  备份目录  $BAK_DIR"
say ""
# 入口系统：arkos 走 Options（自家固件菜单），rocknix 与 darkos 都走 Ports
if [ "$PLATFORM" = "arkos" ]; then
    say "  ${C_BOLD}掌机怎么用：EmulationStation → Options → 「ROM 商店」${C_0}"
else
    say "  ${C_BOLD}掌机怎么用：EmulationStation → Ports → 「ROM 商店」${C_0}"
fi
say "    十字键/左摇杆 移动   右摇杆 滚简介"
say "    A 安装   X 播预览   Y 卸载   L1/R1 翻页   Start 菜单   B 返回"
say "    Start →「修改商店源」可在掌机上直接换 NAS，不用电脑"
say ""
if [ "$PLATFORM" = "rocknix" ]; then
    say "  ${C_Y}ES 里若看不到「ROM 商店」，执行 pkill emulationstatio 即可"
    say "  （essway.service 带 Restart=always，会自动拉起新 ES 并重读 gamelist）${C_0}"
elif [ "$PLATFORM" = "darkos" ]; then
    say "  ${C_Y}ES 里若看不到「ROM 商店」，重启一次 EmulationStation 即可${C_0}"
    say "  ${C_Y}（入口在 Ports 下；gamelist.xml 直接放/roms/ports/，无 xmlns 头）${C_0}"
else
    say "  ${C_Y}ES 里若看不到「ROM 商店」，重启一次 EmulationStation 即可。${C_0}"
fi
if [ "$BACKEND" = "rclone" ]; then
    say ""
    say "  ${C_Y}NAS 走后端 rclone 的 FUSE 挂载（本机内核没有 cifs）。${C_0}"
    say "  排障：tail -20 $STORE_DIR/log/rclone.log"
    say "        $MOUNT_SCRIPT --status"
fi
say ""
