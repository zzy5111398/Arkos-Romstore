# -*- coding: utf-8 -*-
"""平台描述：让同一份代码在 ArkOS 与 AURKNIX/ROCKNIX 上都能跑

## 为什么要有这个模块

老版本把一堆路径写死在代码里（``/mnt/Roms``、``/roms``、``/opt/system``、
``/home/ark/.romstore``、``kmsdrm``、``/opt/inttools/gamecontrollerdb.txt``），
而在 AURKNIX（ROCKNIX 的社区分支）上这些**一个都不成立**：

    ============ ===================== ===================================
    项目         ArkOS（r36s .126）     AURKNIX/ROCKNIX（X35S .135）
    ============ ===================== ===================================
    根文件系统   可写                  **只读 squashfs**（/flash/SYSTEM）
    持久分区     /（全盘）             **只有 /storage**（ext4 7.3G）
    登录用户     ark uid=1002           **只有 root**
    NAS 传输     **内核 cifs** vers=2.1 **内核没有 cifs** → 只能 rclone FUSE
    显示栈       KMS/DRM 直出          **Sway / Wayland**
    ES 配置      /etc/emulationstation /storage/.config/emulationstation
    ES 入口      options → /opt/system ports → /storage/roms/ports
    自启单元     /etc/systemd/system   /storage/.config/system.d
    中文字体     系统自带 NotoCJK      **无**（要从 scummvm 主题里借）
    ============ ===================== ===================================

## 两类事实分开处理

* **安装期**：``install.sh`` 探测平台，把结论写成 ``<STORE_DIR>/platform.json``；
* **运行期**：本模块读 ``platform.json``。

文件缺失时按本机特征现场探测，再退到 ArkOS 默认值 ——
保证**老机器（.126）与离线测试的行为一个字节都不变**。

## 覆盖优先级（从高到低）

1. 单项环境变量（``ROMSTORE_NAS`` / ``ROMSTORE_ROMS`` / ``ROMSTORE_HOME`` …）
   —— 离线自测靠它，见 tests/test_media.py
2. ``platform.json`` 里的同名键
3. 探测到的平台默认值

``platform.json`` 只接受**已知键**，未知键会被忽略（避免手写文件把
config 带偏）；值一律转成字符串后使用。
"""
import json
import os

# ------------------------------------------------------------------ 平台档案

ARKOS = {
    'platform': 'arkos',
    'platform_label': 'ArkOS4Clone（R36S 等 aarch64 掌机）',
    'store_dir': '/home/ark/.romstore',
    'nas_mount': '/mnt/Roms',
    'es_roms': '/roms',
    'es_systems_cfg': '/etc/emulationstation/es_systems.cfg',
    # ES 入口：写进 options 系统的 <path> 目录，文件名为 es_entry_name
    'es_entry_dir': '/opt/system',
    'es_entry_name': 'RomStore.sh',
    'es_entry_system': 'options',
    'es_cache_db': '/home/ark/.emulationstation/imagecache.db',
    'es_settings_cfg': '/home/ark/.emulationstation/es_settings.cfg',
    'font': '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    'font_alt': '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc',
    'gamepad_db': '/opt/inttools/gamecontrollerdb.txt',
    'sdl_video_driver': 'kmsdrm',
    'mpv_vo': 'drm',
    'mpv_ao': 'alsa',
    # 右栏「内嵌预览」出声时给 mpv 的 ao（只用 mpv 后端，见 vplayer.py）。
    # vplayer 会自动在末尾补一个 null 兜底，所以这里只写"真声卡"的候选。
    'preview_ao': 'alsa',
    # mpv 帧服务的落帧目录（必须是 tmpfs；vplayer 会自己核实）
    'preview_tmp': '/tmp',
    # NAS 走内核 cifs
    'mount_backend': 'cifs',
    'mount_script': '/usr/local/sbin/nas-roms-mount.sh',
    'systemd_dir': '/etc/systemd/system',
    'systemd_enable': 'systemctl',
    'target_user': 'ark',
    # /roms 是 exFAT 且 errors=remount-ro，掉电会整盘只读 —— 这台要自愈服务
    'roms_fs_selfheal': True,
    'extra_path': '',
    'samba_vers': '2.1',
}

