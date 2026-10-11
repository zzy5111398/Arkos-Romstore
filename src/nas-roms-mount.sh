#!/bin/bash
# 挂载 NAS ROM 共享到 /mnt/Roms
#
# 商店源（主机 / 共享名 / 账号 / 密码）由 @@STORE_DIR@@/source.json 决定 —— 只此一份。
# 「ROM 商店」→ Start 菜单 →「修改商店源」写这个文件，然后调用本脚本重挂；
# 开机时 systemd 也调用本脚本。两者共用同一份配置，不会各说各话。
# 该文件不存在 / 损坏 / 无 python3 时，回落到下面的内置默认值 ——
# 保证开机挂载永远不会因为配置缺失而失败。
#
# 本文件里的 @@STORE_DIR@@ / @@UID@@ / @@GID@@ 由 install.sh 安装时替换成实机值，
# 安装后不应残留任何占位符（install.sh 会检查）。
#
# 用法：
#   nas-roms-mount.sh            已挂载且源未变 → 立刻退出 0
#   nas-roms-mount.sh --force    无论是否已挂载，都先卸载再重挂（商店换源用）
#
# 关键约束（实测，别改）：
#   * 本机内核 cifs 只认 vers=2.1；vers=2.0 返回 EOPNOTSUPP (errno 95)
#   * 挂载后文件所有者为 @@UID@@/@@GID@@（安装时填目标用户的 uid/gid）
#   * 本机没有 /storage 分区，持久化根分区是 / (mmcblk0p2)
#   * 挂载点固定 /mnt/Roms —— es_systems.cfg 里所有 <path> 都指向它
#
# 并发安全：systemd 单元、NetworkManager dispatcher、商店换源三处都会调用本脚本。
# 无锁时第二个会 mount error(16) Device or resource busy。统一用 /run/nas-roms.lock
# 串行化（exec 9 + flock）。

set -u

MP="/mnt/Roms"
SRC_CONF="@@STORE_DIR@@/source.json"
LOCK="/run/nas-roms.lock"
PY="/usr/bin/python3"

# 内置默认值（source.json 不可用时的兜底）
D_HOST="10.168.1.100"
D_SHARE="Roms"
D_USER=""
D_PASS=""
D_VERS="2.1"

FORCE=0
case "${1:-}" in
    --force|-f) FORCE=1 ;;
esac

read_conf() {
    [ -r "$SRC_CONF" ] || return 0
    [ -x "$PY" ] || return 0
    local line key val
    while IFS= read -r line; do
        key="${line%%=*}"
        val="${line#*=}"
        case "$key" in
            HOST)  [ -n "$val" ] && D_HOST="$val" ;;
            SHARE) [ -n "$val" ] && D_SHARE="$val" ;;
            USER)  D_USER="$val" ;;
            PASS)  D_PASS="$val" ;;
            VERS)  [ -n "$val" ] && D_VERS="$val" ;;
        esac
    done < <("$PY" -c '
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
def g(k):
    v = d.get(k) or ""
    # cifs 的 -o 是逗号分隔，账号/密码里不能有逗号
    return "".join(c for c in str(v) if c not in ",\r\n")
sys.stdout.write("HOST=%s\nSHARE=%s\nUSER=%s\nPASS=%s\nVERS=%s\n" % (
    g("host").strip(), g("share").strip("/"), g("user"), g("password"), g("vers")))
' "$SRC_CONF" 2>/dev/null)
}

read_conf

SRC="//$D_HOST/$D_SHARE"
OPTS="vers=$D_VERS,iocharset=utf8,uid=@@UID@@,gid=@@GID@@,file_mode=0664,dir_mode=0775,nounix,cache=loose,actimeo=60,_netdev"
if [ -n "$D_USER" ]; then
    OPTS="username=$D_USER,password=$D_PASS,$OPTS"
else
    OPTS="guest,$OPTS"
fi

mounted_ok() {
    mountpoint -q "$MP" 2>/dev/null && ls "$MP" >/dev/null 2>&1
}

# /proc/mounts 里当前挂的是不是同一个共享
same_source() {
    awk -v mp="$MP" -v src="$SRC" \
        '$2 == mp && $1 == src { found = 1 } END { exit !found }' /proc/mounts 2>/dev/null
}

# 串行化：最多等 120 秒拿锁，拿不到就直接退出（另一方会处理）
exec 9>"$LOCK" 2>/dev/null || exit 0
if ! flock -w 120 9; then
    echo "拿不到挂载锁 $LOCK，放弃"
    exit 0
fi

if mountpoint -q "$MP" 2>/dev/null; then
    if [ "$FORCE" = "0" ] && same_source && mounted_ok; then
        exit 0                      # 已挂载、源一致、内容可读 → 无事可做
    fi

    if [ "$FORCE" = "1" ]; then
        echo "重挂：先卸载 $MP"
    elif same_source; then
        echo "挂载点仍在但内容不可读（NAS 重启过？），修复重挂"
    else
        echo "已挂载的源与配置不一致，先卸载"
    fi

    umount "$MP" 2>/dev/null
    if mountpoint -q "$MP" 2>/dev/null; then
        sleep 1
        umount "$MP" 2>/dev/null
    fi
    if mountpoint -q "$MP" 2>/dev/null; then
        umount -l "$MP" 2>/dev/null
        sleep 1
    fi
    if mountpoint -q "$MP" 2>/dev/null; then
        echo "无法卸载 $MP（可能仍有进程占用），已放弃"
        exit 16
    fi
fi

mkdir -p "$MP"

# mount.cifs 是用户态 helper —— 内核 cifs 干活前要先找到它。
# 实机踩到过：cifs-utils 没装时，`mount -t cifs` 报的是
# 「bad option; ... you might need a /sbin/mount.<type> helper program」，
# 看着像参数写错了，其实是缺包。这里提前给一句能对症的话。
if ! command -v mount.cifs >/dev/null 2>&1; then
    # 内核内建 cifs 时，某些 mount 版本不带 helper 也能挂；别一口咬定失败，
    # 先照常尝试，但把这句诊断留在输出里。
    echo "提示：未找到 mount.cifs（cifs-utils 未装？）—— 尝试继续，失败请装 cifs-utils"
fi

# 等 445 端口可用（最多 60 秒）
for _ in $(seq 1 30); do
    nc -z -w 2 "$D_HOST" 445 >/dev/null 2>&1 && break
    sleep 2
done

# mount 自己的诊断（"mount error(2): No such file or directory" 这类）必须带出去 ——
# 掌机上没有控制台，这句就是用户唯一能看到的失败原因。
MOUNT_OUT=$(mount -t cifs "$SRC" "$MP" -o "$OPTS" 2>&1)
rc=$?

if [ "$rc" -ne 0 ]; then
    # 可能是并发竞争：再确认一次是否已被另一方挂上
    sleep 1
    mounted_ok && exit 0
    echo "挂载失败：$SRC -> $MP (rc=$rc)"
    [ -n "$MOUNT_OUT" ] && echo "$MOUNT_OUT"
    exit "$rc"
fi

echo "已挂载 $SRC -> $MP"
exit 0
