# -*- coding: utf-8 -*-
"""全局配置与路径常量（Python 3.7 stdlib-only）

**所有路径都来自平台档案**（见 ``romstore/platform.py``），代码里不该再出现
``/mnt/Roms``、``/roms``、``/opt/system`` 这类字面量 —— 那台 ROCKNIX 掌机上
它们一个都不存在。

兼容性承诺：
  * 环境变量覆盖**优先于一切**（离线自测靠它，见 tests/test_media.py）
  * ``platform.json`` 不存在时按本机特征探测，结论与旧版 ArkOS 完全一致
  * 下面这些名字**继续存在且含义不变**，老代码/脚本不必改：
      NAS_ROOT / ES_ROMS / STORE_DIR / ES_SYSTEMS_CFG / ES_OPTIONS_DIR /
      DB_PATH / LOG_DIR / CACHE_DIR / STATE_PATH / FONT_CJK / FONT_CJK_ALT /
      METADATA_NAME / MOUNT_POINT / TRASH_DIR / MIN_FREE_BYTES / MEDIA_KEYS
"""
import os

from . import platform as _platform

P = _platform.load()

# ------------------------------------------------------------------ 平台身份
PLATFORM = P.get('platform', 'arkos')
PLATFORM_LABEL = P.get('platform_label', '')
PLATFORM_FILE = P.get('_from_file', '')

# ------------------------------------------------------------------ 路径
STORE_DIR = P['store_dir']

# NAS 挂载点。**改名要连 nas-roms-mount.sh 与 systemd 单元一起改**
# （它们由 install.sh 用同一份平台参数渲染，所以只需改 platform.py 的档案）。
NAS_ROOT = P['nas_mount']
MOUNT_POINT = NAS_ROOT

# 掌机本地 ROM 根。ES 各系统的 <path> 都在它下面（ArkOS=/roms，ROCKNIX=/storage/roms）
ES_ROMS = P['es_roms']

ES_SYSTEMS_CFG = P['es_systems_cfg']

# ES 入口：入口脚本放 es_entry_dir，gamelist 里写 ./<es_entry_name>。
# ArkOS 放在 options 系统的 /opt/system；ROCKNIX 放在 ports 系统的 /storage/roms/ports。
ES_ENTRY_DIR = P['es_entry_dir']
ES_ENTRY_NAME = P['es_entry_name']
ES_ENTRY_SYSTEM = P['es_entry_system']

# 兼容旧名（0.6.0 之前叫 ES_OPTIONS_DIR）
ES_OPTIONS_DIR = ES_ENTRY_DIR

ES_CACHE_DB = P.get('es_cache_db', '')
ES_SETTINGS_CFG = P.get('es_settings_cfg', '')

DB_PATH = os.path.join(STORE_DIR, 'index.db')
LOG_DIR = os.path.join(STORE_DIR, 'log')
CACHE_DIR = os.path.join(STORE_DIR, 'cache')
STATE_PATH = os.path.join(STORE_DIR, 'state.json')
SOURCE_PATH = os.path.join(STORE_DIR, 'source.json')
# 挂载状态（由 nas-roms-mount.sh 写、source.py 读）——
# rclone 挂载在 /proc/mounts 里的源名是 :smb{...}:Roms，**认不出主机**，
# 所以「挂的还是不是我配置的那个共享」只能靠这个文件判断。
MOUNT_STATE_PATH = os.path.join(STORE_DIR, '.mount-state')

# ------------------------------------------------------------------ 字体
# 注意：ROCKNIX 上没有任何系统级 CJK 字体，安装器会把一份拷进 STORE_DIR/fonts。
FONT_CJK = P['font']
FONT_CJK_ALT = P.get('font_alt', '') or ''

