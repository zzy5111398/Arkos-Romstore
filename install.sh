#!/bin/bash
# =============================================================================
#  ROM 商店（局域网 SMB 版）—— 一键安装 / 升级
#
#  适用：ArkOS4Clone（R36S / R36 Ultra / 同类 aarch64 掌机，用户 ark，uid 1002）
#  用法：
#     sudo ./install.sh --host 10.168.1.100 --share Roms
#     sudo ./install.sh                      # 沿用已有 source.json（全新装则用内置默认）
#     sudo ./install.sh --check              # 只做前置检查，不动任何文件
#     sudo ./install.sh --help
#
#  本脚本是**幂等**的：重复运行 = 升级（源码会被覆盖，source.json 与索引会保留）。
#  卸载用 ./uninstall.sh
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
TARGET_USER=""          # 空 = 自动探测
ES_DIR="/opt/system"    # es_systems.cfg 里 options 系统的 <path>
ES_DIR_GIVEN=0          # 用户是否显式 --es-dir（否则尝试从 es_systems.cfg 自动探测）
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

MOUNT_SCRIPT="/usr/local/sbin/nas-roms-mount.sh"
UNIT="/etc/systemd/system/nas-roms.service"
DISPATCHER="/etc/NetworkManager/dispatcher.d/50-nas-roms"
MOUNT_POINT="/mnt/Roms"

# /roms 只读自愈
HEAL_SH="/opt/system/roms-fs-selfheal.sh"
HEAL_UNIT="/etc/systemd/system/roms-fs-selfheal.service"
WATCH_SH="/opt/system/roms-fs-watch.sh"
WATCH_UNIT="/etc/systemd/system/roms-fs-watch.service"

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
ROM 商店安装器 v$VERSION

用法:
  sudo ./install.sh [选项]

商店源（可省；省了则沿用已有 source.json 或内置默认 10.168.1.100/Roms 匿名）:
  --host H            NAS 主机（IP 或主机名），例：10.168.1.100
  --share S           共享名（不带 // 与主机），例：Roms
  --smbuser U         SMB 账号（留空 = 匿名 guest）
  --smbpass P         SMB 密码

安装位置（一般不用改）:
  --target-user U     程序归属的 Linux 用户（默认自动探测；本机是 ark）
  --es-dir D          EmulationStation 的 options 系统目录（默认 /opt/system）
  --no-mount          装完不立刻挂载
  --skip-index        装完不建索引
  --with-es-guard     额外安装 ES 崩溃循环上限保护（见 extras/）
  --no-auto-apt       缺依赖时不自动 apt-get 补装，只报错停下
  --check             只做前置检查，不写任何文件
  -h, --help          显示本帮助

装完怎么用:
  掌机 EmulationStation → Options → 「ROM 商店」
  A 进入/详情   X 安装   Y 卸载   L1/R1 翻页   Start 菜单   B 返回

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
        exec sudo -- "$0" "$@"
    fi
    die "请用 root 运行：sudo $0"
fi

say ""
say "${C_BOLD}ROM 商店 安装器${C_0}  v$VERSION"

# ----------------------------------------------------------------- 1. 文件自检
head_ "① 安装包完整性"
[ -d "$SRC" ] || die "找不到 src/ 目录（安装包不完整？当前目录 $HERE）"
for f in run.py RomStore.sh nas-roms-mount.sh; do
    [ -r "$SRC/$f" ] || die "缺少 src/$f"
done
[ -r "$SRC/romstore/ui.py" ] || die "缺少 src/romstore/ui.py"
[ -r "$SYSD/nas-roms.service" ] || die "缺少 systemd/nas-roms.service"
ok "安装包完整（$HERE）"

# ----------------------------------------------------------------- 2. 目标用户
head_ "② 目标用户"
if [ -z "$TARGET_USER" ]; then
    if id ark >/dev/null 2>&1; then
        TARGET_USER="ark"
    else
        TARGET_USER=""
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
STORE_DIR="$TARGET_HOME/.romstore"

ok "用户 = $TARGET_USER (uid=$TARGET_UID gid=$TARGET_GID)"
ok "程序目录 = $STORE_DIR"

