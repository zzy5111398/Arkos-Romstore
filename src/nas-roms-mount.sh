#!/bin/bash
# 挂载 NAS ROM 共享到 @@MOUNT_POINT@@  —— 由 install.sh 按平台渲染
#
# 商店源（主机 / 共享名 / 账号 / 密码）由 @@STORE_DIR@@/source.json 决定 —— 只此一份。
# 「ROM 商店」→ Start 菜单 →「修改商店源」写这个文件，然后调用本脚本重挂；
# 开机时 systemd 也调用本脚本。两者共用同一份配置，不会各说各话。
# 该文件不存在 / 损坏 / 无 python3 时，回落到下面的内置默认值 ——
# 保证开机挂载永远不会因为配置缺失而失败。
#
# 本文件里的 @@...@@ 占位符由 install.sh 安装时替换成实机值，
# 安装后不应残留任何占位符（install.sh 会检查）。
#
# 用法：
#   nas-roms-mount.sh            已挂载且源未变 → 立刻退出 0
#   nas-roms-mount.sh --force    无论是否已挂载，都先卸载再重挂（商店换源用）
#   nas-roms-mount.sh --stop     卸载
#   nas-roms-mount.sh --status   打印当前挂载状态
#
# ## 两种后端（由 @@BACKEND@@ 决定，安装时按平台选定）
#
#   cifs    内核 cifs + mount.cifs。ArkOS 走这条。
#           实测：内核只认 vers=2.1，vers=2.0 返回 EOPNOTSUPP (errno 95)
#
#   rclone  rclone 的 FUSE 挂载。AURKNIX/ROCKNIX 走这条 ——
#           那台机器的内核**根本没有 cifs**（/proc/filesystems 里没有，
#           /lib/modules 里也没有 cifs.ko），mount.cifs 也不存在，
#           `mount -t cifs` 直接 "No such device"。
#           它自带的 rclone 1.71 支持 smb 后端，走 FUSE 就等价于挂载。
#
# 并发安全：systemd 单元、网络管理器 dispatcher、商店换源三处都会调用本脚本。
# 无锁时第二个会 mount error(16) Device or resource busy。统一用 /run/nas-roms.lock
# 串行化（exec 9 + flock）。

set -u

MP="@@MOUNT_POINT@@"
BACKEND="@@BACKEND@@"
SRC_CONF="@@STORE_DIR@@/source.json"
STATE="@@STORE_DIR@@/.mount-state"
RCLONE="@@RCLONE_BIN@@"
RCLONE_CONF="@@STORE_DIR@@/rclone.conf"
RCLONE_CACHE="@@STORE_DIR@@/cache/rclone"
RCLONE_LOG="@@STORE_DIR@@/log/rclone.log"
LOCK="/run/nas-roms.lock"
PY="/usr/bin/python3"

# rclone 的 FUSE 挂载要用 fusermount；本机只有 fusermount(v2)、没有 fusermount3，
# 安装器在下面这个目录里放了一个同名桥接。加进 PATH 它才找得到。
EXTRA_PATH="@@EXTRA_PATH@@"
[ -n "$EXTRA_PATH" ] && PATH="$EXTRA_PATH:$PATH"
# ⚠️ 必须把 sbin 补进来：mount.cifs / fsck.exfat / mountpoint 这些都住在
# /usr/sbin，而**普通用户的 PATH 通常不含 sbin**（126 实测 ark 的 PATH 只有
# /usr/local/bin:/usr/bin:/bin:/usr/games）。install.sh 走 sudo 时能找到，
# 但用户在终端里手跑本脚本、或 systemd 以非 root 身份拉起时就会「明明装了
# cifs-utils 却说找不到 mount.cifs」。重复添加无害。
case ":$PATH:" in
    *:/sbin:*) ;;
    *) PATH="$PATH:/sbin:/usr/sbin" ;;
esac
export PATH

# 内置默认值（source.json 不可用时的兜底）
D_HOST="10.168.1.100"
D_SHARE="Roms"
D_USER=""
D_PASS=""
D_VERS="2.1"

MODE="mount"
case "${1:-}" in
    --force|-f) MODE="force" ;;
    --stop|-u)  MODE="stop" ;;
    --status|-s) MODE="status" ;;
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
    # cifs 的 -o / rclone 的连接串都是逗号分隔，账号/密码里不能有逗号
    return "".join(c for c in str(v) if c not in ",\r\n")
sys.stdout.write("HOST=%s\nSHARE=%s\nUSER=%s\nPASS=%s\nVERS=%s\n" % (
    g("host").strip(), g("share").strip("/"), g("user"), g("password"), g("vers")))
' "$SRC_CONF" 2>/dev/null)
}

read_conf

SRC="//$D_HOST/$D_SHARE"

mounted_ok() {
    mountpoint -q "$MP" 2>/dev/null && ls "$MP" >/dev/null 2>&1
}

