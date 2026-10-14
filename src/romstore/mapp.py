# -*- coding: utf-8 -*-
"""NAS 目录名 -> EmulationStation 系统 的映射表（**三套**，按平台选）

置信度 conf:
  'ok'    已确认（系统名与 ES 配置精确对应）
  'maybe' 推测（街机 romset 归属、或 ES 侧有多种可能），界面会打 ⚠
  None    无对应 ES 系统，不参与安装

## 为什么是三套表

两侧系统名各叫各的，而且**三族固件叫得还不一样**：

    NAS 目录    ArkOS(.126 原)   AURKNIX/ROCKNIX(.135)   DarkOS(.126 现)
    FC          nes              nes / famicom             nes / famicom
    SFC         sfc              sfc / snes               sfc / snes
    PS1         psx              psx                       psx
    PSP         **ppsspp**       **psp**                   **ppsspp**
    MD-CD       segacd           segacd / megacd           segacd
    NEOGEO-CD   neogeocd         **neocd**                 neogeocd
    POKE MINI   pokemonmini      **pokemini**              pokemonmini
    DOS         msdos            **pc**                    msdos
    MAME 系列   mame2003         **mame**                  mame2003 / mame2010
    NGC / WII   不支持           **gamecube / wii 都在**   不支持
    Atomiswave  atomiswave       atomiswave                **Sega Atomiswave（带空格）**

所以不能用字符串归一化代替（PSP 那个就是反例：ArkOS/DarkOS 侧 ES 名是 ppsspp，
NAS 目录叫 PSP）。ROCKNIX 与 DarkOS 表还允许一个 NAS 目录写下**多个候选 ES 名**
（用 `|` 分隔），运行期挑本机 es_systems.cfg 里真有的那个 —— 这样同一份代码
换一台固件版本不同的机器也不会指错目录。

⚠️ **别在三个平台间互相抄候选名**：PSP 那格 ArkOS/DarkOS 是 `ppsspp`、
ROCKNIX 是 `psp`；DOS 那格 ArkOS/DarkOS 是 `msdos`、ROCKNIX 是 `pc`。
路径也各自独立（ArkOS/DarkOS 写绝对 /roms/…，ROCKNIX 由 ES_ROMS 拼）。

映射依据：
  * ArkOS 侧   2026-10-11 实机：ES 128 个系统 / NAS 85 个目录
  * ROCKNIX 侧 2026-10-12 实机：/storage/.config/emulationstation/es_systems.cfg
    共 128 个系统，逐个核对过 name/path/extension
  * DarkOS 侧  2026-10-14 实机：/etc/emulationstation/es_systems.cfg 共 126 个系统，
    逐个核对过 name/path/extension；其中 **`snes` 这个 name 在文件里出现两次**
    （/roms/sufami/ 与 /roms/snes/），是这台机独有的坑
"""
import os
import re
import xml.etree.ElementTree as ET

from . import config

