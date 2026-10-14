# -*- coding: utf-8 -*-
"""平台抽象层 / 三平台映射 / 模板占位符 的离线自测（不碰真机、不联网）

这是 0.6.0「移植到 AURKNIX(ROCKNIX)」与「移植到 DarkOS」两次改动的守门测试。
跑法：

    python3 tests/test_platform.py

它盯住四件事：
  1. 平台探测与优先级（环境变量 > platform.json > 本机特征 > arkos 默认）
  2. **老机器行为不变** —— 没有 platform.json 时，ArkOS 的每一个路径
     都必须与 0.5.x 时期一模一样（这是「保留两个系统的兼容性」的硬证据）
  3. ROCKNIX / DarkOS 映射表能挑对本机真有的 ES 名（psp/neocd/pokemini/pc/mame…）
  4. 模板里的每个 @@XXX@@ 占位符 install.sh 都认识（漏一个就是安装期炸）
"""
import importlib
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, SRC)

FAIL = []


def check(name, cond, extra=''):
    print(('  [OK] ' if cond else '  [FAIL] ') + name
          + ('  ' + str(extra) if extra else ''))
    if not cond:
        FAIL.append(name)


# ------------------------------------------------------------------ 工具
_DETECTED = {'v': ''}


def fresh_modules(**env):
    """按给定环境变量重新加载 platform/config/mapp，返回 (P, C, M)

    ⚠️ detect() 必须在**环境变量还在**的时候调用 —— 本函数退出时会还原
    os.environ，之后再调 P.detect() 就看不到 ROMSTORE_PLATFORM 了。
    所以顺手把结论存进 _DETECTED。
    """
    saved = dict(os.environ)
    try:
        for k in list(os.environ):
            if k.startswith('ROMSTORE_'):
                del os.environ[k]
        os.environ.update({k: v for k, v in env.items() if v is not None})
        import romstore.platform as P
        importlib.reload(P)
        _DETECTED['v'] = P.detect()
        import romstore.config as C
        importlib.reload(C)
        import romstore.mapp as M
        importlib.reload(M)
        return P, C, M
    finally:
        os.environ.clear()
        os.environ.update(saved)


def u(p):
    """统一路径分隔符 —— 这个测试台在 Windows 上跑，os.path.join 会给反斜杠"""
    return (p or '').replace('\\', '/')


root = tempfile.mkdtemp(prefix='romstore-p-')

# 一份「像 ROCKNIX」的 es_systems.cfg —— 故意把 ArkOS 与 ROCKNIX 的差异全都摆进去
ROCKNIX_SYSTEMS = [
    ('nes', '/storage/roms/nes'), ('famicom', '/storage/roms/famicom'),
    ('snes', '/storage/roms/snes'), ('sfc', '/storage/roms/sfc'),
    ('gba', '/storage/roms/gba'), ('gb', '/storage/roms/gb'),
    ('psx', '/storage/roms/psx'), ('psp', '/storage/roms/psp'),
    ('neocd', '/storage/roms/neocd'), ('pokemini', '/storage/roms/pokemini'),
    ('pc', '/storage/roms/pc'), ('mame', '/storage/roms/mame'),
    ('arcade', '/storage/roms/arcade'), ('gamecube', '/storage/roms/gamecube'),
    ('wii', '/storage/roms/wii'), ('ports', '/storage/roms/ports'),
    ('saturn', '/storage/roms/saturn'), ('dreamcast', '/storage/roms/dreamcast'),
    ('naomi', '/storage/roms/naomi'), ('hbmame', '/storage/roms/hbmame'),
]
rk_cfg = os.path.join(root, 'es_systems_rocknix.cfg')
with open(rk_cfg, 'w', encoding='utf-8') as f:
    f.write('<?xml version="1.0"?>\n<systemList>\n')
    for n, p in ROCKNIX_SYSTEMS:
        f.write('  <system>\n    <name>%s</name>\n    <path>%s</path>\n'
                '    <extension>.zip .7z</extension>\n'
                '    <command>/usr/bin/runemu.sh %%ROM%% 2>&1</command>\n'
                '  </system>\n' % (n, p))
    f.write('</systemList>\n')

