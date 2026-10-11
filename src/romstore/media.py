# -*- coding: utf-8 -*-
"""刮削素材（封面 / logo）的路径推导与复制

背景：P1 只送 ROM，装完的游戏在 EmulationStation 里没图。本模块补上。

## 实测规律（2026-10-11，在 r36s 上核对过）

NAS 侧（Pegasus 约定）：

    /mnt/Roms/FC/1943.zip                       ← ROM
    /mnt/Roms/FC/media/1943/boxFront.jpg        ← 封面
    /mnt/Roms/FC/media/1943/logo.png            ← 标题 logo
    /mnt/Roms/FC/media/1943/video.mp4           ← 预览视频

**media 目录名 == ROM 文件名去掉扩展名**（`1943.zip` → `media/1943/`）。
这条关系让素材路径**可以直接推导**，不必枚举 media 目录
——文档实测枚举 28,000 个 media 子目录要 **24.8 秒**，推导是 0 秒。

掌机侧（EmulationStation）—— **有两套布局并存，实测于 2026-10-11**：

    A. 散文件式（nes 用 img/，928 条全是这样）
       /roms/nes/1943.zip
       /roms/nes/img/1943.png
       gamelist.xml: <image>./img/1943.png</image>

    B. 每游戏一子目录式（gba 462 条、psp 全这样）
       /roms/gba/黄金太阳.zip
       /roms/gba/media/黄金太阳/boxfront.png
       gamelist.xml: <image>./media/黄金太阳/boxfront.png</image>

**这是本模块最容易出错的地方**：只看目录名（img/media）不够，
必须同时看**布局**。GBA 里 462 个子目录 / 0 个散文件，格式高度统一；
而 nes 的 img/ 里 0 个子目录 / 928 个散文件。所以要先探测、再按同款写，
否则新装的 GBA 游戏会把封面写成 `media/黄金太阳.jpg`（散文件），
既跟同系统的 462 个游戏长得不一样，还可能与已存在的同名**目录**冲突。

两条关键事实：

1. **ES 读的是 gamelist 里 `<image>` 的显式路径**，不是按约定推导。
   所以我们写 `<image>./img/1943.png</image>` + 把文件放到 `img/1943.png`
   就一定生效 —— 不需要猜掌机的目录约定。
2. **掌机各系统的目录名与布局都不统一**（实测：`nes` 用 `img/` + 散文件、
   `gba`/`psp` 用 `media/` + 每游戏子目录）。硬编码会跟系统既有习惯打架，
   所以 `pick_media_dir()` 优先**沿用该系统既有的写法**，探测不到才用默认。

## 为什么不做视频

`video.mp4` 一张 2 MB，而掌机 SD 写速只有 4.8 MB/s —— 下 20 个游戏就多花
一分半，还会把本就不多的 `/roms` 空间（剩 13 G）吃掉。本模块**只做图片**（118–400 KB），
视频留给后续版本按需选装。
"""
import os
import re

# 封面候选文件名（**大小写不敏感**比较 —— 实测 GBA 里 boxFront.jpg
# 与 boxfront.jpg 并存，Linux/CIFS 大小写敏感，硬拼路径会静默读不到）
_COVER_STEMS = ('boxfront', 'box_front', 'box', 'cover', 'front', 'folder')
_LOGO_STEMS = ('logo', 'marquee', 'wheel')
# 扩展名按优先级尝试（实测同系统内 jpg/png 混用）
_EXTS = ('.png', '.jpg', '.jpeg', '.webp')

_IMG_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')

# 掌机侧图片目录名：默认 img（实测 nes 928 条全用它 + 散文件）
DEFAULT_MEDIA_DIR = 'img'
# 已知的其它叫法，按出现频次排列（技术架构文档 §7.3 实测统计）
_KNOWN_MEDIA_DIRS = ('img', 'media', 'images', 'image', 'covers', 'boxart')