# ---------------------------------------------------------------- ArkOS 映射表
MAP_ARKOS = [
    # ---- 任天堂 ----
    ('FC',                  'nes',            '/roms/nes',            'ok',    '红白机'),
    ('FC hack',             'nes',            '/roms/nes',            'ok',    'FC 改版'),
    ('FC-HD',               'nes',            '/roms/nes',            'ok',    'FC 高清改版'),
    ('SFC',                 'sfc',            '/roms/sfc',            'ok',    '超任'),
    ('SFC hack',            'sfc',            '/roms/sfc',            'ok',    'SFC 改版'),
    ('SFC-MSU1',            'snesmsu',        '/roms/snesmsu1',       'maybe', 'MSU1 音频增强版'),
    ('GB',                  'gb',             '/roms/gb',             'ok',    'Game Boy'),
    ('GBC',                 'gbc',            '/roms/gbc',            'ok',    'GBC'),
    ('GBA',                 'gba',            '/roms/gba',            'ok',    'GBA'),
    ('N64',                 'n64',            '/roms/n64',            'ok',    'N64'),
    ('NDS',                 'nds',            '/roms/nds',            'ok',    'NDS'),
    ('POKE MINI',           'pokemonmini',    '/roms/pokemonmini',    'ok',    '宝可梦 mini'),
    ('Virtual Boy',         'virtualboy',     '/roms/virtualboy',     'ok',    'VB'),
    ('GAME WATCH',          'gameandwatch',   '/roms/gameandwatch',   'ok',    'Game & Watch'),

    # ---- 世嘉 ----
    ('MD',                  'megadrive',      '/roms/megadrive',      'ok',    'MD'),
    ('MD hack',             'megadrive',      '/roms/megadrive',      'ok',    'MD 改版'),
    ('MD hack(picodrive)',  'megadrive',      '/roms/megadrive',      'ok',    'MD 改版(picodrive)'),
    ('MD-32X',              'sega32x',        '/roms/sega32x',        'ok',    '32X'),
    ('MD-CD',               'segacd',         '/roms/segacd',         'ok',    'Mega-CD'),
    ('SMS',                 'mastersystem',   '/roms/mastersystem',   'ok',    'Master System'),
    ('GG',                  'gamegear',       '/roms/gamegear',       'ok',    'Game Gear'),
    ('DC',                  'dreamcast',      '/roms/dreamcast',      'ok',    'Dreamcast'),
    ('DC hack',             'dreamcast',      '/roms/dreamcast',      'ok',    'DC 改版'),

    # ---- 索尼 ----
    ('PS1',                 'psx',            '/roms/psx',            'ok',    'PS1'),
    ('PS1 hack',            'psx',            '/roms/psx',            'ok',    'PS1 改版'),
    ('PSP',                 'ppsspp',         '/roms/psp',            'ok',    'PSP'),

    # ---- 其他主机 ----
    ('3DO',                 '3do',            '/roms/3do',            'ok',    '3DO'),
    ('ATARI2600',           'atari2600',      '/roms/atari2600',      'ok',    'Atari 2600'),
    ('ATARI5200',           'atari5200',      '/roms/atari5200',      'ok',    'Atari 5200'),
    ('ATARI7800',           'atari7800',      '/roms/atari7800',      'ok',    'Atari 7800'),
    ('LYNX',                'atarilynx',      '/roms/atarilynx',      'ok',    'Atari Lynx'),
    ('PCE',                 'pcengine',       '/roms/pcengine',       'ok',    'PC Engine'),
    ('PCE-CD',              'pcenginecd',     '/roms/pcenginecd',     'ok',    'PC Engine CD'),
    ('PC-FX',               'pcfx',           '/roms/pcfx',           'ok',    'PC-FX'),
    ('NEOGEO-CD',           'neogeocd',       '/roms/neogeocd',       'ok',    'Neo Geo CD'),
    ('NGPC',                'ngpc',           '/roms/ngpc',           'ok',    'Neo Geo Pocket Color'),
    ('WS',                  'wonderswan',     '/roms/wonderswan',     'ok',    'WonderSwan'),
    ('WSC',                 'wonderswancolor','/roms/wonderswancolor','ok',    'WonderSwan Color'),
    ('SS',                  'saturn',         '/roms/saturn',         'ok',    '世嘉土星'),
    ('SS plus',             'saturn',         '/roms/saturn',         'ok',    '土星补充包'),
    ('NAOMI',               'naomi',          '/roms/naomi',          'ok',    'NAOMI 街机'),
    ('OPENBOR',             'openbor',        '/roms/openbor',        'ok',    'OpenBOR'),
    ('DOS',                 'msdos',          '/roms/dos',            'ok',    'DOS'),
    ('PC',                  'msdos',          '/roms/dos',            'maybe', 'PC 游戏，按 DOS 归置'),

    # ---- 街机 romset（同一 ES 系统下的分类子集）----
    ('FBNEO ACT',           'arcade',         '/roms/arcade',         'maybe', 'FBNeo 动作类'),
    ('FBNEO ACT V',         'arcade',         '/roms/arcade',         'maybe', 'FBNeo 动作类'),
    ('FBNEO ACT hack1',     'arcade',         '/roms/arcade',         'maybe', 'FBNeo 改版'),
    ('FBNEO ACT hack2',     'arcade',         '/roms/arcade',         'maybe', 'FBNeo 改版'),
    ('FBA ACT hack',        'arcade',         '/roms/arcade',         'maybe', 'FBNeo 改版'),
    ('FBNEO ETC',           'arcade',         '/roms/arcade',         'maybe', 'FBNeo 其他'),
    ('FBNEO ETC V',         'arcade',         '/roms/arcade',         'maybe', 'FBNeo 其他'),
    ('FBNEO FLY',           'arcade',         '/roms/arcade',         'maybe', 'FBNeo 飞行'),
    ('FBNEO FLY V',         'arcade',         '/roms/arcade',         'maybe', 'FBNeo 飞行'),
    ('FBNEO FTG',           'arcade',         '/roms/arcade',         'maybe', 'FBNeo 格斗'),
    ('FBNEO FTG hack',      'arcade',         '/roms/arcade',         'maybe', 'FBNeo 格斗改版'),
    ('FBNEO RAC',           'arcade',         '/roms/arcade',         'maybe', 'FBNeo 竞速'),
    ('FBNEO STG',           'arcade',         '/roms/arcade',         'maybe', 'FBNeo 射击'),
    ('FBNEO STG V',         'arcade',         '/roms/arcade',         'maybe', 'FBNeo 射击'),
    ('FBNEO STG hack',      'arcade',         '/roms/arcade',         'maybe', 'FBNeo 射击改版'),
    ('MAME ACT',            'mame2003',       '/roms/mame2003',       'maybe', 'MAME 动作类'),
    ('MAME ETC',            'mame2003',       '/roms/mame2003',       'maybe', 'MAME 其他'),
    ('MAME FLY',            'mame2003',       '/roms/mame2003',       'maybe', 'MAME 飞行'),
    ('MAME FLY V',          'mame2003',       '/roms/mame2003',       'maybe', 'MAME 飞行'),
    ('MAME FTG',            'mame2003',       '/roms/mame2003',       'maybe', 'MAME 格斗'),
    ('MAME FTG hack',       'mame2003',       '/roms/mame2003',       'maybe', 'MAME 格斗改版'),
    ('MAME RAC',            'mame2003',       '/roms/mame2003',       'maybe', 'MAME 竞速'),
    ('MAME STG',            'mame2003',       '/roms/mame2003',       'maybe', 'MAME 射击'),
    ('MAME STG V',          'mame2003',       '/roms/mame2003',       'maybe', 'MAME 射击'),
]