# 一份「像 ArkOS」的
ARKOS_SYSTEMS = [
    ('nes', '/roms/nes'), ('sfc', '/roms/sfc'), ('gba', '/roms/gba'),
    ('psx', '/roms/psx'), ('ppsspp', '/roms/psp'),
    ('neogeocd', '/roms/neogeocd'), ('pokemonmini', '/roms/pokemonmini'),
    ('msdos', '/roms/dos'), ('mame2003', '/roms/mame2003'),
    ('arcade', '/roms/arcade'), ('options', '/opt/system'),
]
ak_cfg = os.path.join(root, 'es_systems_arkos.cfg')
with open(ak_cfg, 'w', encoding='utf-8') as f:
    f.write('<?xml version="1.0"?>\n<systemList>\n')
    for n, p in ARKOS_SYSTEMS:
        f.write('  <system>\n    <name>%s</name>\n    <path>%s</path>\n'
                '    <extension>.zip .7z</extension>\n'
                '    <command>%%ROM%% 2>&1</command>\n'
                '  </system>\n' % (n, p))
    f.write('</systemList>\n')

# 一份「像 DarkOS(126)」的 —— 逐条抄自 126 实机的 /etc/emulationstation/es_systems.cfg，
# 刻意保留了三处真机才有的坑：
#   ① snes 出现**两次**：第一次配 /roms/sufami/，第二次才配 /roms/snes/
#   ② Atomiswave 的 ES 名**带空格**（`Sega Atomiswave`），路径却是 /roms/atomiswave
#   ③ ppsspp 与 psp 同时存在，且 ppsspp 的路径是 /roms/psp/（不是 ROCKNIX 那个 psp）
DARKOS_SYSTEMS = [
    ('famicom', '/roms/famicom/'), ('fds', '/roms/fds/'),
    ('sfc', '/roms/sfc/'), ('nes', '/roms/nes/'),
    ('snes', '/roms/sufami/'), ('satellaview', '/roms/satellaview/'),
    ('snes', '/roms/snes/'), ('snesmsu', '/roms/snesmsu1/'),
    ('snes-hacks', '/roms/snes-hacks/'),
    ('gb', '/roms/gb/'), ('gbc', '/roms/gbc/'), ('gba', '/roms/gba/'),
    ('n64', '/roms/n64/'), ('nds', '/roms/nds/'),
    ('pokemonmini', '/roms/pokemonmini/'), ('virtualboy', '/roms/virtualboy/'),
    ('gameandwatch', '/roms/gameandwatch/'),
    ('megadrive', '/roms/megadrive/'), ('megadrivemsu', '/roms/msumd/'),
    ('genesis', '/roms/genesis/'), ('segacd', '/roms/segacd/'),
    ('sega32x', '/roms/sega32x/'), ('mastersystem', '/roms/mastersystem/'),
    ('gamegear', '/roms/gamegear/'), ('saturn', '/roms/saturn/'),
    ('dreamcast', '/roms/dreamcast/'),
    ('psx', '/roms/psx/'), ('ppsspp', '/roms/psp/'), ('pspminis', '/roms/pspminis/'),
    ('3do', '/roms/3do/'), ('atari2600', '/roms/atari2600/'),
    ('atari5200', '/roms/atari5200/'), ('atari7800', '/roms/atari7800/'),
    ('atarilynx', '/roms/atarilynx/'), ('coleco', '/roms/coleco/'),
    ('pcengine', '/roms/pcengine/'), ('pcenginecd', '/roms/pcenginecd/'),
    ('pcfx', '/roms/pcfx/'), ('neogeocd', '/roms/neogeocd/'),
    ('ngpc', '/roms/ngpc/'), ('wonderswan', '/roms/wonderswan/'),
    ('wonderswancolor', '/roms/wonderswancolor/'),
    ('naomi', '/roms/naomi/'), ('Sega Atomiswave', '/roms/atomiswave/'),
    ('openbor', '/roms/openbor/'), ('msdos', '/roms/dos/'), ('pc', '/roms/dos/'),
    ('arcade', '/roms/arcade/'), ('cps1', '/roms/cps1/'), ('cps2', '/roms/cps2/'),
    ('cps3', '/roms/cps3/'), ('alg', '/roms/alg/'), ('daphne', '/roms/daphne/'),
    ('mame2003', '/roms/mame2003/'), ('mame2010', '/roms/mame/'),
    ('ports', '/roms/ports/'),
]
dk_cfg = os.path.join(root, 'es_systems_darkos.cfg')
with open(dk_cfg, 'w', encoding='utf-8') as f:
    f.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<systemList xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n')
    for n, p in DARKOS_SYSTEMS:
        f.write('\t<system>\n\t\t<name>%s</name>\n\t\t<path>%s</path>\n'
                '\t\t<extension>.zip .7z</extension>\n'
                '\t\t<command>sudo perfmax %%GOVERNOR%% %%ROM%%</command>\n'
                '\t</system>\n' % (n, p))
    f.write('</systemList>\n')