ROCKNIX = {
    'platform': 'rocknix',
    'platform_label': 'AURKNIX / ROCKNIX（X35S 等 RK3566 掌机）',
    'store_dir': '/storage/.romstore',
    'nas_mount': '/storage/nas-roms',
    'es_roms': '/storage/roms',
    'es_systems_cfg': '/storage/.config/emulationstation/es_systems.cfg',
    # 这台没有 options 系统；ES 的 Tools 菜单指向 /storage/.config/modules，
    # 但那是用 foot（终端）跑的，GUI 程序放那儿会被终端窗口压着。
    # ports 系统的命令是 runemu.sh，它会替我们停 ES / 退出后杀掉 ES，
    # 与官方「在线商店」(EmuDrop) 用的是同一条路 —— 实测可用。
    'es_entry_dir': '/storage/roms/ports',
    'es_entry_name': 'RomStore.sh',
    'es_entry_system': 'ports',
    'es_cache_db': '/storage/.emulationstation/imagecache.db',
    'es_settings_cfg': '/storage/.emulationstation/es_settings.cfg',
    # 这台**没有任何系统级 CJK 字体**，安装器会从 scummvm 主题里拷一份到
    # store 目录（/storage 持久分区），所以这里的首选路径是 store 内的副本。
    'font': '/storage/.romstore/fonts/NotoSansSC-Regular.otf',
    'font_alt': '/storage/.romstore/fonts/DroidSansFallback.ttf',
    'gamepad_db': '/storage/.config/SDL-GameControllerDB/gamecontrollerdb.txt',
    'sdl_video_driver': 'wayland',
    'mpv_vo': 'gpu',
    # 留空 = 不传 --ao，让 mpv 自己选（这台音频栈是 pulseaudio）
    'mpv_ao': '',
    # 右栏「内嵌预览」的 ao 候选：这台 ES 用 SDL_AUDIODRIVER=pulseaudio，
    # 所以 pulse 要排在 alsa 后面当备选。vplayer 会自动补末尾的 null。
    'preview_ao': 'alsa,pulse',
    # 这台 /tmp 实测是 tmpfs（488MB），帧文件写这里最合适
    'preview_tmp': '/tmp',
    # NAS 走 rclone 的 FUSE 挂载（这台内核没有 cifs，也没有 mount.cifs）
    'mount_backend': 'rclone',
    'mount_script': '/storage/.romstore/nas-roms-mount.sh',
    'systemd_dir': '/storage/.config/system.d',
    'systemd_enable': 'symlink',
    'target_user': 'root',
    'roms_fs_selfheal': False,
    # rclone 1.71 只找 fusermount3，而本机只有 fusermount(v2) ——
    # 安装器会在 store/bin 放一个同名桥接，这里把它挂到 PATH 前面
    'extra_path': '/storage/.romstore/bin',
    'samba_vers': '3.1.1',
}

DARKOS = {
    'platform': 'darkos',
    'platform_label': 'DarkOS（dArkOS，R36S 等 aarch64 掌机）',
    'store_dir': '/home/ark/.romstore',
    'nas_mount': '/mnt/Roms',
    'es_roms': '/roms',
    'es_systems_cfg': '/etc/emulationstation/es_systems.cfg',
    # 这台**没有 options 系统**（grep options = 0），但有 ports 系统，
    # 命令是 `sudo perfmax %GOVERNOR% %ROM%; nice -n -19 %ROM%; sudo perfnorm`
    # —— %ROM% 直接执行，不像 ArkOS 那样被固件的 runemu.sh 接管，
    # 与 ROCKNIX 走的是同一条路（实测可用，见 2026-10-14 真机）。
    'es_entry_dir': '/roms/ports',
    'es_entry_name': 'RomStore.sh',
    'es_entry_system': 'ports',
    'es_cache_db': '/home/ark/.emulationstation/imagecache.db',
    'es_settings_cfg': '/home/ark/.emulationstation/es_settings.cfg',
    'font': '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    'font_alt': '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc',
    'gamepad_db': '/opt/inttools/gamecontrollerdb.txt',
    'sdl_video_driver': 'kmsdrm',
    'mpv_vo': 'drm',
    'mpv_ao': 'alsa',
    'preview_ao': 'alsa',
    'preview_tmp': '/tmp',
    # 内核有 cifs（/proc/filesystems 里有），但**没装 mount.cifs** ——
    # install.sh 的自动补装会把它补上（跟 ArkOS 那台同一个坑）。
    'mount_backend': 'cifs',
    'mount_script': '/usr/local/sbin/nas-roms-mount.sh',
    'systemd_dir': '/etc/systemd/system',
    'systemd_enable': 'systemctl',
    'target_user': 'ark',
    # /roms 同样是 exFAT 且 errors=remount-ro（实测 mount 行有该串）
    'roms_fs_selfheal': True,
    'extra_path': '',
    'samba_vers': '2.1',
}