UNSUPPORTED_ARKOS = [
    ('3DS',        '本机 ES 无 3ds 系统'),
    ('NGC',        '本机 ES 无 GC 系统'),
    ('PS2',        '本机 ES 无 PS2 系统'),
    ('PS2 hack',   '本机 ES 无 PS2 系统'),
    ('WII',        '本机 ES 无 WII 系统'),
    ('WII Ware',   '本机 ES 无 WII 系统'),
    ('MODEL2',     '本机 ES 无 Model 2'),
    ('MODEL3',     '本机 ES 无 Model 3'),
    ('TeknoParrot','本机 ES 无街机框体系统'),
    ('LIGHT GUN',  '光枪专用，无独立 ES 系统'),
]

# -------------------------------------------------------------- ROCKNIX 映射表
# 格式与 ArkOS 表不同：第 2 列是 **候选 ES 名**（`|` 分隔，取本机真有的第一个），
# 第 3 列是 **ES ROM 根下的子目录名**，绝对路径 = config.ES_ROMS + '/' + 子目录。
MAP_ROCKNIX_RAW = [
    # ---- 任天堂 ----
    # FC 同时在 nes 与 famicom 两个系统里存在。选 nes：
    #   1) 这台机器上另一家「在线商店」(EmuDrop) 也是往 nes 放 ROM，口径一致
    #   2) /storage/roms/nes 已经有 ROM，famicom 是空的（ES 默认不显示空系统）
    ('FC',                  'nes|famicom',          'nes',              'ok',    '红白机'),
    ('FC hack',             'nes|famicom',          'nes',              'ok',    'FC 改版'),
    ('FC-HD',               'nes|famicom',          'nes',              'ok',    'FC 高清改版'),
    ('SFC',                 'sfc|snes',             'sfc',              'ok',    '超任'),
    ('SFC hack',            'sfc|snes',             'sfc',              'ok',    'SFC 改版'),
    ('SFC-MSU1',            'snesmsu1|snesmsu',     'snesmsu1',         'maybe', 'MSU1 音频增强版'),
    ('GB',                  'gb',                   'gb',               'ok',    'Game Boy'),
    ('GBC',                 'gbc',                  'gbc',              'ok',    'GBC'),
    ('GBA',                 'gba',                  'gba',              'ok',    'GBA'),
    ('N64',                 'n64',                  'n64',              'ok',    'N64'),
    ('NDS',                 'nds',                  'nds',              'ok',    'NDS'),
    ('POKE MINI',           'pokemini|pokemonmini', 'pokemini',         'ok',    '宝可梦 mini'),
    ('Virtual Boy',         'virtualboy',           'virtualboy',       'ok',    'VB'),
    ('GAME WATCH',          'gameandwatch',         'gameandwatch',     'ok',    'Game & Watch'),

    # ---- 世嘉 ----
    ('MD',                  'megadrive|genesis',    'megadrive',        'ok',    'MD'),
    ('MD hack',             'megadrive|genesis',    'megadrive',        'ok',    'MD 改版'),
    ('MD hack(picodrive)',  'megadrive|genesis',    'megadrive',        'ok',    'MD 改版(picodrive)'),
    ('MD-32X',              'sega32x',              'sega32x',          'ok',    '32X'),
    ('MD-CD',               'segacd|megacd',        'segacd',           'ok',    'Mega-CD'),
    ('SMS',                 'mastersystem',         'mastersystem',     'ok',    'Master System'),
    ('GG',                  'gamegear',             'gamegear',         'ok',    'Game Gear'),
    ('DC',                  'dreamcast',            'dreamcast',        'ok',    'Dreamcast'),
    ('DC hack',             'dreamcast',            'dreamcast',        'ok',    'DC 改版'),

    # ---- 索尼 ----
    ('PS1',                 'psx',                  'psx',              'ok',    'PS1'),
    ('PS1 hack',            'psx',                  'psx',              'ok',    'PS1 改版'),
    # ⚠ ArkOS 侧这个系统的 ES 名是 ppsspp，ROCKNIX 侧是 psp —— 别互相抄
    ('PSP',                 'psp|ppsspp',           'psp',              'ok',    'PSP'),

    # ---- 其他主机 ----
    ('3DO',                 '3do',                  '3do',              'ok',    '3DO'),
    ('ATARI2600',           'atari2600',            'atari2600',        'ok',    'Atari 2600'),
    ('ATARI5200',           'atari5200',            'atari5200',        'ok',    'Atari 5200'),
    ('ATARI7800',           'atari7800',            'atari7800',        'ok',    'Atari 7800'),
    ('LYNX',                'atarilynx',            'atarilynx',        'ok',    'Atari Lynx'),
    ('PCE',                 'pcengine|tg16',        'pcengine',         'ok',    'PC Engine'),
    ('PCE-CD',              'pcenginecd|tg16cd',    'pcenginecd',       'ok',    'PC Engine CD'),
    ('PC-FX',               'pcfx',                 'pcfx',             'ok',    'PC-FX'),
    ('NEOGEO-CD',           'neocd|neogeocd',       'neocd',            'ok',    'Neo Geo CD'),
    ('NGPC',                'ngpc|ngp',             'ngpc',             'ok',    'Neo Geo Pocket Color'),
    ('WS',                  'wonderswan',           'wonderswan',       'ok',    'WonderSwan'),
    ('WSC',                 'wonderswancolor',      'wonderswancolor',  'ok',    'WonderSwan Color'),
    ('SS',                  'saturn',               'saturn',           'ok',    '世嘉土星'),
    ('SS plus',             'saturn',               'saturn',           'ok',    '土星补充包'),
    ('NAOMI',               'naomi|atomiswave',     'naomi',            'ok',    'NAOMI 街机'),
    ('OPENBOR',             'openbor',              'openbor',          'ok',    'OpenBOR'),
    # ROCKNIX 没有 msdos，对应的是 `pc`（扩展名 .com/.bat/.exe/.dosz）
    ('DOS',                 'pc|msdos',             'pc',               'ok',    'DOS'),
    ('PC',                  'pc|msdos',             'pc',               'maybe', 'PC 游戏'),

    # ROCKNIX 上这几个系统**是有的**（ArkOS 上没有）—— 但体积大、跑得动跑不动另说，
    # 所以一律打 maybe，让界面显示 ⚠，由用户自己决定装不装。
    ('NGC',                 'gamecube',             'gamecube',         'maybe', 'NGC（需 Dolphin，体积大）'),
    ('WII',                 'wii',                  'wii',              'maybe', 'WII（需 Dolphin，体积大）'),
    ('WII Ware',            'wiiware',              'wiiware',          'maybe', 'WII Ware'),

    # ---- 街机 romset ----
    # FBNeo 系列的 ES 名与 ArkOS 一致（arcade）；MAME 系列则是 mame（不是 mame2003）
    ('FBNEO ACT',           'arcade|fbn',           'arcade',           'maybe', 'FBNeo 动作类'),
    ('FBNEO ACT V',         'arcade|fbn',           'arcade',           'maybe', 'FBNeo 动作类'),
    ('FBNEO ACT hack1',     'arcade|fbn',           'arcade',           'maybe', 'FBNeo 改版'),
    ('FBNEO ACT hack2',     'arcade|fbn',           'arcade',           'maybe', 'FBNeo 改版'),
    ('FBA ACT hack',        'arcade|fbn',           'arcade',           'maybe', 'FBNeo 改版'),
    ('FBNEO ETC',           'arcade|fbn',           'arcade',           'maybe', 'FBNeo 其他'),
    ('FBNEO ETC V',         'arcade|fbn',           'arcade',           'maybe', 'FBNeo 其他'),
    ('FBNEO FLY',           'arcade|fbn',           'arcade',           'maybe', 'FBNeo 飞行'),
    ('FBNEO FLY V',         'arcade|fbn',           'arcade',           'maybe', 'FBNeo 飞行'),
    ('FBNEO FTG',           'arcade|fbn',           'arcade',           'maybe', 'FBNeo 格斗'),
    ('FBNEO FTG hack',      'arcade|fbn',           'arcade',           'maybe', 'FBNeo 格斗改版'),
    ('FBNEO RAC',           'arcade|fbn',           'arcade',           'maybe', 'FBNeo 竞速'),
    ('FBNEO STG',           'arcade|fbn',           'arcade',           'maybe', 'FBNeo 射击'),
    ('FBNEO STG V',         'arcade|fbn',           'arcade',           'maybe', 'FBNeo 射击'),
    ('FBNEO STG hack',      'arcade|fbn',           'arcade',           'maybe', 'FBNeo 射击改版'),
    ('MAME ACT',            'mame',                 'mame',             'maybe', 'MAME 动作类'),
    ('MAME ETC',            'mame',                 'mame',             'maybe', 'MAME 其他'),
    ('MAME FLY',            'mame',                 'mame',             'maybe', 'MAME 飞行'),
    ('MAME FLY V',          'mame',                 'mame',             'maybe', 'MAME 飞行'),
    ('MAME FTG',            'mame',                 'mame',             'maybe', 'MAME 格斗'),
    ('MAME FTG hack',       'mame',                 'mame',             'maybe', 'MAME 格斗改版'),
    ('MAME RAC',            'mame',                 'mame',             'maybe', 'MAME 竞速'),
    ('MAME STG',            'mame',                 'mame',             'maybe', 'MAME 射击'),
    ('MAME STG V',          'mame',                 'mame',             'maybe', 'MAME 射击'),
    # NAS 上新增的目录（ArkOS 表里没有），ROCKNIX 有 hbmame 系统
    ('HBMAME',              'hbmame',               'hbmame',           'maybe', 'HBMAME 改版街机'),
]