# 兜底顺序（主字体 → 备选 → 安装器可能拷贝过的位置 → 系统上能借到的）
FONT_SEARCH = [FONT_CJK, FONT_CJK_ALT,
               os.path.join(STORE_DIR, 'fonts', 'NotoSansSC-Regular.otf'),
               os.path.join(STORE_DIR, 'fonts', 'DroidSansFallback.ttf'),
               os.path.join(STORE_DIR, 'fonts', 'NotoSansCJK-Regular.ttc'),
               '/usr/config/scummvm/themes/themes/fonts-cjk/NotoSansSC-Regular.otf',
               '/usr/config/commander-baidupcs/res/DroidSansFallback.ttf',
               '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
               '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc']


def font_candidates():
    """按优先级返回去重后的字体路径列表（可能都不存在）"""
    out = []
    for p in FONT_SEARCH:
        if p and p not in out:
            out.append(p)
    return out


def font_path():
    """挑一个真实存在的中文字体；一个都没有时返回首选路径（调用方自行报错）"""
    for p in font_candidates():
        if os.path.exists(p):
            return p
    return FONT_CJK


# ------------------------------------------------------------------ 运行环境
GAMEPAD_DB = P.get('gamepad_db', '')
SDL_VIDEO_DRIVER = P.get('sdl_video_driver', '')
MPV_VO = P.get('mpv_vo', '')
MPV_AO = P.get('mpv_ao', '')
# 右栏「内嵌预览」：出声时的 ao 候选链、落帧目录（见 vplayer.MpvFramePlayer）
PREVIEW_AO = P.get('preview_ao', '')
PREVIEW_TMP = P.get('preview_tmp', '')

MOUNT_BACKEND = P.get('mount_backend', 'cifs')
MOUNT_SCRIPT = P.get('mount_script', '')
SAMBA_VERS = P.get('samba_vers', '')

SYSTEMD_DIR = P.get('systemd_dir', '/etc/systemd/system')
TARGET_USER = P.get('target_user', '')
ROMS_FS_SELFHEAL = bool(P.get('roms_fs_selfheal', False))
EXTRA_PATH = P.get('extra_path', '') or ''

RCLONE_BIN = '/usr/bin/rclone'

METADATA_NAME = 'metadata.pegasus.txt'

# 回收站（卸载时可撤销）。放在 ES ROM 根下面，跟随本地盘
TRASH_DIR = os.path.join(ES_ROMS, '.romstore-trash')

# 保留空间下限：低于此值拒绝安装
MIN_FREE_BYTES = 64 * 1024 * 1024

# 媒体只读（只送 ROM，封面/logo 从 NAS 现取）
MEDIA_KEYS = ('box_front', 'logo', 'video')


def ensure_dirs():
    for d in (STORE_DIR, LOG_DIR, CACHE_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def describe():
    """多行摘要（--validate / 安装器收尾 / 排障）"""
    return '\n'.join([
        '平台        : %s (%s)' % (PLATFORM, PLATFORM_LABEL),
        '平台档案    : %s' % (PLATFORM_FILE or '（未生成，按本机特征探测）'),
        '程序目录    : %s' % STORE_DIR,
        'NAS 挂载点  : %s' % NAS_ROOT,
        '本地 ROM 根 : %s' % ES_ROMS,
        'ES 配置     : %s' % ES_SYSTEMS_CFG,
        'ES 入口     : %s/%s（%s 系统）' % (ES_ENTRY_DIR, ES_ENTRY_NAME, ES_ENTRY_SYSTEM),
        'NAS 后端    : %s%s' % (MOUNT_BACKEND,
                                '（rclone FUSE）' if MOUNT_BACKEND == 'rclone' else '（内核 cifs）'),
        '显示/视频   : SDL_VIDEODRIVER=%s  mpv vo=%s%s'
        % (SDL_VIDEO_DRIVER or '(未设)', MPV_VO or '(默认)',
           (' ao=%s' % MPV_AO) if MPV_AO else ''),
        '中文字体    : %s' % font_path(),
    ])