# /proc/mounts 里当前挂的是不是同一个共享（只对 cifs 有意义）
same_source() {
    awk -v mp="$MP" -v src="$SRC" \
        '$2 == mp && $1 == src { found = 1 } END { exit !found }' /proc/mounts 2>/dev/null
}

# 状态文件（客户端用它判断「挂的是不是我配置的那个源」）——
# rclone 挂载在 /proc/mounts 里的源名是 :smb{hash}:Roms，认不出主机。
write_state() {
    mkdir -p "$(dirname "$STATE")" 2>/dev/null
    {
        echo "BACKEND=$BACKEND"
        echo "HOST=$D_HOST"
        echo "SHARE=$D_SHARE"
        echo "USER=$D_USER"
        echo "MOUNT=$MP"
        echo "AT=$(date '+%F %T')"
    } > "$STATE" 2>/dev/null
    chmod 600 "$STATE" 2>/dev/null
}

rm_state() {
    rm -f "$STATE" 2>/dev/null
}

do_umount() {
    # rclone 的 FUSE 挂载用 fusermount 卸更干净；两个都试，最后兜底 lazy
    umount "$MP" 2>/dev/null
    mountpoint -q "$MP" 2>/dev/null && { fusermount -u -z "$MP" 2>/dev/null; sleep 1; }
    mountpoint -q "$MP" 2>/dev/null && { umount "$MP" 2>/dev/null; sleep 1; }
    mountpoint -q "$MP" 2>/dev/null && { umount -l "$MP" 2>/dev/null; sleep 1; }
    # 挂载没了但 rclone 进程还在，会让下次挂载抢不到 FUSE —— 顺手清掉
    pkill -f "rclone mount .* $MP" 2>/dev/null
    ! mountpoint -q "$MP" 2>/dev/null
}

if [ "$MODE" = "stop" ]; then
    if do_umount; then
        rm_state
        echo "已卸载 $MP"
        exit 0
    fi
    echo "卸载失败：$MP 仍处于挂载状态"
    exit 16
fi

if [ "$MODE" = "status" ]; then
    if mountpoint -q "$MP" 2>/dev/null; then
        echo "已挂载 $MP"
        awk -v mp="$MP" '$2 == mp { print "  " $1 " type " $3 " opts " $4 }' /proc/mounts
        [ -r "$STATE" ] && sed 's/^/  /' "$STATE"
        exit 0
    fi
    echo "未挂载 $MP"
    exit 1
fi

# 串行化：最多等 120 秒拿锁，拿不到就直接退出（另一方会处理）
exec 9>"$LOCK" 2>/dev/null || exit 0

# ⚠️ flock 的口径在两家实现之间不一致：
#     util-linux 版有 `-w SECS`（等多久）；**BusyBox 版没有 -w**，
#     传了会立刻以 "invalid option -- 'w'" 失败（exit 1，秒退）。
#     ROCKNIX 上 /usr/bin/flock 就是 BusyBox —— 0.6.0 首次真机安装时
#     踩到：挂载脚本每次都"拿不到挂载锁"，实际根本没人持锁。
# 所以先试 -w（ArkOS 走这条），失败再退回「-n + 自己重试」，两边都能用。
LOCK_WAIT=120
if flock -w "$LOCK_WAIT" 9 2>/dev/null; then
    :                       # util-linux 版：等到了
elif flock -n 9 2>/dev/null; then
    :                       # BusyBox 版：上面那次是"不认 -w"的语法错，锁其实空着
else
    # 别写"失败"—— 有另一个实例在挂同一个点，那是正常并发，让位就好。
    # （BusyBox 的 flock 不支持 -w，所以这里不会等，直接就放弃）
    echo "已有另一个挂载进程在工作（$LOCK 被占用），本次让位"
    exit 0
fi

if mountpoint -q "$MP" 2>/dev/null; then
    if [ "$MODE" != "force" ] && mounted_ok; then
        # cifs 还能顺手核对源名；rclone 认不出源名，只认状态文件
        if [ "$BACKEND" = "cifs" ]; then
            same_source && { write_state; exit 0; }
        elif [ -r "$STATE" ] && grep -qx "HOST=$D_HOST" "$STATE" \
             && grep -qx "SHARE=$D_SHARE" "$STATE"; then
            exit 0
        fi
    fi

    if [ "$MODE" = "force" ]; then
        echo "重挂：先卸载 $MP"
    else
        echo "已挂载但内容不可读或源不一致，先卸载"
    fi

    if ! do_umount; then
        echo "无法卸载 $MP（可能仍有进程占用），已放弃"
        exit 16
    fi
fi

mkdir -p "$MP"