UNSUPPORTED_ROCKNIX = [
    ('3DS',        '本机 ES 无 3ds 系统（NDS 用 nds）'),
    ('PS2',        '本机 ES 无 PS2 系统'),
    ('PS2 hack',   '本机 ES 无 PS2 系统'),
    ('MODEL2',     '本机 ES 无 Model 2'),
    ('MODEL3',     '本机 ES 无 Model 3'),
    ('TeknoParrot','本机 ES 无街机框体系统'),
    ('LIGHT GUN',  '光枪专用，无独立 ES 系统'),
]

# ================================================================= 解析支持
# es_systems.cfg 不是合法 XML：某些系统的 <command> 里有裸 &（"2>&1"），
# 会让 ElementTree 直接抛 not well-formed。解析前先把「非实体」的 & 转义掉。
_AMP_RE = re.compile(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)')


def load_es_systems(cfg_path=None):
    """解析 es_systems.cfg，返回 [(name, path, extensions_set)]

    注意：该文件含裸 &，需先做转义修复才能喂给 ElementTree。
    """
    p = cfg_path or config.ES_SYSTEMS_CFG
    if not os.path.isfile(p):
        return []
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            raw = f.read()
        if raw.startswith('\ufeff'):
            raw = raw[1:]
        raw = _AMP_RE.sub('&amp;', raw)
        root = ET.fromstring(raw)
    except Exception:
        return []
    out = []
    for sysel in root.findall('system'):
        name = (sysel.findtext('name') or '').strip()
        path = (sysel.findtext('path') or '').strip().rstrip('/')
        ext = (sysel.findtext('extension') or '').strip()
        exts = set(e.lower() for e in ext.split() if e.startswith('.'))
        if name:
            out.append((name, path, exts))
    return out