# 布局：散文件 vs 每游戏一子目录
LAYOUT_FLAT = 'flat'          # <dir>/<stem>.<ext>
LAYOUT_SUBDIR = 'subdir'      # <dir>/<stem>/boxfront.<ext>
DEFAULT_LAYOUT = LAYOUT_FLAT  # 探测不到时的默认（nes 是最常见的形态）

# 子目录式布局里，封面放进子目录后的固定文件名
SUBDIR_COVER_NAME = 'boxfront'


def _norm(s):
    return (s or '').strip().lower()


def rom_stem(rom_name):
    """ROM 文件名 -> 去掉扩展名的基准名（素材目录名就取它）"""
    base = os.path.basename((rom_name or '').replace('\\', '/'))
    return os.path.splitext(base)[0]


def _listdir_ci(path):
    """列出目录内容 -> {小写名: 真实名}。读不到就返回空（NAS 掉线不该抛异常）"""
    try:
        names = os.listdir(path)
    except OSError:
        return {}
    return dict((n.lower(), n) for n in names)


def find_asset(nas_system_dir, rom_name, kind='box_front', media_dir=None):
    """在 NAS 上找某个游戏的素材，返回 (绝对路径, 相对路径) 或 (None, None)

    nas_system_dir: NAS 上该系统的目录，如 /mnt/Roms/FC
    rom_name:       ROM 文件名（如 1943.zip）或已知的 media 目录名（如 1943）
    kind:           'box_front' 或 'logo'

    策略（由准到宽，逐级回退）：
      1. 直接推导 <media>/<stem>/<候选名><扩展名>（大小写不敏感）
      2. 枚举该 media 目录，挑出像封面的第一个图片
    """
    if not nas_system_dir:
        return None, None

    if kind == 'logo':
        stems = _LOGO_STEMS
    else:
        stems = _COVER_STEMS

    # 候选目录名：显式给的 media_dir 优先，否则用 rom 去扩展名
    cands = []
    if media_dir:
        cands.append(media_dir)
    st = rom_stem(rom_name)
    if st and st not in cands:
        cands.append(st)

    for base in cands:
        for md in ('media', ''):
            d = os.path.join(nas_system_dir, md, base) if md else os.path.join(nas_system_dir, base)
            files = _listdir_ci(d)
            if not files:
                continue
            # 1) 按候选名 + 扩展名精确找（大小写不敏感）
            for stem in stems:
                for ext in _EXTS:
                    hit = files.get(stem + ext)
                    if hit:
                        rel = os.path.join(md, base, hit) if md else os.path.join(base, hit)
                        return os.path.join(d, hit), rel
            # 2) 退一步：目录里任何像图片的文件里，挑名字含 cover/box/front 的
            imgs = [(k, v) for k, v in sorted(files.items())
                    if os.path.splitext(k)[1] in _IMG_EXTS]
            for k, v in imgs:
                if any(t in k for t in ('cover', 'box', 'front', 'folder')):
                    rel = os.path.join(md, base, v) if md else os.path.join(base, v)
                    return os.path.join(d, v), rel
            # 3) 只有 logo 请求时，别再退化成随便一张图
            if kind != 'logo' and len(imgs) == 1:
                k, v = imgs[0]
                rel = os.path.join(md, base, v) if md else os.path.join(base, v)
                return os.path.join(d, v), rel
    return None, None