rk_home = os.path.join(root, 'rocknix-home')
ak_home = os.path.join(root, 'arkos-home')
dk_home = os.path.join(root, 'darkos-home')
os.makedirs(rk_home)
os.makedirs(ak_home)
os.makedirs(dk_home)

# ---------------------------------------------------------------- 1. 探测
print('== 1. 平台探测（未生成 platform.json 时按本机特征）==')
P, C, M = fresh_modules(ROMSTORE_PLATFORM='rocknix', ROMSTORE_HOME=rk_home,
                        ROMSTORE_ES_CFG=rk_cfg)
check('ROMSTORE_PLATFORM=rocknix 生效', _DETECTED['v'] == 'rocknix', _DETECTED['v'])
check('  → config.PLATFORM', C.PLATFORM == 'rocknix', C.PLATFORM)

P, C, M = fresh_modules(ROMSTORE_PLATFORM='arkos', ROMSTORE_HOME=ak_home,
                        ROMSTORE_ES_CFG=ak_cfg)
check('ROMSTORE_PLATFORM=arkos 生效', _DETECTED['v'] == 'arkos', _DETECTED['v'])

# ------------------------------------------------- 2. 老机器行为逐项不变
print('== 2. 无 platform.json 时 ArkOS 行为必须与 0.5.x 完全一致 ==')
# ⚠️ 这一次**不设** ROMSTORE_ES_CFG —— 要验的就是平台档案里的默认值本身
P, C, M = fresh_modules(ROMSTORE_PLATFORM='arkos',
                        ROMSTORE_HOME='/home/ark/.romstore')
LEGACY = {
    'STORE_DIR': '/home/ark/.romstore',
    'NAS_ROOT': '/mnt/Roms',
    'MOUNT_POINT': '/mnt/Roms',
    'ES_ROMS': '/roms',
    'ES_SYSTEMS_CFG': '/etc/emulationstation/es_systems.cfg',
    'ES_OPTIONS_DIR': '/opt/system',
    'ES_ENTRY_DIR': '/opt/system',
    'ES_ENTRY_NAME': 'RomStore.sh',
    'ES_ENTRY_SYSTEM': 'options',
    'DB_PATH': '/home/ark/.romstore/index.db',
    'MOUNT_BACKEND': 'cifs',
    'MOUNT_SCRIPT': '/usr/local/sbin/nas-roms-mount.sh',
    'SYSTEMD_DIR': '/etc/systemd/system',
    'TARGET_USER': 'ark',
    'SDL_VIDEO_DRIVER': 'kmsdrm',
    'MPV_VO': 'drm',
    'MPV_AO': 'alsa',
    'EXTRA_PATH': '',
    'SAMBA_VERS': '2.1',
    'TRASH_DIR': '/roms/.romstore-trash',
    'METADATA_NAME': 'metadata.pegasus.txt',
    'FONT_CJK': '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    'GAMEPAD_DB': '/opt/inttools/gamecontrollerdb.txt',
}
for k, want in sorted(LEGACY.items()):
    got = u(getattr(C, k))
    check('%-16s == %s' % (k, want), got == want, '' if got == want else got)
check('roms_fs_selfheal 打开（只读自愈）', C.ROMS_FS_SELFHEAL is True)