_ES_NAMES = {'v': None}


def es_names(refresh=False):
    """本机 es_systems.cfg 里的系统名集合（解析不到就是空集合）

    用于从候选 ES 名里挑出**这台机器真有**的那个。
    """
    if _ES_NAMES['v'] is None or refresh:
        try:
            _ES_NAMES['v'] = set(n for n, _p, _e in load_es_systems())
        except Exception:
            _ES_NAMES['v'] = set()
    return _ES_NAMES['v']


def _pick(name_candidates):
    """从 'a|b|c' 里挑本机存在的第一个；都没有（或解析不到 cfg）就用第一个。"""
    cands = [c for c in (name_candidates or '').split('|') if c]
    if not cands:
        return ''
    names = es_names()
    if names:
        for c in cands:
            if c in names:
                return c
    return cands[0]


UNSUPPORTED_DARKOS = [
    ('3DS',        '本机 ES 无 3ds 系统（NDS 用 nds）'),
    ('NGC',        '本机 ES 无 gamecube 系统（Batocera 系未收录）'),
    ('PS2',        '本机 ES 无 PS2 系统'),
    ('PS2 hack',   '本机 ES 无 PS2 系统'),
    ('WII',        '本机 ES 无 WII 系统'),
    ('WII Ware',   '本机 ES 无 WII 系统'),
    ('MODEL2',     '本机 ES 无 Model 2'),
    ('MODEL3',     '本机 ES 无 Model 3'),
    ('TeknoParrot','本机 ES 无街机框体系统'),
    ('LIGHT GUN',  '光枪专用，无独立 ES 系统'),
]

