# -*- coding: utf-8 -*-
"""ROM 商店客户端（局域网 SMB 版，双平台：ArkOS / AURKNIX-ROCKNIX）

版本历史见包内 CHANGELOG.md
  P1 建店：索引 / 安装 / SDL2 中文界面 / ES 入口
  P2 换源：source.json 唯一真相 + 掌机内改商店源 + 手柄软键盘
  P3 打包：install.sh / uninstall.sh 可分发
  P4 双平台：见 romstore/platform.py —— 同一份代码跑 ArkOS 与 AURKNIX
"""
import os as _os

# 保证 platform.json 一定能被找到：本包的上一级目录就是 STORE_DIR。
# （RomStore.sh 会先 cd 进 STORE_DIR 再跑 run.py，但 CLI 可能从别处调用；
#   显式设一次比依赖 cwd 稳。已显式设过的环境变量优先。）
_os.environ.setdefault(
    'ROMSTORE_HOME', _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

__version__ = '0.6.0'
