# -*- coding: utf-8 -*-
"""NAS 目录名 -> EmulationStation 系统 的映射表

置信度 conf:
  'ok'    已确认（系统名与 ES 配置精确对应）
  'maybe' 推测（街机 romset 归属、或 ES 侧有多种可能），界面会打 ⚠
  None    无对应 ES 系统，不参与安装

映射依据（2026-10-11 掌机实测）：
  - ES 侧 128 个系统，见 /etc/emulationstation/es_systems.cfg
  - NAS 侧 85 个目录，其中 78 个有 metadata.pegasus.txt，合计 11,000 条游戏
  - 系统名两侧各叫各的，不能用字符串归一化代替（例：PSP 的 ES 名是 ppsspp）
"""
import re
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------- 映射表
MAP = [
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

# 明确无对应 ES 系统的 NAS 目录（有 metadata 也不参与安装）
UNSUPPORTED = [
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

_BY_DIR = {m[0]: m for m in MAP}

# es_systems.cfg 不是合法 XML：options 系统的 <command> 里有裸 &（"2>&1"），
# 实测第 2588 行，会让 ElementTree 直接抛 not well-formed。
# 解析前先把「非实体」的 & 转义掉。
_AMP_RE = re.compile(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)')


def lookup(nas_dir):
    """返回 (es_name, es_path, conf, note) 或 None（不在映射表中）"""
    m = _BY_DIR.get(nas_dir)
    if not m:
        return None
    return (m[1], m[2], m[3], m[4])


# 按 ES 名反查：一个 ES 名可能对应多个 NAS 目录（如 nes ← FC / FC hack / FC-HD）
def _by_es_name():
    d = {}
    for m in MAP:
        d.setdefault(m[1], m)
    return d


_BY_ES = _by_es_name()


def resolve(name):
    """宽容解析用户输入：先按 NAS 目录名，再按 ES 系统名（大小写不敏感）

    为什么需要（0.4.1 实机踩坑）：
      用户在掌机上看到的是 **ES 名**（`gba`），但内部一律按 **NAS 目录名**（`GBA`）
      拼路径。两者经常不同（`nes` ← `FC`、`ppsspp` ← `PSP`）。
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


def load_es_systems(cfg_path=None):
    """解析 es_systems.cfg，返回 [(name, path, extensions_set)]

    注意：该文件含裸 &，需先做转义修复才能喂给 ElementTree。
    """
    import os
    p = cfg_path or __import__('romstore.config', fromlist=['x']).ES_SYSTEMS_CFG
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
    for m in MAP:
        nas_dir, es_name, es_path, conf, note = m
        if es_name not in idx:
            problems.append((nas_dir, 'ES 系统 %s 不存在' % es_name))
        elif es_path not in idx[es_name]:
            problems.append((nas_dir, '路径不符: 表中 %s, 配置中 %s'
                             % (es_path, '/'.join(idx[es_name]))))
    return problems


def norm_key(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())