PROFILES = {'arkos': ARKOS, 'rocknix': ROCKNIX, 'darkos': DARKOS}

# platform.json 允许覆盖的键（= 两个档案键的并集，避免手写文件带偏 config）
_KEYS = set()
for _p in PROFILES.values():
    _KEYS |= set(_p.keys())

_EXTRA_KEYS = ('nas_mount_subdir', 'source_host', 'source_share')
_KEYS |= set(_EXTRA_KEYS)

# 现场探测用的特征路径（ROCKNIX 家族）
_ROCKNIX_MARKS = (
    '/storage/.config/system.d',
    '/storage/roms',
    '/flash/SYSTEM',
)
_OS_RELEASE = '/etc/os-release'
_ROCKNIX_OS_NAMES = ('rocknix', 'aurknix', 'jelos', 'batocera')
# ⚠️ DarkOS 的 /etc/os-release 自报的是 **Debian GNU/Linux**（内核 4.4.189 的
#    重打包版），既不含任何 ROCNIX 字样，也没有任何自己的标记 —— 唯一能认出它
#    的特征是「有 /opt/system 那套 ArkOS 脚本，但 es_systems.cfg 是 Batocera 风格」。
_DARKOS_MARKS = ('/opt/system',)
_DARKOS_SYSTEM_NAMES = ('darkos', 'dark os', 'darkosre')


def _isdir(p):
    try:
        return os.path.isdir(p)
    except OSError:
        return False


def _islink_or_file(p):
    try:
        return os.path.exists(p) or os.path.islink(p)
    except OSError:
        return False