# -------------------------------------------------------------- DarkOS 映射表
# 目标：dArkOS（10.168.1.126，hostname darkosre-r36，Debian 13 根 / 内核 4.4.189）
#
# 这台的 ES 是 **Batocera 风格**：126 个系统名里一大批是 ArkOS 从来没有的短名，
# 还有带空格的名字（`Sega Atomiswave`、`famicom disk system`、`tic-80`…）。
# 但**文件系统层面它和 ArkOS 一模一样**（/roms 根、绝对路径、同样的 /opt/system），
# 所以本表的第 3 列直接写绝对路径，与 ArkOS 表同形；只有第 2 列是候选列表。
#
# 实机依据（2026-10-14，逐个核对 /etc/emulationstation/es_systems.cfg 的
# name/path/extension 三元组）：
#   * `ppsspp` → /roms/psp/（**和 ArkOS 一样**，别照抄 ROCKNIX 表的 `psp`）
#   * `msdos`  → /roms/dos/（ArkOS 口径，不是 ROCKNIX 的 `pc`）
#   * `mame2003` → /roms/mame2003/ 与 `mame2010` → /roms/mame/ **两个都在**
#   * `nes` 与 `famicom` **成对存在**，且 `fds` 是 `famicom disk system`
#   * `snes` 在 cfg 里**重复出现两次**（/roms/sufami/ 与 /roms/snes/）——
#     这是这台机独有的坑，见下面的注释
#   * ES 名带空格的只有 Sega Atomiswave（路径却是 /roms/atomiswave/）
MAP_DARKOS_RAW = [
    # ---- 任天堂 ----
    # 实测 /roms/nes 与 /roms/famicom **都是空的**，ES 默认不显示空系统，
    # 所以装进去哪个都能看见；选 nes（主流命名，且 ArkOS 表也是它）。
    ('FC',                  'nes|famicom',            '/roms/nes',           'ok',    '红白机'),
    ('FC hack',             'nes|famicom',            '/roms/nes',           'ok',    'FC 改版'),
    ('FC-HD',               'nes|famicom',            '/roms/nes',           'ok',    'FC 高清改版'),
    ('SFC',                 'sfc|snes',               '/roms/sfc',           'ok',    '超任'),
    ('SFC hack',            'snes-hacks|sfc',         '/roms/snes-hacks',    'ok',    'SFC 改版'),
    # snesmsu 这个系统的**路径**叫 snesmsu1（名字与目录不同名，ArkOS 也这样）
    ('SFC-MSU1',            'snesmsu|snesmsu1',       '/roms/snesmsu1',      'maybe', 'MSU1 音频增强版'),
    ('GB',                  'gb',                     '/roms/gb',            'ok',    'Game Boy'),
    ('GBC',                 'gbc',                    '/roms/gbc',           'ok',    'GBC'),
    ('GBA',                  'gba',                    '/roms/gba',           'ok',    'GBA'),
    ('N64',                 'n64',                    '/roms/n64',           'ok',    'N64'),
    ('NDS',                 'nds',                    '/roms/nds',           'ok',    'NDS'),
    ('POKE MINI',           'pokemonmini|pokemini',   '/roms/pokemonmini',   'ok',    '宝可梦 mini'),
    ('Virtual Boy',         'virtualboy',             '/roms/virtualboy',    'ok',    'VB'),
    ('GAME WATCH',          'gameandwatch',           '/roms/gameandwatch',  'ok',    'Game & Watch'),

    # ---- 世嘉 ----
    ('MD',                  'megadrive|genesis',      '/roms/megadrive',     'ok',    'MD'),
    ('MD hack',             'megadrive|genesis',      '/roms/megadrive',     'ok',    'MD 改版'),
    ('MD hack(picodrive)',  'megadrive|genesis',      '/roms/megadrive',     'ok',    'MD 改版(picodrive)'),
    ('MD-32X',              'sega32x',                '/roms/sega32x',       'ok',    '32X'),
    ('MD-CD',               'segacd',                 '/roms/segacd',        'ok',    'Mega-CD'),
    ('SMS',                 'mastersystem',           '/roms/mastersystem',  'ok',    'Master System'),
    ('GG',                  'gamegear',               '/roms/gamegear',      'ok',    'Game Gear'),
    ('DC',                  'dreamcast',              '/roms/dreamcast',     'ok',    'Dreamcast'),
    ('DC hack',             'dreamcast',              '/roms/dreamcast',     'ok',    'DC 改版'),

    # ---- 索尼 ----
    ('PS1',                 'psx',                    '/roms/psx',           'ok',    'PS1'),
    ('PS1 hack',            'psx',                    '/roms/psx',           'ok',    'PS1 改版'),
    # ⚠ 这台的 ppsspp 路径是 /roms/psp/，与 ArkOS 表相同 —— **不是** ROCKNIX 那个 psp
    ('PSP',                 'ppsspp|psp',             '/roms/psp',           'ok',    'PSP'),

    # ---- 其他主机 ----
    ('3DO',                 '3do',                    '/roms/3do',           'ok',    '3DO'),
    ('ATARI2600',           'atari2600',              '/roms/atari2600',     'ok',    'Atari 2600'),
    ('ATARI5200',           'atari5200',              '/roms/atari5200',     'ok',    'Atari 5200'),
    ('ATARI7800',           'atari7800',              '/roms/atari7800',     'ok',    'Atari 7800'),
    ('LYNX',                'atarilynx',              '/roms/atarilynx',     'ok',    'Atari Lynx'),
    ('PCE',                 'pcengine',               '/roms/pcengine',      'ok',    'PC Engine'),
    ('PCE-CD',              'pcenginecd',             '/roms/pcenginecd',    'ok',    'PC Engine CD'),
    ('PC-FX',               'pcfx',                   '/roms/pcfx',          'ok',    'PC-FX'),
    ('NEOGEO-CD',           'neogeocd|neocd',         '/roms/neogeocd',      'ok',    'Neo Geo CD'),
    ('NGPC',                'ngpc',                   '/roms/ngpc',          'ok',    'Neo Geo Pocket Color'),
    ('WS',                  'wonderswan',             '/roms/wonderswan',    'ok',    'WonderSwan'),
    ('WSC',                 'wonderswancolor',        '/roms/wonderswancolor','ok',   'WonderSwan Color'),
    ('SS',                  'saturn',                 '/roms/saturn',        'ok',    '世嘉土星'),
    ('SS plus',             'saturn',                 '/roms/saturn',        'ok',    '土星补充包'),
    # 这台 Atomiswave 的 ES 名**带空格**（`Sega Atomiswave`），路径却是 /roms/atomiswave
    ('NAOMI',               'naomi|Sega Atomiswave',  '/roms/naomi',         'ok',    'NAOMI 街机'),
    ('OPENBOR',             'openbor',                '/roms/openbor',       'ok',    'OpenBOR'),
    # 这台有 msdos（不是 ROCKNIX 的 pc）
    ('DOS',                 'msdos|pc',               '/roms/dos',           'ok',    'DOS'),
    ('PC',                  'msdos|pc',               '/roms/dos',           'maybe', 'PC 游戏，按 DOS 归置'),

    # ---- 街机 romset ----
    # ⚠ 这台同时有 mame2003(/roms/mame2003) 与 mame2010(/roms/mame)，优先前者
    #   （NAS 的 MAME 分类目录是 0.5.x 时代按 mame2003 建的）
    ('FBNEO ACT',           'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 动作类'),
    ('FBNEO ACT V',         'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 动作类'),
    ('FBNEO ACT hack1',     'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 改版'),
    ('FBNEO ACT hack2',     'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 改版'),
    ('FBA ACT hack',        'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 改版'),
    ('FBNEO ETC',           'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 其他'),
    ('FBNEO ETC V',         'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 其他'),
    ('FBNEO FLY',           'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 飞行'),
    ('FBNEO FLY V',         'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 飞行'),
    ('FBNEO FTG',           'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 格斗'),
    ('FBNEO FTG hack',      'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 格斗改版'),
    ('FBNEO RAC',           'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 竞速'),
    ('FBNEO STG',           'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 射击'),
    ('FBNEO STG V',         'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 射击'),
    ('FBNEO STG hack',      'arcade',                 '/roms/arcade',        'maybe', 'FBNeo 射击改版'),
    ('MAME ACT',            'mame2003|mame2010|mame', '/roms/mame2003',      'maybe', 'MAME 动作类'),
    ('MAME ETC',            'mame2003|mame2010|mame', '/roms/mame2003',      'maybe', 'MAME 其他'),
    ('MAME FLY',            'mame2003|mame2010|mame', '/roms/mame2003',      'maybe', 'MAME 飞行'),
    ('MAME FLY V',          'mame2003|mame2010|mame', '/roms/mame2003',      'maybe', 'MAME 飞行'),
    ('MAME FTG',            'mame2003|mame2010|mame', '/roms/mame2003',      'maybe', 'MAME 格斗'),
    ('MAME FTG hack',       'mame2003|mame2010|mame', '/roms/mame2003',      'maybe', 'MAME 格斗改版'),
    ('MAME RAC',            'mame2003|mame2010|mame', '/roms/mame2003',      'maybe', 'MAME 竞速'),
    ('MAME STG',            'mame2003|mame2010|mame', '/roms/mame2003',      'maybe', 'MAME 射击'),
    ('MAME STG V',          'mame2003|mame2010|mame', '/roms/mame2003',      'maybe', 'MAME 射击'),
    # NAS 上新增的目录（ArkOS 表里没有），这台有 coleco
    ('COLECO',              'coleco|colecovision',    '/roms/coleco',        'maybe', 'Coleco 掌机'),
    ('HBMAME',              'mame2010|mame|hbmame',   '/roms/mame',          'maybe', 'HBMAME 改版街机'),
]