# =============================================================== cifs 后端
if [ "$BACKEND" = "cifs" ]; then
    OPTS="vers=$D_VERS,iocharset=utf8,uid=@@UID@@,gid=@@GID@@,file_mode=0664,dir_mode=0775,nounix,cache=loose,actimeo=60,_netdev"
    if [ -n "$D_USER" ]; then
        OPTS="username=$D_USER,password=$D_PASS,$OPTS"
    else
        OPTS="guest,$OPTS"
    fi

    # mount.cifs 是用户态 helper —— 内核 cifs 干活前要先找到它。
    # 实机踩到过：cifs-utils 没装时，`mount -t cifs` 报的是
    # 「bad option; ... you might need a /sbin/mount.<type> helper program」，
    # 看着像参数写错了，其实是缺包。这里提前给一句能对症的话。
    if ! command -v mount.cifs >/dev/null 2>&1; then
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
        sleep 1
        if mounted_ok; then write_state; exit 0; fi     # 并发竞争：另一方已挂上
        echo "挂载失败：$SRC -> $MP (rc=$rc)"
        [ -n "$MOUNT_OUT" ] && echo "$MOUNT_OUT"
        exit "$rc"
    fi

    write_state
    echo "已挂载 $SRC -> $MP"
    exit 0
fi

# ============================================================= rclone 后端
if [ ! -x "$RCLONE" ]; then
    echo "挂载失败：找不到 rclone（$RCLONE）"
    echo "本机内核没有 cifs，只能用 rclone 挂 SMB —— 请确认 rclone 已安装"
    exit 1
fi

mkdir -p "$RCLONE_CACHE" "$(dirname "$RCLONE_LOG")" 2>/dev/null

# 凭据写进独立配置（0600，含密码）。
# 不用 rclone 的临时连接串，是因为连接串里的 pass 必须是 obscured 的密文，
# 而这里要能一眼看清配的是什么源、出问题能直接改。
obscure() {
    "$RCLONE" obscure "$1" 2>/dev/null
}

write_rclone_conf() {
    local tmp="$RCLONE_CONF.tmp.$$"
    {
        echo "[nas]"
        echo "type = smb"
        echo "host = $D_HOST"
        if [ -n "$D_USER" ]; then
            echo "user = $D_USER"
        else
            echo "user = guest"
        fi
        if [ -n "$D_PASS" ]; then
            local op
            op=$(obscure "$D_PASS")
            [ -n "$op" ] && echo "pass = $op"
        fi
    } > "$tmp" 2>/dev/null
    chmod 600 "$tmp" 2>/dev/null
    mv -f "$tmp" "$RCLONE_CONF" 2>/dev/null
}

write_rclone_conf
if [ ! -r "$RCLONE_CONF" ]; then
    echo "挂载失败：写不出 rclone 配置 $RCLONE_CONF"
    exit 1
fi

# 等 445 端口可用（最多 60 秒）
for _ in $(seq 1 30); do
    nc -z -w 2 "$D_HOST" 445 >/dev/null 2>&1 && break
    sleep 2
done

# 先探一次「能不能列共享」—— 失败时这里给出的错比挂载时报的清楚得多，
# 而且不会留下半个挂载点。
if ! PROBE_OUT=$("$RCLONE" lsf "nas:$D_SHARE" --config "$RCLONE_CONF" \
                 --max-depth 1 --timeout 20s 2>&1); then
    echo "挂载失败：$SRC -> $MP"
    echo "$PROBE_OUT" | head -3
    exit 32
fi

# FUSE 挂载本体。要点：
#   --cache-dir 必须显式给 —— 默认是 ~/.cache/rclone，而 root 的 HOME
#   在只读的 squashfs 上，写不进去会直接失败。
#   --vfs-cache-mode full：封面/元数据会被反复读，全缓存比直读快得多；
#   上限 512M 防它把 /storage 撑满。
#   setsid：脱离本脚本的进程组 —— 否则 systemd 收尾时会把 rclone 一起带走。
setsid "$RCLONE" mount "nas:$D_SHARE" "$MP" \
    --config "$RCLONE_CONF" \
    --read-only \
    --cache-dir "$RCLONE_CACHE" \
    --dir-cache-time 10m \
    --vfs-cache-mode full \
    --vfs-cache-max-size 512M \
    --vfs-cache-max-age 24h \
    --vfs-read-chunk-size 1M \
    --vfs-read-chunk-size-limit 32M \
    --no-modtime \
    --log-level ERROR \
    --log-file "$RCLONE_LOG" \
    </dev/null >>"$RCLONE_LOG" 2>&1 &

# 等挂载点出现（最多 40 秒）
for _ in $(seq 1 40); do
    if mountpoint -q "$MP" 2>/dev/null && ls "$MP" >/dev/null 2>&1; then
        write_state
        echo "已挂载 $SRC -> $MP（rclone FUSE）"
        exit 0
    fi
    sleep 1
done

echo "挂载失败：$SRC -> $MP（rclone 未在 40 秒内挂上）"
tail -5 "$RCLONE_LOG" 2>/dev/null
pkill -f "rclone mount .* $MP" 2>/dev/null
exit 32
