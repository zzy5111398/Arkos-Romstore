# -*- coding: utf-8 -*-
"""Pegasus metadata.pegasus.txt 解析器

实测格式（2026-10-11）：
    collection: FC                      <- 文件头块（无 game: 前缀）
    sort-by: 022
    extensions: nes, 7z, zip
    launch: "{env.appdir}\\RetroArch\\retroarch.exe" -L "...\\nestopia_libretro.dll" "{file.path}"
                                        <- 空行分隔
    game: 超级马里奥1                    <- 游戏块开始
    file: 超级马里奥1.zip                <- 单文件
    sort-by: 001
    description: ...                    <- 占全文 80-91%
                                        <- 空行分隔
    game: 露娜2 永恒之蓝
    files:                              <- 多碟（SS 独占 100 个）
      009/Lunar 2 ... (DISC 1).chd
      009/Lunar 2 ... (DISC 2).chd
    assets.box_front: media/009/boxFront.png
    assets.logo: media/009/logo.png

必须处理的三件事：
  1. 文件头块（collection:/extensions:/launch:）在第一个 game: 之前，不能被当成游戏
  2. files: 后的缩进行是继续行，不是新字段
  3. description 里可能出现任意字符，一旦进入就忽略到块结束
"""
import re
import os

_CORE_RE = re.compile(r'([A-Za-z0-9_\-]+)_libretro\.(?:so|dll)')

# 已知字段前缀（用于把「缩进行」与「新字段」区分开）
_FIELD_PREFIXES = (
    'game:', 'file:', 'files:', 'sort-by:', 'developer:', 'publisher:',
    'genre:', 'players:', 'rating:', 'release:', 'playcount:', 'playtime:',
    'description:', 'launch:', 'collection:', 'extensions:', 'assets.',
)


class Game(object):
    __slots__ = ('title', 'rom', 'roms', 'sort_by', 'core', 'assets',
                 'developer', 'genre', 'players')

    def __init__(self, title):
        self.title = title
        self.rom = None          # 单文件相对路径
        self.roms = []           # 多碟相对路径列表
        self.sort_by = ''
        self.core = None         # RetroArch core 名（不含 _libretro 后缀）
        self.assets = {}         # box_front / logo / video -> 相对路径
        self.developer = ''
        self.genre = ''
        self.players = ''

    @property
    def is_multi(self):
        return bool(self.roms)

    def all_roms(self):
        """返回全部 ROM 相对路径（单碟 1 个，多碟 N 个）"""
        if self.roms:
            return list(self.roms)
        return [self.rom] if self.rom else []

    def media_dir(self):
        """推导 media/ 子目录名（实测命中率 100%）

        优先级：
          1. assets.box_front 存在        -> 取其所在目录名（media/009/boxFront.png -> 009）
          2. 多碟或 rom 在子目录          -> 取第一段目录名
          3. 否则                         -> basename 去扩展名
        """
        bf = self.assets.get('box_front')
        if bf:
            parts = bf.replace('\\', '/').split('/')
            if len(parts) >= 2:
                return parts[-2]
        rp = self.rom
        if self.roms:
            rp = self.roms[0]
        if rp:
            rp = rp.replace('\\', '/')
            if '/' in rp:
                return rp.split('/')[0]
            return os.path.splitext(os.path.basename(rp))[0]
        return None

    def has_boxart(self):
        return bool(self.assets.get('box_front'))


def parse(path):
    """解析一个 metadata.pegasus.txt，返回 (header_dict, [Game, ...])"""
    header = {}
    games = []
    cur = None
    in_files = False
    in_desc = False

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for raw in f:
            line = raw.rstrip('\r\n')

            if line.startswith('game:'):
                if cur:
                    games.append(cur)
                cur = Game(line[5:].strip())
                in_files = in_desc = False
                continue

            if cur is None:
                # 文件头块
                if ':' in line and not line.startswith((' ', '\t')):
                    k, v = line.split(':', 1)
                    header[k.strip()] = v.strip()
                continue

            # ---- 游戏块内 ----
            if in_desc:
                if line.strip() == '':
                    in_desc = False
                continue

            if in_files:
                if line.strip() == '':
                    in_files = False
                    continue
                if line[0] in (' ', '\t') and not line.lstrip().startswith(_FIELD_PREFIXES):
                    v = line.strip()
                    if v:
                        cur.roms.append(v)
                    continue
                # 落到这里说明是正常字段，继续往下走
                in_files = False

            if line.strip() == '':
                continue

            if line.startswith('file:'):
                cur.rom = line[5:].strip()
            elif line.startswith('files:'):
                in_files = True
            elif line.startswith('sort-by:'):
                cur.sort_by = line[8:].strip()
            elif line.startswith('developer:'):
                cur.developer = line[10:].strip()
            elif line.startswith('genre:'):
                cur.genre = line[6:].strip()
            elif line.startswith('players:'):
                cur.players = line[8:].strip()
            elif line.startswith('assets.'):
                body = line[7:]
                if ':' in body:
                    k, v = body.split(':', 1)
                    k = k.strip()
                    if k in ('box_front', 'logo', 'video', 'box_back', 'screenshot', 'marquee', 'cartridge'):
                        cur.assets[k] = v.strip()
            elif line.startswith('description:'):
                in_desc = True
            elif line.startswith('launch:'):
                m = _CORE_RE.search(line)
                if m:
                    cur.core = m.group(1)

    if cur:
        games.append(cur)
    return header, games


def parse_descriptions(path, want_titles):
    """只取指定标题的 description（详情页用），避免整表入库。

    want_titles: set；返回 {title: desc}
    """
    out = {}
    if not want_titles:
        return out
    cur_title = None
    in_desc = False
    buf = []
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for raw in f:
            line = raw.rstrip('\r\n')
            if line.startswith('game:'):
                if cur_title in want_titles and buf:
                    out[cur_title] = '\n'.join(buf)
                cur_title = line[5:].strip()
                buf = []
                in_desc = False
                continue
            if cur_title is None:
                continue
            if in_desc:
                if line.strip() == '':
                    in_desc = False
                else:
                    buf.append(line.strip())
                continue
            if line.startswith('description:'):
                in_desc = True
                v = line[12:].strip()
                if v:
                    buf.append(v)
    if cur_title in want_titles and buf:
        out[cur_title] = '\n'.join(buf)
    return out
