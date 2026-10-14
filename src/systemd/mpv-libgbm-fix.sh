#!/bin/bash
# mpv-libgbm-fix.sh —— 修「Mali 抢占 libgbm.so.1 导致 mpv 起不来」
#
# 背景（126 / DarkOS R36S 实测，2026-10-14）
#   Mali 固件把三个 SONAME 全抢走：
#       libgbm.so     -> libMali.so
#       libgbm.so.1   -> libMali.so
#       libgbm.so.1.0.0 -> libMali.so
#   而 Debian 的 libgbm1 包**装着**（25.0.7-2），实体文件也被它解到了
#   /usr/lib/aarch64-linux-gnu/ 下 —— 却被这些软链顶掉了。
#   结果：mpv 链接 libgbm.so.1 -> 解析到闭源 libMali，而 libMali 不导出
#   mpv 需要的符号：
#       mpv: symbol lookup error: mpv: undefined symbol: gbm_surface_create_with_modifiers
#
#   ⚠️ 这个错是**加载期**（lazy binding 被关掉）报的，所以换任何 --vo 都救不了，
#      也不是视频格式问题 —— 任何片子都放不出来，一秒都不行。
#
# 为什么不用「把软链改回 Mesa」
#   libMali 是 ES / GPU 加速的依赖，改错会黑屏甚至开不了机。
#   本脚本**一个系统文件都不动**，只把 Mesa 那份真库单独解到
#   $STORE_DIR/lib/mpv/ 下，播放时用 LD_LIBRARY_PATH 只给 mpv 用。
#   作用域被限制在 mpv 这一个进程，Mali 照旧从系统路径拿它那份。
#
# 用法：
#   bash mpv-libgbm-fix.sh            # 检查，缺就修
#   bash mpv-libgbm-fix.sh --check    # 只体检不修
#   bash mpv-libgbm-fix.sh --force    # 强制重解（库被搞坏时）
#
# 幂等：修好了再跑一次就是 no-op。

set -u

STORE_DIR="@@STORE_DIR@@"
LIBDIR="$STORE_DIR/lib/mpv"
STATE="$LIBDIR/.fixed"
# mpv 0.40+ 需要的新版 gbm 符号（Mesa 12.1 加入）
NEED_SYM="gbm_surface_create_with_modifiers"
SYSTEM_GBM="/usr/lib/aarch64-linux-gnu/libgbm.so.1"
LOG_TAG="romstore-libgbm-fix"

CHECK_ONLY=0
FORCE=0
for a in "$@"; do
    case "$a" in
        --check) CHECK_ONLY=1 ;;
        --force) FORCE=1 ;;
        *) echo "未知参数：$a"; exit 2 ;;
    esac
done

log()  { logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true; echo "[$LOG_TAG] $*"; }

# mpv 能不能用：能真跑起来一帧就算好。--frames=1 是最短路径。
probe_mpv() {
    local f; f=$(mktemp /tmp/romstore-mpvprobe-XXXXXX.mkv) || return 1
    # 生成一个 0.2 秒的纯色测试片（lavfi 要 ffmpeg；没有就退化成空跑）
    if command -v ffmpeg >/dev/null 2>&1; then
        ffmpeg -v error -f lavfi -i color=c=black:s=64x64:d=0.2 -c:v mpeg4 -y "$f" \
            >/dev/null 2>&1 || { rm -f "$f"; return 1; }
        LD_LIBRARY_PATH="$LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
            timeout 20 mpv --no-config --vo=drm --ao=none --frames=1 \
            "$f" >/dev/null 2>&1
        rc=$?
    else
        # 没 ffmpeg 就没有片源；退化为「只看符号能不能解析」
        LD_LIBRARY_PATH="$LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
            mpv --no-config --version >/dev/null 2>&1
        rc=$?
    fi
    rm -f "$f"
    return $rc
}

# 从 apt 的 deb 里解出 Mesa 的真 libgbm，放到 $LIBDIR
fetch_real_gbm() {
    local tmp; tmp=$(mktemp -d /tmp/romstore-gbm-XXXXXX) || return 1
    mkdir -p "$LIBDIR"
    # 已解过就不重复解
    if [ -f "$STATE" ] && [ "$FORCE" != "1" ]; then
        rm -rf "$tmp"; return 0
    fi
    ( cd "$tmp" || exit 1
      apt-get download libgbm1:arm64 >/dev/null 2>&1 || exit 1
      dpkg-deb -x ./*.deb x || exit 1
      src=$(find x -name 'libgbm.so.1.0.0' -type f | head -1)
      [ -n "$src" ] || exit 1
      cp -f "$src" "$LIBDIR/libgbm.so.1"
      # 自愈时通常已经是目标用户（ark），但安装器以 root 跑就会留 root 属主。
      # 商店以普通用户运行时虽然只读也够用，但同目录后续写入会 EPERM，统一一下。
      if [ "$(id -u)" = "0" ]; then
          chown -R "$(stat -c %u:%g "$STORE_DIR" 2>/dev/null || echo 0:0)" "$LIBDIR" 2>/dev/null || true
      fi
    ) || { rm -rf "$tmp"; return 1; }
    rm -rf "$tmp"
    date > "$STATE"
    return 0
}

echo "== mpv libgbm 体检 =="
command -v mpv >/dev/null 2>&1 || { log "没装 mpv，跳过（全屏预览不可用）"; exit 0; }

if probe_mpv; then
    log "mpv 正常，无需处理"
    exit 0
fi
log "mpv 起不来 —— 大概率是 libgbm 符号缺失"

if [ "$CHECK_ONLY" = "1" ]; then
    # 只报告，不动手
    echo "--- 诊断 ---"
    ls -l "$SYSTEM_GBM" 2>&1 | head -1
    echo "软链指向: $(readlink -f "$SYSTEM_GBM" 2>/dev/null)"
    mpv --no-config --version 2>&1 | head -2
    exit 1
fi

if [ "$FORCE" != "1" ] && [ -f "$STATE" ] && [ -f "$LIBDIR/libgbm.so.1" ]; then
    # 已经修过一次还不行 → 说明别的因素，清掉状态重来
    rm -f "$STATE"
fi

log "从 apt 解出 Mesa 真 libgbm 到 $LIBDIR（不碰系统软链）"
if ! fetch_real_gbm; then
    log "解包失败 —— 多半是机器离线。"
    log "手工办法：把任一 arm64 机器上的 /usr/lib/aarch64-linux-gnu/libgbm.so.1.0.0"
    log "（Mesa 的那个，不是 libMali）拷到 $STORE_DIR/lib/ 即可。"
    exit 1
fi

if probe_mpv; then
    log "修好了：mpv 能正常起（用 $LIBDIR/libgbm.so.1）"
    exit 0
fi

log "仍然起不来 —— 缺的可能不止 gbm，把完整报错留着："
mpv --no-config --version 2>&1 | head -5 >&2
exit 1