def _build_rocknix():
    out = []
    for nas_dir, cand, sub, conf, note in MAP_ROCKNIX_RAW:
        name = _pick(cand)
        # 子目录取候选里的实际系统名更稳（例如 snesmsu1 这种名字与目录同名）
        path = os.path.join(config.ES_ROMS, sub or name)
        out.append((nas_dir, name, path, conf, note))
    return out


def _build_darkos():
    """DarkOS：第 3 列已经是绝对路径（与 ArkOS 表同形），只挑 ES 名"""
    out = []
    for nas_dir, cand, path, conf, note in MAP_DARKOS_RAW:
        out.append((nas_dir, _pick(cand), path, conf, note))
    return out


# ------------------------------------------------------- 当前平台生效的表
def _build_map():
    if config.PLATFORM == 'rocknix':
        return _build_rocknix()
    if config.PLATFORM == 'darkos':
        return _build_darkos()
    return list(MAP_ARKOS)


def _build_unsupported():
    if config.PLATFORM == 'rocknix':
        return list(UNSUPPORTED_ROCKNIX)
    if config.PLATFORM == 'darkos':
        return list(UNSUPPORTED_DARKOS)
    return list(UNSUPPORTED_ARKOS)


MAP = _build_map()
UNSUPPORTED = _build_unsupported()


