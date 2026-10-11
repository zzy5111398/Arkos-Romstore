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
RS_HOST=""
RS_SHARE=""
RS_SMBUSER=""
RS_SMBPASS=""
DO_MOUNT=1
DO_INDEX=1
WITH_ES_GUARD=0
CHECK_ONLY=0

STORE_DIR=""
TARGET_HOME=""
TARGET_UID=""
TARGET_GID=""

MOUNT_SCRIPT="/usr/local/sbin/nas-roms-mount.sh"
UNIT="/etc/systemd/system/nas-roms.service"
DISPATCHER="/etc/NetworkManager/dispatcher.d/50-nas-roms"
MOUNT_POINT="/mnt/Roms"

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
        --es-dir)      ES_DIR="${2:-}";      shift 2 ;;
        --no-mount)    DO_MOUNT=0;    shift ;;
        --skip-index)  DO_INDEX=0;    shift ;;
        --with-es-guard) WITH_ES_GUARD=1; shift ;;
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

# ----------------------------------------------------------------- 3. 依赖
head_ "③ 依赖检查"
FAIL=0

need_cmd() {  # need_cmd <命令> <它用来干什么>
    if command -v "$1" >/dev/null 2>&1; then
        ok "$1  $(command -v "$1")"
    else
        bad "$1 未安装 —— $2"
        FAIL=1
    fi
}
need_cmd python3    "本程序是 Python 写的"
need_cmd mount.cifs "挂载 SMB 共享（cifs-utils）"
need_cmd flock      "挂载并发串行化（util-linux）"
need_cmd mountpoint "判断挂载状态（util-linux）"
need_cmd nc         "探测 NAS 的 445 端口（netcat）"

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

if [ -d "$ES_DIR" ]; then ok "ES options 目录 $ES_DIR"
else bad "$ES_DIR 不存在 —— 确认是不是 ArkOS（可用 --es-dir 指定）"; FAIL=1; fi

ESCFG=/etc/emulationstation/es_systems.cfg
if [ -f "$ESCFG" ]; then
    if grep -q "<path>$ES_DIR/" "$ESCFG"; then
        ok "es_systems.cfg 里 options 系统指向 $ES_DIR/"
    else
        warn "es_systems.cfg 里没找到指向 $ES_DIR/ 的系统"
        warn "  入口可能不出现在 Options 菜单里，请核对 --es-dir"
    fi
else
    warn "找不到 $ESCFG"
fi

if [ "$FAIL" -ne 0 ]; then
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

bak() {  # bak <文件> <备份到哪>
    local f="$1" b="$2"
    [ -e "$f" ] || return 0
    cp -p "$f" "$b" && ok "备份 $f" || warn "备份失败 $f"
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
fi

# ----------------------------------------------------------------- 5. 装程序
head_ "⑤ 安装程序文件"
mkdir -p "$STORE_DIR/romstore" "$STORE_DIR/log"

NPY=$(ls -1 "$SRC/romstore/"*.py | wc -l)
install -m 644 "$SRC/romstore/"*.py "$STORE_DIR/romstore/"
ok "romstore/*.py  （$NPY 个模块）"

install -m 755 "$SRC/run.py" "$STORE_DIR/run.py"
ok "run.py"

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
    "$MOUNT_SCRIPT" --force 2>&1 | sed 's/^/  /' || true
    if mountpoint -q "$MOUNT_POINT" 2>/dev/null && ls "$MOUNT_POINT" >/dev/null 2>&1; then
        ok "$MOUNT_POINT 已挂载（$(ls -A "$MOUNT_POINT" 2>/dev/null | wc -l) 个条目）"
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