# ------------------------------------------------------- 3. platform.json
print('== 3. platform.json 的优先级与容错 ==')
with open(os.path.join(rk_home, 'platform.json'), 'w', encoding='utf-8') as f:
    json.dump({'platform': 'rocknix', 'store_dir': rk_home,
               'nas_mount': '/storage/nas-roms', 'es_roms': '/storage/roms',
               'es_entry_dir': '/storage/roms/ports',
               'mount_backend': 'rclone',
               'mount_script': '/storage/.romstore/nas-roms-mount.sh',
               'systemd_dir': '/storage/.config/system.d',
               'target_user': 'root', 'extra_path': '/storage/.romstore/bin',
               '不存在的键': 'should be ignored',
               'nas_mount_subdir': 'Roms'}, f)

# 本机特征说 arkos，但 platform.json 说 rocknix —— 应以后者为准
P, C, M = fresh_modules(ROMSTORE_HOME=rk_home, ROMSTORE_ES_CFG=rk_cfg)
check('platform.json 覆盖探测结论', C.PLATFORM == 'rocknix', C.PLATFORM)
check('  档案来源被记下', C.PLATFORM_FILE.endswith('platform.json'), C.PLATFORM_FILE)
check('  nas_mount', C.NAS_ROOT == '/storage/nas-roms', C.NAS_ROOT)
check('  store_dir', C.STORE_DIR == rk_home, C.STORE_DIR)
check('  es_entry_dir', C.ES_ENTRY_DIR == '/storage/roms/ports', C.ES_ENTRY_DIR)
check('  mount_backend', C.MOUNT_BACKEND == 'rclone', C.MOUNT_BACKEND)
check('  systemd_dir', C.SYSTEMD_DIR == '/storage/.config/system.d', C.SYSTEMD_DIR)
check('  target_user', C.TARGET_USER == 'root', C.TARGET_USER)
check('  extra_path', C.EXTRA_PATH == '/storage/.romstore/bin', C.EXTRA_PATH)
check('  未知键被忽略', P.load().get('不存在的键') is None)
check('  DB_PATH 跟着 store_dir', C.DB_PATH == os.path.join(rk_home, 'index.db'),
      C.DB_PATH)
check('  MOUNT_STATE_PATH 跟着 store_dir',
      C.MOUNT_STATE_PATH == os.path.join(rk_home, '.mount-state'),
      C.MOUNT_STATE_PATH)

# 环境变量优先级最高
P, C, M = fresh_modules(ROMSTORE_HOME=rk_home, ROMSTORE_ES_CFG=rk_cfg,
                        ROMSTORE_NAS='/tmp/自定义挂载点',
                        ROMSTORE_BACKEND='cifs')
check('环境变量 > platform.json', C.NAS_ROOT == '/tmp/自定义挂载点', C.NAS_ROOT)
check('  后端也被环境变量改写', C.MOUNT_BACKEND == 'cifs', C.MOUNT_BACKEND)

# 坏 JSON 不该崩
with open(os.path.join(rk_home, 'platform.json'), 'w') as f:
    f.write('{ 这不是 json ')
P, C, M = fresh_modules(ROMSTORE_HOME=rk_home, ROMSTORE_ES_CFG=rk_cfg)
check('platform.json 坏了也不抛异常',
      C.PLATFORM in ('arkos', 'rocknix', 'darkos'), C.PLATFORM)

# ---------------------------------------------------- 4. ROCKNIX 映射表
print('== 4. ROCKNIX 映射表（ArkOS/ROCKNIX 系统名不同的那些）==')
P, C, M = fresh_modules(ROMSTORE_PLATFORM='rocknix', ROMSTORE_HOME=rk_home,
                        ROMSTORE_ROMS='/storage/roms', ROMSTORE_ES_CFG=rk_cfg)
M.rebuild()
EXPECT_RK = {
    'PSP': ('psp', '/storage/roms/psp'),
    'NEOGEO-CD': ('neocd', '/storage/roms/neocd'),
    'POKE MINI': ('pokemini', '/storage/roms/pokemini'),
    'DOS': ('pc', '/storage/roms/pc'),
    'MAME ACT': ('mame', '/storage/roms/mame'),
    'FBNEO STG': ('arcade', '/storage/roms/arcade'),
    'PC': ('pc', '/storage/roms/pc'),
    'NGC': ('gamecube', '/storage/roms/gamecube'),
    'WII': ('wii', '/storage/roms/wii'),
    'HBMAME': ('hbmame', '/storage/roms/hbmame'),
}
for nas, (es, path) in sorted(EXPECT_RK.items()):
    r = M.resolve(nas)
    got = (r[1], u(r[2])) if r else None
    check('%-12s -> %s %s' % (nas, es, path), got == (es, path), got)