# ---- ES options 目录：没显式给 --es-dir 就从 es_systems.cfg 自动探测 ---------
# 入口能不能出现在 ES 菜单里，唯一取决于 options 系统的 <path>。
# 与其硬编码默认值再事后警告，不如直接问配置文件要 —— 探测失败才回落到默认。
if [ "$ES_DIR_GIVEN" != "1" ]; then
    _cfg=/etc/emulationstation/es_systems.cfg
    if [ -f "$_cfg" ]; then
        _p=$(awk '
            /<system>/ { inblk=1; name=""; path="" }
            inblk && /<name>/ {
                n=$0; sub(/.*<name>/, "", n); sub(/<\/name>.*/, "", n); name=n
            }
            inblk && /<path>/ {
                p=$0; sub(/.*<path>/, "", p); sub(/<\/path>.*/, "", p); path=p
            }
            inblk && /<\/system>/ {
                if (name == "options") { print path; exit }
                inblk=0
            }
        ' "$_cfg" 2>/dev/null)
        _p="${_p%/}"
        if [ -n "$_p" ] && [ "$_p" != "$ES_DIR" ]; then
            ok "ES options 目录自动探测：$_p（覆盖默认 $ES_DIR）"
            ES_DIR="$_p"
        fi
    fi
fi

# ----------------------------------------------------------------- 3. 依赖
head_ "③ 依赖检查"
FAIL=0
MISSING_PKGS=""     # 可自动补装的 apt 包名，攒起来一次装完

# need_cmd <命令> <用途> [<apt包名>]
# 给了 apt 包名且本机有 apt-get 时，先不判死 —— 记下来，稍后自动补装。
need_cmd() {
    local cmd="$1" why="$2" pkg="${3:-}"
    if command -v "$cmd" >/dev/null 2>&1; then
        ok "$cmd  $(command -v "$cmd")"
        return 0
    fi
    if [ -n "$pkg" ]; then
        # 有包名就记下来 —— 不管 --check 与否，都要能在提示里给出确切包名
        MISSING_PKGS="$MISSING_PKGS $pkg"
        if [ "$AUTO_APT" = "1" ]; then
            warn "$cmd 未安装 —— $why（将自动补装 $pkg）"
            return 0
        fi
        bad "$cmd 未安装 —— $why"
        bad "  补装：apt-get install $pkg"
        FAIL=1
        return 1
    fi
    bad "$cmd 未安装 —— $why"
    FAIL=1
    return 1
}

# 是否允许安装器自己跑 apt-get 补依赖。
# 默认：真机上存在 apt-get 且未显式 --no-auto-apt 时允许。
# --check 是干跑（承诺「不动任何文件」），此时一律不补装，只报告缺什么。
AUTO_APT="${AUTO_APT:-0}"
if [ "$NO_AUTO_APT" != "1" ] && [ "$CHECK_ONLY" != "1" ] \
   && command -v apt-get >/dev/null 2>&1; then
    AUTO_APT=1
fi

need_cmd python3    "本程序是 Python 写的"
need_cmd mount.cifs "挂载 SMB 共享（cifs 内核 + mount.cifs 用户态 helper）" cifs-utils
need_cmd flock      "挂载并发串行化（util-linux）" util-linux
need_cmd mountpoint "判断挂载状态（util-linux）" util-linux
need_cmd nc         "探测 NAS 的 445 端口（netcat）" netcat-openbsd

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

FONT=""
for f in /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
         /usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc; do
    [ -f "$f" ] && { FONT="$f"; break; }
done
if [ -n "$FONT" ]; then
    ok "中文字体 $(basename "$FONT")"
else
    warn "没找到 Noto CJK 字体 —— 界面中文会显示为空白"
    warn "  补装：apt-get install fonts-noto-cjk"
fi

SDL2=""
for d in /usr/lib/aarch64-linux-gnu /usr/lib/arm-linux-gnueabihf /usr/lib; do
    for f in "$d"/libSDL2-2.*.so.* "$d"/libSDL2.so; do
        [ -e "$f" ] && { SDL2="$f"; break; }
    done
    [ -n "$SDL2" ] && break
done
if [ -n "$SDL2" ]; then ok "SDL2 $(basename "$SDL2")"
else bad "没找到 libSDL2 —— 图形界面起不来（apt-get install libsdl2-2.0-0）"; FAIL=1; fi

TTF=""
for d in /usr/lib/aarch64-linux-gnu /usr/lib/arm-linux-gnueabihf /usr/lib; do
    for f in "$d"/libSDL2_ttf-2.*.so.* "$d"/libSDL2_ttf.so; do
        [ -e "$f" ] && { TTF="$f"; break; }
    done
    [ -n "$TTF" ] && break
done
if [ -n "$TTF" ]; then ok "SDL2_ttf $(basename "$TTF")"
else bad "没找到 libSDL2_ttf（apt-get install libsdl2-ttf-2.0-0）"; FAIL=1; fi

if [ -r /opt/inttools/gamecontrollerdb.txt ]; then
    ok "手柄映射库 /opt/inttools/gamecontrollerdb.txt"
else
    warn "/opt/inttools/gamecontrollerdb.txt 不存在 —— 手柄可能认不出来"
    warn "  该文件由 ArkOS 固件提供；缺失说明这不是标准 ArkOS"
fi

# ---- cifs 内核支持 ----------------------------------------------------------
# mount.cifs 只是用户态 helper；真正干活的是内核 cifs。
# 实机（4.4.189）是**内建**的（/proc/filesystems 直接有 cifs），
# 但别的固件可能是模块。三种结果分开报，别把「内核没支持」和
# 「用户态没装」混为一谈 —— 两者的修法完全不同。
if grep -qw cifs /proc/filesystems 2>/dev/null; then
    ok "内核 cifs 支持（/proc/filesystems 已列出）"
elif modprobe cifs >/dev/null 2>&1 && grep -qw cifs /proc/filesystems 2>/dev/null; then
    ok "内核 cifs 模块已加载（modprobe 成功）"
elif modprobe cifs >/dev/null 2>&1; then
    ok "内核 cifs 模块已加载"
else
    bad "内核不支持 cifs —— 没有 cifs.ko，也没内建"
    bad "  这台机器的内核无法挂 SMB 共享，装商店也没用（换固件/内核）"
    FAIL=1
fi

if [ -d "$ES_DIR" ]; then ok "ES options 目录 $ES_DIR"
else bad "$ES_DIR 不存在 —— 确认是不是 ArkOS（可用 --es-dir 指定）"; FAIL=1; fi

ESCFG=/etc/emulationstation/es_systems.cfg
if [ -f "$ESCFG" ]; then
    # 精确校验：入口能不能在 ES 里出现，只取决于 **options 系统** 的 <path>。
    # 光 grep "<path>$ES_DIR/" 不够 —— ArkOS4Clone 的 es_systems.cfg 里有
    # .dual / .rk3326 / .bak 多份副本，别的系统的 path 也可能长得一样。
    # 这里抽出 name=options 那个 <system> 块里的 <path>，再和 --es-dir 比。
    ES_OPT_PATH=$(awk '
        /<system>/ { inblk=1; name=""; path="" }
        inblk && /<name>/ {
            n=$0; sub(/.*<name>/, "", n); sub(/<\/name>.*/, "", n); name=n
        }
        inblk && /<path>/ {
            p=$0; sub(/.*<path>/, "", p); sub(/<\/path>.*/, "", p); path=p
        }
        inblk && /<\/system>/ {
            if (name == "options") { print path; exit }
            inblk=0
        }
    ' "$ESCFG" 2>/dev/null)
    ES_OPT_PATH="${ES_OPT_PATH%/}"      # 去掉结尾斜杠便于比较
    if [ -n "$ES_OPT_PATH" ] && [ "$ES_OPT_PATH" = "$ES_DIR" ]; then
        ok "es_systems.cfg 的 options 系统 <path> = $ES_DIR/ ✓"
    elif [ -n "$ES_OPT_PATH" ]; then
        bad "es_systems.cfg 的 options 系统指向 $ES_OPT_PATH/，与 --es-dir=$ES_DIR 不一致"
        bad "  入口脚本会放进 $ES_DIR，但 ES 只会去 $ES_OPT_PATH/ 找 —— 菜单里看不到"
        bad "  改用：sudo ./install.sh --es-dir $ES_OPT_PATH ..."
        FAIL=1
    else
        warn "es_systems.cfg 里没解析到 name=options 的系统"
        warn "  确认这是不是 ArkOS4Clone（可用 --es-dir 指定入口目录）"
    fi
else
    warn "找不到 $ESCFG —— 无法确认入口目录，请核对 --es-dir"
fi

# ---- 自动补装缺失依赖 -------------------------------------------------------
# 实机实测：掌机源是 eoan（已 EOL，走 old-releases），cifs-utils 只新增 1 个包、
# 依赖齐全、几秒装完。比起让人 SSH 上去手敲一条 apt-get，安装器直接做掉更省事
# —— 尤其这是**唯一一个会导致「装不上」的常见缺项**。
if [ -n "${MISSING_PKGS// /}" ]; then
    head_ "③b 自动补装缺失依赖"
    say "  缺：$MISSING_PKGS"
    # shellcheck disable=SC2086
    if apt-get install -y $MISSING_PKGS; then
        ok "补装完成"
        # 补装后复检，确认真的可用了
        STILL=""
        for c in mount.cifs flock mountpoint nc; do
            command -v "$c" >/dev/null 2>&1 || STILL="$STILL $c"
        done
        if [ -n "${STILL// /}" ]; then
            bad "补装后仍缺失：$STILL"
            FAIL=1
        else
            ok "依赖复检通过"
        fi
    else
        bad "自动补装失败 —— 请手动执行：apt-get install $MISSING_PKGS"
        FAIL=1
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

# ----------------------------------------------------------------- 4. 备份
head_ "④ 备份会被覆盖的文件"
STAMP=$(date '+%Y%m%d-%H%M%S')
BAK_DIR="$STORE_DIR/backup/$STAMP"
mkdir -p "$BAK_DIR"
chown "$TARGET_UID:$TARGET_GID" "$BAK_DIR" 2>/dev/null
BAK_COUNT=0

bak() {  # bak <文件> <备份到哪>
    local f="$1" b="$2"
    [ -e "$f" ] || return 0
    if cp -p "$f" "$b" 2>/dev/null; then
        ok "备份 $f"
        BAK_COUNT=$((BAK_COUNT + 1))
    else
        warn "备份失败 $f"
    fi
}

if [ -e "$MOUNT_SCRIPT" ] && [ ! -e "$MOUNT_SCRIPT.orig-bak" ]; then
    cp -p "$MOUNT_SCRIPT" "$MOUNT_SCRIPT.orig-bak" && ok "备份 $MOUNT_SCRIPT -> .orig-bak"
elif [ -e "$MOUNT_SCRIPT.orig-bak" ]; then
    ok "$MOUNT_SCRIPT.orig-bak 已存在，保留最早的原件"
fi
bak "$UNIT"                  "$BAK_DIR/nas-roms.service"
bak "$DISPATCHER"            "$BAK_DIR/50-nas-roms"
bak "$ES_DIR/RomStore.sh"    "$BAK_DIR/RomStore.sh"
if [ -f "$STORE_DIR/source.json" ]; then
    cp -p "$STORE_DIR/source.json" "$BAK_DIR/source.json"
    ok "备份 source.json（本次未指定 --host 时不会改动它）"
    BAK_COUNT=$((BAK_COUNT + 1))
fi
# 全新安装时上面一个都不会命中 —— 说清楚，免得空白让人以为出了问题
if [ "$BAK_COUNT" -eq 0 ]; then
    say "  · 全新安装，没有需要备份的旧文件（升级时这里会列出被覆盖的文件）"
    rmdir "$BAK_DIR" 2>/dev/null || true    # 留个空目录没意义
fi

# ----------------------------------------------------------------- 5. 装程序
head_ "⑤ 安装程序文件"
mkdir -p "$STORE_DIR/romstore" "$STORE_DIR/log"

NPY=$(ls -1 "$SRC/romstore/"*.py | wc -l)
install -m 644 "$SRC/romstore/"*.py "$STORE_DIR/romstore/"
ok "romstore/*.py  （$NPY 个模块）"

install -m 755 "$SRC/run.py" "$STORE_DIR/run.py"
ok "run.py"

# 记下装的是哪个版本 —— 排障时在掌机上 cat 一下就知道，不必翻日志
if [ -f "$SRC/../VERSION" ]; then
    install -m 644 "$SRC/../VERSION" "$STORE_DIR/VERSION"
    ok "VERSION（$(cat "$SRC/../VERSION")）"
fi

find "$STORE_DIR" -type d -name __pycache__ -prune -exec rm -rf -- {} + 2>/dev/null
chown -R "$TARGET_UID:$TARGET_GID" "$STORE_DIR"
ok "目录归属已设为 $TARGET_USER"

# ----------------------------------------------------------------- 6. 模板替换
head_ "⑥ 生成入口脚本与挂载脚本"
render() {  # render <模板> <目标>
    sed -e "s|@@STORE_DIR@@|$STORE_DIR|g" \
        -e "s|@@ES_DIR@@|$ES_DIR|g" \
        -e "s|@@UID@@|$TARGET_UID|g" \
        -e "s|@@GID@@|$TARGET_GID|g" "$1" > "$2"
    if grep -q '@@[A-Z_]\{1,\}@@' "$2"; then
        bad "占位符未替换完：$2"
        grep -n '@@[A-Z_]\{1,\}@@' "$2" | head
        rm -f "$2"
        die "模板替换失败"
    fi
}

render "$SRC/RomStore.sh" "$ES_DIR/RomStore.sh"
chmod 755 "$ES_DIR/RomStore.sh"
chown "$TARGET_UID:$TARGET_GID" "$ES_DIR/RomStore.sh"
ok "$ES_DIR/RomStore.sh"

# 0.4.2 早期版本装过一个独立的「视频播放器」入口，后来改成商店详情页里播放。
# 升级时把旧入口清掉，免得 Options 里留一个已经没用的条目。
if [ -e "$ES_DIR/VideoPlayer.sh" ]; then
    rm -f "$ES_DIR/VideoPlayer.sh" && ok "已移除旧版独立入口 $ES_DIR/VideoPlayer.sh"
fi

render "$SRC/nas-roms-mount.sh" "$MOUNT_SCRIPT.new"
chmod 755 "$MOUNT_SCRIPT.new"
chown 0:0 "$MOUNT_SCRIPT.new"
mv -f "$MOUNT_SCRIPT.new" "$MOUNT_SCRIPT"
ok "$MOUNT_SCRIPT"

bash -n "$MOUNT_SCRIPT"        || die "挂载脚本语法错误"
ok "挂载脚本语法检查通过"
bash -n "$ES_DIR/RomStore.sh"  || die "入口脚本语法错误"
ok "入口脚本语法检查通过"

# ----------------------------------------------------------------- 7. 商店源
head_ "⑦ 商店源配置"
as_user() {
    sudo -u "$TARGET_USER" -H \
        env HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" \
            PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        python3 -m romstore.cli "$@"
}

if [ -n "$RS_HOST" ]; then
    # 通过环境变量把值送进 Python，避免密码出现在 ps / 命令历史里
    RS_HOST="$RS_HOST" RS_SHARE="$RS_SHARE" RS_SMBUSER="$RS_SMBUSER" RS_SMBPASS="$RS_SMBPASS" \
    sudo -u "$TARGET_USER" -H \
        env HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" \
            RS_HOST="$RS_HOST" RS_SHARE="$RS_SHARE" \
            RS_SMBUSER="$RS_SMBUSER" RS_SMBPASS="$RS_SMBPASS" \
        python3 - <<'PYEOF'
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

# ----------------------------------------------------------------- 8. systemd
head_ "⑧ 挂载服务（systemd）"
install -m 644 "$SYSD/nas-roms.service" "$UNIT"
ok "$UNIT"

# /roms 只读自愈：exFAT 无 journal，掉电会截断 FAT 表导致整盘只读（Errno 30）
if [ -r "$SRCD/systemd/roms-fs-selfheal.sh" ] && [ -r "$SYSD/roms-fs-selfheal.service" ]; then
    bak "$HEAL_SH" "$BAK_DIR/roms-fs-selfheal.sh"
    bak "$HEAL_UNIT" "$BAK_DIR/roms-fs-selfheal.service"
    install -m 755 "$SRCD/systemd/roms-fs-selfheal.sh" "$HEAL_SH"
    install -m 644 "$SYSD/roms-fs-selfheal.service" "$HEAL_UNIT"
    ok "只读自愈：$HEAL_SH"
else
    warn "分发包缺少自愈脚本，跳过（不影响主功能）"
fi

# 运行期只读监控：开机自愈只管开机那一次，而实测第三次事故是运行 42 分钟后
# 才被 remount-ro 的，开机自愈根本没机会介入。这个服务补上运行期的缺口。
if [ -r "$SRCD/systemd/roms-fs-watch.sh" ] && [ -r "$SYSD/roms-fs-watch.service" ]; then
    bak "$WATCH_SH" "$BAK_DIR/roms-fs-watch.sh"
    bak "$WATCH_UNIT" "$BAK_DIR/roms-fs-watch.service"
    install -m 755 "$SRCD/systemd/roms-fs-watch.sh" "$WATCH_SH"
    install -m 644 "$SYSD/roms-fs-watch.service" "$WATCH_UNIT"
    ok "只读监控：$WATCH_SH"
else
    warn "分发包缺少只读监控脚本，跳过（不影响主功能）"
fi

if [ -r "$SYSD/50-nas-roms" ]; then
    if [ -d /etc/NetworkManager ]; then
        install -m 755 -o root -g root "$SYSD/50-nas-roms" "$DISPATCHER"
        ok "$DISPATCHER（网络重连时自动补挂）"
    else
        warn "系统没有 NetworkManager，跳过 dispatcher"
    fi
fi

systemctl daemon-reload
if systemctl enable nas-roms.service >/dev/null 2>&1; then
    ok "已设为开机自启"
else
    warn "enable 失败，可稍后手动：systemctl enable nas-roms"
fi

# 自愈服务是可选增强，装不上也不该让安装失败
if [ -f "$HEAL_UNIT" ]; then
    if systemctl enable roms-fs-selfheal.service >/dev/null 2>&1; then
        ok "roms-fs-selfheal.service 已设为开机自启（掉电只读自动修复）"
    else
        warn "自愈服务 enable 失败，可稍后手动：systemctl enable roms-fs-selfheal"
    fi
fi
if [ -f "$WATCH_UNIT" ]; then
    if systemctl enable roms-fs-watch.service >/dev/null 2>&1; then
        ok "roms-fs-watch.service 已设为开机自启（运行中只读自动发现）"
    else
        warn "监控服务 enable 失败，可稍后手动：systemctl enable roms-fs-watch"
    fi
fi

# ----------------------------------------------------------------- 9. ES 崩溃保护（可选）
if [ "$WITH_ES_GUARD" = "1" ]; then
    head_ "⑨ ES 崩溃循环上限保护（可选）"
    if [ -r "$EXTRAS/10-crash-limit.conf" ]; then
        install -D -m 644 "$EXTRAS/10-crash-limit.conf" \
            /etc/systemd/system/emulationstation.service.d/10-crash-limit.conf
        systemctl daemon-reload
        ok "已装 10-crash-limit.conf（ES 连崩 5 次即停下，不再无限重启）"
    else
        warn "找不到 extras/10-crash-limit.conf，跳过"
    fi
fi

# ----------------------------------------------------------------- 10. 挂载
if [ "$DO_MOUNT" = "1" ]; then
    head_ "⑩ 挂载 NAS"
    # 走 systemd 而不是直接调脚本 —— 实机踩到过：
    # 直接跑脚本会把挂载建起来，但 nas-roms.service 仍显示 inactive(dead)，
    # 于是「服务状态」和「实际挂载」对不上，验收时误判成故障。
    # 用 systemctl start 启动，两者一致，且服务是 RemainAfterExit=yes，
    # 起来后会保持 active(exited)。
    if systemctl start nas-roms.service 2>/dev/null; then
        # 单元可能因为「已挂载且源一致」秒退；幂等，无副作用
        :
    fi
    "$MOUNT_SCRIPT" --force 2>&1 | sed 's/^/  /' || true
    # 挂载可能由上面任一路径完成 —— 服务状态在这里对齐一次
    systemctl start nas-roms.service >/dev/null 2>&1 || true
    if mountpoint -q "$MOUNT_POINT" 2>/dev/null && ls "$MOUNT_POINT" >/dev/null 2>&1; then
        ok "$MOUNT_POINT 已挂载（$(ls -A "$MOUNT_POINT" 2>/dev/null | wc -l) 个条目）"
        if systemctl is-active nas-roms.service >/dev/null 2>&1; then
            ok "nas-roms.service = active（与挂载状态一致）"
        else
            warn "nas-roms.service 显示 $(systemctl is-active nas-roms.service 2>/dev/null)"
            warn "  挂载已生效，但服务未跟踪 —— 下次开机仍会自启，不影响使用"
        fi
    else
        warn "$MOUNT_POINT 未能挂载"
        warn "  确认 NAS 已开机、共享名正确，然后在商店里 Start →「修改商店源」改对"
    fi
else
    head_ "⑩ 挂载 NAS"
    say "  按 --no-mount 跳过"
fi

# ----------------------------------------------------------------- 11. 索引
if [ "$DO_INDEX" = "1" ] && mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    head_ "⑪ 建立索引（首次约 10–15 秒）"
    as_user --build 2>&1 | sed 's/^/  /'
    if as_user --stats >/dev/null 2>&1; then
        ok "索引就绪"
    else
        warn "索引可能不完整，商店首次启动时会自动重建"
    fi
fi

# ----------------------------------------------------------------- 12. ES 入口
head_ "⑫ 注册 EmulationStation 入口"
if as_user --register >/dev/null 2>&1; then
    ok "Options → 「ROM 商店」已注册"
else
    warn "注册失败，可手动跑：sudo -u $TARGET_USER python3 -m romstore.cli --register"
fi

# ----------------------------------------------------------------- 13. 自动验收
# 实机教训：装完「看起来全绿」不等于真能用。这一节把装完必须为真的几件事
# 逐条复核一遍，避免用户拿到一个「装上了但打不开」的掌机。
head_ "⑬ 安装后自检"
VERIFY_FAIL=0
v_ok()   { printf '  %s✔%s %s\n' "$C_G" "$C_0" "$*"; }
v_bad()  { printf '  %s✘%s %s\n' "$C_R" "$C_0" "$*"; VERIFY_FAIL=1; }
v_warn() { printf '  %s!%s %s\n' "$C_Y" "$C_0" "$*"; }

# 1) 程序本体
if [ -r "$STORE_DIR/run.py" ] && [ -r "$STORE_DIR/romstore/ui.py" ]; then
    v_ok "程序本体就位（run.py + romstore/）"
else
    v_bad "程序本体不完整（$STORE_DIR）"
fi

# 1b) 封面模块（P2 起是必需模块，缺了安装游戏会 ImportError）
if [ -r "$STORE_DIR/romstore/media.py" ]; then
    v_ok "封面模块就位（romstore/media.py）"
else
    v_bad "缺少 romstore/media.py（封面导入不可用）"
fi

# 1c) 版本号（排障用：cat ~/.romstore/VERSION 就知道装的是哪版）
if [ -r "$STORE_DIR/VERSION" ]; then
    v_ok "版本号已记录（$(cat "$STORE_DIR/VERSION")）"
else
    v_bad "缺少 ~/.romstore/VERSION（不影响功能）"
fi

# 2) 入口脚本 + ES 注册
if [ -x "$ES_DIR/RomStore.sh" ]; then
    v_ok "ES 入口脚本可执行（$ES_DIR/RomStore.sh）"
else
    v_bad "ES 入口脚本缺失或不可执行（$ES_DIR/RomStore.sh）"
fi
if [ -f "$ES_DIR/gamelist.xml" ] && grep -q 'RomStore\.sh' "$ES_DIR/gamelist.xml"; then
    v_ok "gamelist.xml 里有「ROM 商店」条目"
else
    v_bad "gamelist.xml 里没找到入口条目"
fi

# 2b) 预览视频的依赖：详情页按 X 播放靠 mpv 直出 DRM
if command -v mpv >/dev/null 2>&1; then
    v_ok "mpv 可用（详情页预览视频）"
else
    v_warn "没找到 mpv —— 商店详情页按 X 无法播放预览视频"
fi
if [ -f "$ES_DIR/gamelist.xml" ] && grep -q 'VideoPlayer\.sh' "$ES_DIR/gamelist.xml"; then
    v_warn "gamelist.xml 里还留着旧版「视频播放器」条目（--unregister 可清理）"
fi

# 3) 商店源
if [ -f "$STORE_DIR/source.json" ]; then
    v_ok "source.json 存在（$(as_user --source-show 2>/dev/null | sed -n 's/^共享 *: *//p' | head -1)）"
else
    v_bad "source.json 不存在 —— 商店会回落到内置默认源"
fi

# 4) 挂载
if mountpoint -q "$MOUNT_POINT" 2>/dev/null && ls "$MOUNT_POINT" >/dev/null 2>&1; then
    v_ok "NAS 已挂载（$(ls -A "$MOUNT_POINT" 2>/dev/null | wc -l) 个条目）"
else
    v_bad "NAS 未挂载 —— 商店会显示空库（进商店后 Start →「修改商店源」可改）"
fi

# 5) 索引
if [ -f "$STORE_DIR/index.db" ] && as_user --stats >/dev/null 2>&1; then
    v_ok "索引可读（$(as_user --stats 2>/dev/null | sed -n 's/^游戏条目（去重后）: *//p' | head -1) 款）"
else
    v_bad "索引缺失或不可读 —— 商店启动时会自动重建"
fi

# 6) systemd 开机自启
if systemctl is-enabled nas-roms.service >/dev/null 2>&1; then
    v_ok "nas-roms.service 开机自启已启用"
else
    v_bad "nas-roms.service 未启用开机自启"
fi

# 6b) /roms 只读自愈（可选增强，缺了只是打回原形，不算故障）
if [ -f "$HEAL_SH" ] && command -v fsck.exfat >/dev/null 2>&1; then
    if systemctl is-enabled roms-fs-selfheal.service >/dev/null 2>&1; then
        v_ok "roms-fs-selfheal.service 已启用（掉电只读自动修复）"
    else
        v_warn "roms-fs-selfheal.service 未启用，可手动：systemctl enable roms-fs-selfheal"
    fi
else
    v_warn "未安装 /roms 只读自愈（或系统无 fsck.exfat）—— 掉电后需手工 fsck"

# 6c) 运行期只读监控
if [ -f "$WATCH_UNIT" ]; then
    if systemctl is-enabled roms-fs-watch.service >/dev/null 2>&1; then
        v_ok "roms-fs-watch.service 已启用（运行中只读自动发现并修复）"
    else
        v_warn "roms-fs-watch.service 未启用，可手动：systemctl enable roms-fs-watch"
    fi
else
    v_warn "未安装 /roms 只读监控 —— 运行中损坏需手工 SSH 排查"
fi
fi

# 7) 图形环境（无头探针，不占屏）
PROBE=$( cd "$STORE_DIR" && sudo -u "$TARGET_USER" -H \
    env HOME="$TARGET_HOME" PYTHONPATH="$STORE_DIR" \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    python3 run.py --probe 2>&1 ) || true
if printf '%s' "$PROBE" | grep -q 'SDL_Init(VIDEO): OK'; then
    v_ok "SDL2 视频初始化 OK（$(printf '%s' "$PROBE" | sed -n 's/.*当前驱动 = //p' | head -1)）"
else
    v_bad "SDL2 视频初始化失败 —— 图形界面可能起不来"
fi
CTRL=$(printf '%s' "$PROBE" | sed -n 's/^手柄数量[^:]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -1)
if [ "${CTRL:-0}" -gt 0 ] 2>/dev/null; then
    v_ok "手柄识别到 $CTRL 个"
else
    v_bad "没识别到手柄 —— 检查 /opt/inttools/gamecontrollerdb.txt"
fi
if printf '%s' "$PROBE" | grep -q '中文字体.*存在'; then
    v_ok "中文字体可用"
else
    v_bad "中文字体缺失 —— 界面中文会显示为方块"
fi

say ""
if [ "$VERIFY_FAIL" = "0" ]; then
    say "  ${C_G}${C_BOLD}自检全部通过 —— 可以直接在掌机上用了。${C_0}"
else
    say "  ${C_Y}${C_BOLD}自检有未通过项（上面标 ✘）—— 不影响已装好的部分，按提示处理即可。${C_0}"
fi

# ----------------------------------------------------------------- 总结
head_ "完成"
say "  版本      $VERSION"
say "  程序目录  $STORE_DIR"
say "  入口      $ES_DIR/RomStore.sh"
say "  挂载脚本  $MOUNT_SCRIPT"
say "  备份目录  $BAK_DIR"
say ""
say "  ${C_BOLD}掌机怎么用：EmulationStation → Options → 「ROM 商店」${C_0}"
say "    A 进入/详情   X 安装   Y 卸载   L1/R1 翻页   Start 菜单   B 返回"
say "    Start →「修改商店源」可在掌机上直接换 NAS，不用电脑"
say ""
say "  ${C_Y}ES 里若看不到「ROM 商店」，重启一次 EmulationStation 即可。${C_0}"
say ""
