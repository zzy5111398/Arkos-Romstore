# -*- coding: utf-8 -*-
"""全局配置与路径常量（Python 3.7 stdlib-only）"""
import os

# 环境变量可覆盖，便于在 PC 上做离线测试
NAS_ROOT = os.environ.get('ROMSTORE_NAS', '/mnt/Roms')
ES_ROMS = os.environ.get('ROMSTORE_ROMS', '/roms')
STORE_DIR = os.environ.get('ROMSTORE_HOME', '/home/ark/.romstore')
ES_SYSTEMS_CFG = os.environ.get('ROMSTORE_ES_CFG',
                                '/etc/emulationstation/es_systems.cfg')
# EmulationStation 的 options 系统目录（es_systems.cfg 里 <path>/opt/system/</path>）。
# 入口脚本 RomStore.sh 放这里，gamelist 条目写 ./RomStore.sh —— 两者必须一致。
ES_OPTIONS_DIR = os.environ.get('ROMSTORE_ES_OPTIONS', '/opt/system')

DB_PATH = os.path.join(STORE_DIR, 'index.db')
LOG_DIR = os.path.join(STORE_DIR, 'log')
CACHE_DIR = os.path.join(STORE_DIR, 'cache')
STATE_PATH = os.path.join(STORE_DIR, 'state.json')

FONT_CJK = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
FONT_CJK_ALT = '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc'

METADATA_NAME = 'metadata.pegasus.txt'
MOUNT_POINT = NAS_ROOT

# 回收站（卸载时可撤销）
TRASH_DIR = os.path.join(ES_ROMS, '.romstore-trash')

# 保留空间下限：低于此值拒绝安装
MIN_FREE_BYTES = 64 * 1024 * 1024

# 媒体只读（P1 只送 ROM，封面/logo 从 NAS 现取）
MEDIA_KEYS = ('box_front', 'logo', 'video')


def ensure_dirs():
    for d in (STORE_DIR, LOG_DIR, CACHE_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