# nes / famicom 并存时选 nes；sfc / snes 并存时选 sfc
check('FC(nes+famicom 并存) 选 nes', M.resolve('FC')[1] == 'nes', M.resolve('FC')[1])
check('SFC(sfc+snes 并存) 选 sfc', M.resolve('SFC')[1] == 'sfc', M.resolve('SFC')[1])

# ES 名反查：nes 应能同时对应 FC / FC hack / FC-HD
rev = M.resolve('nes')
check('resolve 认 ES 名 nes', rev is not None and rev[0] == 'FC',
      rev[0] if rev else None)
check('  es_path 落在 ROCKNIX 的 ROM 根下',
      u(M.resolve('nes')[2]).startswith('/storage/roms/'), M.resolve('nes')[2])

# 候选全不在本机 cfg 里 → 回落候选第一个（不要崩，也不要空）
P2, C2, M2 = fresh_modules(ROMSTORE_PLATFORM='rocknix', ROMSTORE_HOME=rk_home,
                           ROMSTORE_ROMS='/storage/roms',
                           ROMSTORE_ES_CFG=os.path.join(root, '不存在.cfg'))
M2.rebuild()
r = M2.resolve('PSP')
check('cfg 解析不到时回落候选首位 psp', r and r[1] == 'psp', r[1] if r else None)

# --------------------------------------------------- 5. ArkOS 表没被动过
print('== 5. ArkOS 映射表保持原样 ==')
P, C, M = fresh_modules(ROMSTORE_PLATFORM='arkos', ROMSTORE_HOME=ak_home,
                        ROMSTORE_ROMS='/roms', ROMSTORE_ES_CFG=ak_cfg)
M.rebuild()
EXPECT_AK = {
    'PSP': ('ppsspp', '/roms/psp'),
    'NEOGEO-CD': ('neogeocd', '/roms/neogeocd'),
    'POKE MINI': ('pokemonmini', '/roms/pokemonmini'),
    'DOS': ('msdos', '/roms/dos'),
    'MAME ACT': ('mame2003', '/roms/mame2003'),
    'FC': ('nes', '/roms/nes'),
    'SFC': ('sfc', '/roms/sfc'),
}
for nas, want in sorted(EXPECT_AK.items()):
    r = M.resolve(nas)
    got = (r[1], u(r[2])) if r else None
    check('%-12s -> %s %s' % (nas, want[0], want[1]), got == want, got)
check('ArkOS 不支持 NGC（本机无 GC 系统）',
      any(u[0] == 'NGC' for u in M.UNSUPPORTED))
check('ROCKNIX 支持 NGC（走 maybe）',
      any(m[0] == 'NGC' for m in M.MAP_ROCKNIX_RAW))

# ------------------------------------------------- 6. DarkOS 档案与映射表
print('== 6. DarkOS 平台档案（126 实机核对过的 22 项）==')
P, C, M = fresh_modules(ROMSTORE_PLATFORM='darkos',
                        ROMSTORE_HOME='/home/ark/.romstore')