def os_release():
    """把 /etc/os-release 解析成 dict（读不到就给空字典，不抛异常）"""
    d = {}
    try:
        with open(_OS_RELEASE, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return d


def detect():
    """判断本机是哪一族平台。

    优先级：环境变量 → /etc/os-release 自报 → 结构性特征 → 默认 arkos。

    ⚠️ 别只看 /storage 存在 —— 有些发行版会挂一个空的 /storage。
       这里要求 system.d 或 roms 至少有一个真在，才认 ROCKNIX。

    ⚠️ DarkOS 必须在 ROCKNIX **之后**判：它有 /opt/system（ArkOS 特征）但
       没有 /storage，所以两个分支本来就不冲突；但万一将来出现 /opt/system
       与 /storage 共存的机器，先判 ROCKNIX 更保守（只读根 + rclone 那套
       在 ArkOS 特征机上也能跑，反过来不行）。
    """
    env = (os.environ.get('ROMSTORE_PLATFORM') or '').strip().lower()
    if env in PROFILES:
        return env

    rel = os_release()
    name = (rel.get('OS_NAME') or rel.get('NAME') or rel.get('ID') or '').lower()
    if name and any(m in name for m in _ROCKNIX_OS_NAMES):
        return 'rocknix'
    if name and any(m in name for m in _DARKOS_SYSTEM_NAMES):
        return 'darkos'

    for m in _ROCKNIX_MARKS:
        if _isdir(m):
            return 'rocknix'

    # DarkOS：自报 Debian 认不出来，只能靠 hostname（实测 darkosre-r36）
    # 或 os-release 里有人加了 darkos 标记。
    try:
        host = ''
        with open('/proc/sys/kernel/hostname', 'r', encoding='utf-8',
                  errors='replace') as f:
            host = f.read().strip().lower()
    except Exception:
        host = ''
    if host and any(m in host for m in _DARKOS_SYSTEM_NAMES):
        return 'darkos'

    # ArkOS 结构性特征：可写的根 + options 系统目录
    if _isdir('/opt/system') or _islink_or_file('/etc/emulationstation/es_systems.cfg'):
        return 'arkos'
    return 'arkos'


def platform_file(store_dir=None):
    """platform.json 的位置（可由 ROMSTORE_PLATFORM_FILE 显式覆盖）"""
    env = os.environ.get('ROMSTORE_PLATFORM_FILE')
    if env:
        return env
    base = store_dir or os.environ.get('ROMSTORE_HOME') or PROFILES[detect()]['store_dir']
    return os.path.join(base, 'platform.json')


def _read_overrides(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        if k not in _KEYS:
            continue
        if v is None or isinstance(v, (list, dict)):
            continue
        if isinstance(v, bool):
            out[k] = v
        else:
            out[k] = str(v)
    return out


_CACHE = {}


def load(refresh=False):
    """返回当前生效的平台参数字典

    叠加顺序：缺陷值（平台档案） ← platform.json ← 环境变量。
    结果带两个仅供排障用的键：``_from_file``（读到没读到的 platform.json 路径）、
    ``_detected``（现场探测结论，不受 platform.json 影响）。
    """
    if _CACHE and not refresh:
        return _CACHE['p']

    detected = detect()
    p = dict(PROFILES[detected])
    p['_detected'] = detected

    store = os.environ.get('ROMSTORE_HOME')
    pf = platform_file(store)
    if os.environ.get('ROMSTORE_HOME'):
        p['store_dir'] = os.environ['ROMSTORE_HOME']

    ov = _read_overrides(pf)
    if ov.get('platform') in PROFILES:
        # platform.json 是安装期的结论，权威 —— 用它选档案
        p = dict(PROFILES[ov['platform']])
        p['_detected'] = detected
    p.update(ov)
    p['_from_file'] = pf if ov else ''
    if not os.path.exists(pf):
        p['_from_file'] = ''

    # 环境变量兜底（离线自测用）
    env_map = {
        'ROMSTORE_NAS': 'nas_mount',
        'ROMSTORE_ROMS': 'es_roms',
        'ROMSTORE_HOME': 'store_dir',
        'ROMSTORE_ES_CFG': 'es_systems_cfg',
        'ROMSTORE_ES_OPTIONS': 'es_entry_dir',
        'ROMSTORE_ES_ENTRY': 'es_entry_name',
        'ROMSTORE_MOUNT_SCRIPT': 'mount_script',
        'ROMSTORE_FONT': 'font',
        'ROMSTORE_FONT_ALT': 'font_alt',
        'ROMSTORE_GAMEPAD_DB': 'gamepad_db',
        'ROMSTORE_SDL_DRIVER': 'sdl_video_driver',
        'ROMSTORE_MPV_VO': 'mpv_vo',
        'ROMSTORE_MPV_AO': 'mpv_ao',
        'ROMSTORE_PREVIEW_AO': 'preview_ao',
        'ROMSTORE_PREVIEW_TMP': 'preview_tmp',
        'ROMSTORE_BACKEND': 'mount_backend',
    }
    for k, v in env_map.items():
        if os.environ.get(k):
            p[v] = os.environ[k]

    _CACHE['p'] = p
    return p


def describe(p=None):
    """一行摘要（给 --validate / --source-show / 安装器收尾用）"""
    p = p or load()
    src = p.get('_from_file') or '（未生成 platform.json，按本机特征现场探测）'
    return '%s [%s]  %s' % (p.get('platform_label') or p.get('platform', '?'),
                            p.get('platform', '?'), src)