def _detect_layout(dir_path, sample=60):
    """判断某个素材目录里是「散文件」还是「每游戏一子目录」

    返回 (layout, media_dir_name) —— 文件名从路径里带出来，省一次拼装。
    判据（实测数据很干净，不用纠结边缘情况）：
      - 出现 >=1 个「含图片的子目录」且 散图片数 == 0  -> subdir
      - 出现 >=1 个散图片且 含图片的子目录数 == 0      -> flat
      - 都没有 / 读不到                                -> (None, None)
    """
    try:
        names = os.listdir(dir_path)
    except OSError:
        return None, None

    subdirs_with_img = 0
    flat_imgs = 0
    for n in names[:sample * 4]:
        p = os.path.join(dir_path, n)
        try:
            if os.path.isdir(p):
                # 子目录里有没有 boxfront*/图片
                try:
                    for c in os.listdir(p):
                        if os.path.splitext(c)[1].lower() in _IMG_EXTS:
                            subdirs_with_img += 1
                            break
                except OSError:
                    pass
            elif os.path.splitext(n)[1].lower() in _IMG_EXTS:
                flat_imgs += 1
        except OSError:
            continue
        if subdirs_with_img and flat_imgs:
            break  # 混着的情况不用再数了

    if subdirs_with_img and not flat_imgs:
        return LAYOUT_SUBDIR, None
    if flat_imgs and not subdirs_with_img:
        return LAYOUT_FLAT, None
    # 混合或者空：以先出现的为准，空则不定
    if subdirs_with_img:
        return LAYOUT_SUBDIR, None
    if flat_imgs:
        return LAYOUT_FLAT, None
    return None, None


def pick_media_dir(es_path, gamelist_text=None):
    """决定这个系统该把图片放进哪个子目录、以及用哪种布局

    返回 (media_dir_name, layout)。规则（由准到宽）：

      1. 从 gamelist.xml 里已有的 <image> 路径反推 —— 这是最可靠的证据，
         因为那正是 ES 实际在读的东西。
         `./img/1943.png`            -> ('img',   flat)
         `./media/黄金太阳/boxfront.png` -> ('media', subdir)
      2. 探测文件系统里已存在的候选目录，看它里面是散文件还是子目录。
      3. 都没有 -> 默认 ('img', flat)。
    """
    # 1) 从 gamelist 的 <image> 反推
    if gamelist_text:
        for m in re.finditer(r'<image>\s*\./([^<]+?)\s*</image>', gamelist_text):
            rel = m.group(1).strip().replace('\\', '/')
            parts = [p for p in rel.split('/') if p]
            if len(parts) >= 3:
                # ./media/<游戏>/boxfront.png  -> 子目录式
                return parts[0], LAYOUT_SUBDIR
            if len(parts) == 2:
                # ./img/1943.png -> 散文件式
                return parts[0], LAYOUT_FLAT
    # 2) 探测文件系统
    for d in _KNOWN_MEDIA_DIRS:
        p = os.path.join(es_path, d)
        if not os.path.isdir(p):
            continue
        layout, _ = _detect_layout(p)
        if layout:
            return d, layout
    # 3) 默认
    return DEFAULT_MEDIA_DIR, DEFAULT_LAYOUT


def target_image_name(rom_name, src_ext):
    """散文件布局下的掌机侧图片名：<ROM 去扩展名><原扩展名>

    实测掌机既有文件就是这个规律：ROM 1943.zip -> img/1943.png
    （扩展名跟源文件走，不强行转成 png —— 字节完全一样，不需要转码）
    """
    return rom_stem(rom_name) + (src_ext or '.png')


def target_image_rel(media_dir, layout, rom_name, src_ext):
    """按布局算出掌机侧图片的**相对路径**（相对该系统根目录）

    flat   -> img/1943.png
    subdir -> media/黄金太阳/boxfront.png

    返回 (rel_for_es, abs_under_es_path_parts)
      rel_for_es: 写进 gamelist 的 `./...` 去掉 `./` 后的部分（用 / 分隔）
      parts:      需要在 es_path 下逐级创建/写入的路径片段
    """
    stem = rom_stem(rom_name)
    ext = src_ext or '.png'
    if layout == LAYOUT_SUBDIR:
        parts = [media_dir, stem, SUBDIR_COVER_NAME + ext]
        return '/'.join(parts), parts
    parts = [media_dir, stem + ext]
    return '/'.join(parts), parts


def copy_atomic(src, dst, chunk=256 * 1024):
    """流式复制 + 临时文件原子替换。返回写入字节数"""
    tmp = dst + '.part'
    n = 0
    with open(src, 'rb') as fi, open(tmp, 'wb') as fo:
        while True:
            b = fi.read(chunk)
            if not b:
                break
            fo.write(b)
            n += len(b)
    os.replace(tmp, dst)
    return n