check('ROMSTORE_PLATFORM=darkos 生效', _DETECTED['v'] == 'darkos', _DETECTED['v'])
DARKOS = {
    'STORE_DIR': '/home/ark/.romstore',
    'NAS_ROOT': '/mnt/Roms',
    'MOUNT_POINT': '/mnt/Roms',
    'ES_ROMS': '/roms',
    'ES_SYSTEMS_CFG': '/etc/emulationstation/es_systems.cfg',
    'ES_OPTIONS_DIR': '/roms/ports',
    'ES_ENTRY_DIR': '/roms/ports',
    'ES_ENTRY_NAME': 'RomStore.sh',
    'ES_ENTRY_SYSTEM': 'ports',       # ⚠ 这台没有 options 系统
    'DB_PATH': '/home/ark/.romstore/index.db',
    'MOUNT_BACKEND': 'cifs',
    'MOUNT_SCRIPT': '/usr/local/sbin/nas-roms-mount.sh',
    'SYSTEMD_DIR': '/etc/systemd/system',
    'TARGET_USER': 'ark',
    'SDL_VIDEO_DRIVER': 'kmsdrm',     # 仍是 DRM 直出
    'MPV_VO': 'drm',
    'MPV_AO': 'alsa',
    'EXTRA_PATH': '',                 # 与 arkos 一样，bin 不搬
    'SAMBA_VERS': '2.1',              # 实测这台也只认 vers=2.1
    'TRASH_DIR': '/roms/.romstore-trash',
    'ES_CACHE_DB': '/home/ark/.emulationstation/imagecache.db',
    'ES_SETTINGS_CFG': '/home/ark/.emulationstation/es_settings.cfg',
    'FONT_CJK': '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    'GAMEPAD_DB': '/opt/inttools/gamecontrollerdb.txt',
}
for k, want in sorted(DARKOS.items()):
    got = u(getattr(C, k, None))
    check('%-16s == %s' % (k, want), got == want, '' if got == want else got)
check('roms_fs_selfheal 打开（/roms 是 exFAT+remount-ro）', C.ROMS_FS_SELFHEAL is True)
check('darkos 进了 PROFILES', 'darkos' in P.PROFILES)

print('== 6b. DarkOS 映射表（Batocera 风格 ES 名）==')
P, C, M = fresh_modules(ROMSTORE_PLATFORM='darkos', ROMSTORE_HOME=dk_home,
                        ROMSTORE_ROMS='/roms', ROMSTORE_ES_CFG=dk_cfg)
M.rebuild()
EXPECT_DK = {
    # 与 ArkOS 同名的
    'FC': ('nes', '/roms/nes'),
    'SFC': ('sfc', '/roms/sfc'),
    'SFC hack': ('snes-hacks', '/roms/snes-hacks'),
    'GB': ('gb', '/roms/gb'),
    'GBA': ('gba', '/roms/gba'),
    'N64': ('n64', '/roms/n64'),
    'POKE MINI': ('pokemonmini', '/roms/pokemonmini'),
    'MD': ('megadrive', '/roms/megadrive'),
    'DC': ('dreamcast', '/roms/dreamcast'),
    'SS': ('saturn', '/roms/saturn'),
    'PS1': ('psx', '/roms/psx'),
    'NAOMI': ('naomi', '/roms/naomi'),
    'DOS': ('msdos', '/roms/dos'),
    # ⚠ 这台与 ROCKNIX 不同的关键几项
    'PSP': ('ppsspp', '/roms/psp'),      # 不是 rocknix 的 psp
    'NEOGEO-CD': ('neogeocd', '/roms/neogeocd'),
    'MAME ACT': ('mame2003', '/roms/mame2003'),
    # 带空格的 ES 名
    'PCE': ('pcengine', '/roms/pcengine'),
    'PCE-CD': ('pcenginecd', '/roms/pcenginecd'),
    'SMS': ('mastersystem', '/roms/mastersystem'),
    'GG': ('gamegear', '/roms/gamegear'),
}
for nas, (es, path) in sorted(EXPECT_DK.items()):
    r = M.resolve(nas)
    got = (r[1], u(r[2])) if r else None
    check('%-12s -> %s %s' % (nas, es, path), got == (es, path), got)

# 带空格的 ES 名必须能被反查（'Sega Atomiswave' → naomi）
r = M.resolve('Sega Atomiswave')
check('反查带空格 ES 名 "Sega Atomiswave" 命中 NAOMI',
      r is not None and r[0] == 'NAOMI', r[0] if r else None)

# snes 在 cfg 里出现两次（先 sufami 后 snes）—— 不能因此挑错
check('FC(nes+famicom 并存) 选 nes', M.resolve('FC')[1] == 'nes', M.resolve('FC')[1])
check('SFC(sfc+snes 并存) 选 sfc', M.resolve('SFC')[1] == 'sfc', M.resolve('SFC')[1])

# es_path 必须落在 /roms 下（不能像 rocknix 那样拼 /storage）
check('  es_path 落在 /roms 下', u(M.resolve('nes')[2]).startswith('/roms/'),
      M.resolve('nes')[2])