def rebuild():
    """重新解析 es_systems.cfg 并重建映射（换平台/换配置文件后调用）"""
    global MAP, UNSUPPORTED, _BY_DIR, _BY_ES
    es_names(refresh=True)
    MAP = _build_map()
    UNSUPPORTED = _build_unsupported()
    _BY_DIR = dict((m[0], m) for m in MAP)
    _BY_ES = _by_es_name()
    return MAP


_BY_DIR = dict((m[0], m) for m in MAP)


def lookup(nas_dir):
    """返回 (es_name, es_path, conf, note) 或 None（不在映射表中）"""
    m = _BY_DIR.get(nas_dir)
    if not m:
        return None
    return (m[1], m[2], m[3], m[4])


# 按 ES 名反查：一个 ES 名可能对应多个 NAS 目录（如 nes ← FC / FC hack / FC-HD）
def _by_es_name():
    d = {}
    by_dir = dict((m[0], m) for m in MAP)
    for m in MAP:
        d.setdefault(m[1], m)

    # ---- 候选别名（0.6.1 / DarkOS 实机踩坑）--------------------------------
    # 映射表里写的是 'a|b|c' 候选串，但 _pick() 只挑中**一个**放进 MAP，
    # 于是没被挑中的候选名在反查表里查不到。126 上就踩了：
    #   ('NAOMI', 'naomi|Sega Atomiswave', '/roms/naomi', ...)
    # 本机 cfg 里 naomi 与 Sega Atomiswave 两个系统名都在，_pick 挑了排在前面的
    # naomi，用户在 ES 里看到 / 念出的却是「Sega Atomiswave」—— resolve() 直接返回
    # None，表现为「明明系统在那儿却搜不到」。
    # 所以把「本机 cfg 里确实存在、但没被挑中的候选名」也登记成别名。
    # 判据是 c in names（**必须真在 cfg 里**），否则 126 上 psp 会被误当成
    # PSP 的别名，而这台的 psp 目录并不存在。
    raw = {'rocknix': MAP_ROCKNIX_RAW, 'darkos': MAP_DARKOS_RAW}.get(config.PLATFORM)
    if raw:
        names = es_names()
        if names:
            for row in raw:
                nas_dir, cand = row[0], row[1]
                m = by_dir.get(nas_dir)
                if not m:
                    continue
                for c in (cand or '').split('|'):
                    c = c.strip()
                    if c and c != m[1] and c in names:
                        d.setdefault(c, m)
    return d


_BY_ES = _by_es_name()


def resolve(name):
    """宽容解析用户输入：先按 NAS 目录名，再按 ES 系统名（大小写不敏感）

    为什么需要（0.4.1 实机踩坑）：
      用户在掌机上看到的是 **ES 名**（`gba`），但内部一律按 **NAS 目录名**（`GBA`）
      拼路径。两者经常不同（`nes` ← `FC`、`psp` ← `PSP`）。
      让用户记两套名字不现实，所以这里两种都认。

    返回 (nas_dir, es_name, es_path, conf, note) 或 None。
    """
    m = _BY_DIR.get(name)
    if m:
        return (m[0],) + m[1:]
    # 大小写不敏感的 NAS 目录名
    low = name.lower()
    for k, v in _BY_DIR.items():
        if k.lower() == low:
            return (k,) + v[1:]
    # 按 ES 名
    v = _BY_ES.get(low)
    if v:
        return tuple(v)
    for k, v in _BY_ES.items():
        if k.lower() == low:
            return tuple(v)
    return None


def es_path_index(cfg_path=None):
    """返回 {es_name: [path, ...]}，用于校验映射表指向的路径真实存在"""
    d = {}
    for name, path, _ in load_es_systems(cfg_path):
        d.setdefault(name, []).append(path)
    return d


def validate(cfg_path=None):
    """校验映射表：ES 名是否存在、路径是否与配置一致。返回问题列表"""
    problems = []
    idx = es_path_index(cfg_path)
    if not idx:
        return [('全局', '无法解析 es_systems.cfg，跳过校验')]
    root = config.ES_ROMS.rstrip('/')
    for m in MAP:
        nas_dir, es_name, es_path, conf, note = m
        if es_name not in idx:
            problems.append((nas_dir, 'ES 系统 %s 不存在' % es_name))
            continue
        # 路径比对：ArkOS 表写的是绝对 /roms/...，ROCKNIX 表由 ES_ROMS 拼出来，
        # 两者都要求「配置里有这个路径」；但两族固件都有「同名系统挂别处」的
        # 情况（colecovision -> /storage/roms/coleco），所以只要**名字在**且
        # 配置里的路径与 ES_ROMS 同根，就不算错。
        cands = idx[es_name]
        if es_path in cands:
            continue
        if any(c.startswith(root + '/') or c == root for c in cands):
            continue
        problems.append((nas_dir, '路径不符: 表中 %s, 配置中 %s'
                         % (es_path, '/'.join(cands))))
    return problems


def norm_key(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())