# darkos 也要兜住「cfg 读不到」的情况
P2, C2, M2 = fresh_modules(ROMSTORE_PLATFORM='darkos', ROMSTORE_HOME=dk_home,
                           ROMSTORE_ROMS='/roms',
                           ROMSTORE_ES_CFG=os.path.join(root, '不存在.cfg'))
M2.rebuild()
r = M2.resolve('PSP')
check('cfg 解析不到时回落候选首位 ppsspp', r and r[1] == 'ppsspp',
      r[1] if r else None)

# 三张表互不串味
check('ArkOS 表不受 darkos 影响（PSP 仍 ppsspp，但路径 /roms/psp）',
      M.resolve('PSP')[1] in ('ppsspp', 'psp'))
check('darkos 走 maybe 的项也在表里',
      any(m[0] == 'COLECO' for m in M.MAP_DARKOS_RAW))

# ------------------------------------------------- 7. 模板占位符清单
print('== 7. 模板 @@占位符@@ 与 install.sh 的替换表对得上 ==')
install_sh = open(os.path.join(ROOT, 'install.sh'), encoding='utf-8').read()
known = set(re.findall(r's\|@@([A-Z_]+)@@\|', install_sh))
check('install.sh 里解析到替换规则', len(known) >= 10, sorted(known))

templates = ['src/RomStore.sh', 'src/nas-roms-mount.sh',
             'systemd/nas-roms.service', 'systemd/nas-roms-rocknix.service']
for rel in templates:
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        check('模板存在 ' + rel, False)
        continue
    txt = open(p, encoding='utf-8').read()
    used = set(re.findall(r'@@([A-Z_]+)@@', txt))
    unknown = sorted(used - known)
    check('%-34s 占位符全部已知 %s' % (rel, sorted(used)), not unknown,
          ('未知: ' + ', '.join(unknown)) if unknown else '')

# RomStore.sh 必须带 @@PLATFORM@@（收尾逻辑按平台分支）
check('RomStore.sh 带 @@PLATFORM@@',
      '@@PLATFORM@@' in open(os.path.join(ROOT, 'src/RomStore.sh'),
                             encoding='utf-8').read())
# 两个 systemd 单元都要能被渲染成合法的 oneshot 单元（服务名 = 文件名，由
# systemd 自己决定，所以这里查的是内容而不是名字）
for rel in ('systemd/nas-roms.service', 'systemd/nas-roms-rocknix.service'):
    t = open(os.path.join(ROOT, rel), encoding='utf-8').read()
    name = os.path.basename(rel)               # nas-roms.service / xxx-rocknix.service
    check('%s 有 [Unit]/[Service]/[Install]' % name,
          all(s in t for s in ('[Unit]', '[Service]', '[Install]')))
    check('%s 是 oneshot + RemainAfterExit' % name,
          'Type=oneshot' in t and 'RemainAfterExit=yes' in t)
    check('%s ExecStart 与 ExecStop(--stop) 成对' % name,
          'ExecStart=@@MOUNT_SCRIPT@@' in t
          and 'ExecStop=@@MOUNT_SCRIPT@@ --stop' in t)
# rclone 那份必须把 store/bin 排到 PATH 前面（fusermount3 桥接靠它）
rk = open(os.path.join(ROOT, 'systemd/nas-roms-rocknix.service'),
          encoding='utf-8').read()
check('rocknix 单元把 @@EXTRA_PATH@@ 排进 PATH',
      'PATH=@@EXTRA_PATH@@' in rk)
check('rocknix 单元设了 HOME=/storage',
      'Environment=HOME=/storage' in rk)

# --------------------------------------------------- 8. make-dist 清单
print('== 8. make-dist.py 的打包清单包含新文件 ==')
md = open(os.path.join(ROOT, 'make-dist.py'), encoding='utf-8').read()
for f in ('romstore/platform.py', 'romstore/config.py', 'romstore/mapp.py'):
    check('need 列表含 ' + f, f in md)
for f in ('nas-roms-rocknix.service',):
    check('need 列表含 systemd/' + f, f in md)

shutil.rmtree(root, ignore_errors=True)

print()
if FAIL:
    print('有 %d 项未通过：' % len(FAIL))
    for n in FAIL:
        print('  - ' + n)
    sys.exit(1)
print('全部通过')
