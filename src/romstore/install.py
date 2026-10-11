# -*- coding: utf-8 -*-
"""安装 / 卸载

一次「安装」做四件事：
  1. 复制 ROM（单碟 1 个文件；多碟 N 个文件 + 生成 .m3u）
  2. 复制封面到 /roms/<系统>/<img|media>/（见 media.py，P2 新增）
  3. 写 / 更新 gamelist.xml 里的一条 <game>（中文标题 + 描述 + 封面路径）
  4. （仍不做）视频预览 —— 单张 2 MB，SD 写速只有 4.8 MB/s，留待按需选装

gamelist.xml 采用**文本级插入**而非重新解析序列化：
  掌机上 /roms/nes/gamelist.xml 已有 926 条、带 BOM、2 空格缩进，
  用 ElementTree 重写会丢失原有格式与实体，风险不可接受。
"""
import json
import os
import re
import shutil
import time

from . import config
from . import media as media_mod

_GL_HEAD = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<gameList>\n</gameList>\n'


def _esc(s):
    """XML 文本转义。只转 & < >（双引号在文本节点里无需转义，转了反而显示成 &quot;）"""
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def free_bytes(path):
    """path 所在文件系统的可用字节数（优先用其最近的已存在父目录）"""
    p = path
    while p and not os.path.exists(p):
        np = os.path.dirname(p)
        if np == p:
            break
        p = np
    try:
        st = os.statvfs(p or '/')
        return st.f_bavail * st.f_frsize
    except OSError:
        return 0


def human(n):
    if n is None:
        return '?'
    n = float(n)
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or u == 'TB':
            return ('%.0f %s' % (n, u)) if u in ('B', 'KB') else ('%.2f %s' % (n, u))
        n /= 1024.0
    return '%.2f TB' % n


def rom_sources(conn, game):
    """返回 [(源路径, 目标文件名)]，并给出总字节数（会 stat，详情页调用）"""
    nas_dir = game['nas_dir']
    base_src = os.path.join(config.NAS_ROOT, nas_dir)
    rels = []
    if game['roms_json']:
        try:
            rels = json.loads(game['roms_json'])
        except ValueError:
            rels = []
    if not rels and game['rom_rel']:
        rels = [game['rom_rel']]
    out = []
    total = 0
    for r in rels:
        r = r.replace('\\', '/')
        src = os.path.join(base_src, r)
        try:
            sz = os.path.getsize(src)
        except OSError:
            sz = None
        out.append((src, os.path.basename(r), sz))
        if sz:
            total += sz
    return out, total


def sanitize(name):
    """exFAT 上不允许的字符替换掉"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    return name.strip().rstrip('.')


def _detect_newline(text):
    """探测文件的换行风格。

    实测掌机 /roms/nes/gamelist.xml 全部是 CRLF（4632 个 \r\n、0 个裸 \n），
    若写入时混用 LF，会造成换行风格不一致（XML 语义无害，但不利于比对与维护）。
    """
    crlf = text.count('\r\n')
    lf = text.count('\n')
    return '\r\n' if (crlf > 0 and crlf == lf) else '\n'


def build_gamelist_block(rom_name, title, desc=None, image=None, developer=None,
                         genre=None, players=None, nl='\n'):
    L = ['  <game>', '    <path>./%s</path>' % _esc(rom_name)]
    if title:
        L.append('    <name>%s</name>' % _esc(title))
    if desc:
        L.append('    <desc>%s</desc>' % _esc(desc))
    if developer:
        L.append('    <developer>%s</developer>' % _esc(developer))
    if genre:
        L.append('    <genre>%s</genre>' % _esc(genre))
    if players:
        L.append('    <players>%s</players>' % _esc(players))
    if image:
        L.append('    <image>%s</image>' % _esc(image))
    L.append('  </game>')
    return nl.join(L)


_GAME_BLOCK_RE = re.compile(r'[ \t]*<game>(?:(?!</game>).)*?</game>', re.S)
_PATH_RE = re.compile(r'<path>\s*\./(.*?)\s*</path>', re.S)


def _find_block(text, rom_name):
    """定位包含指定 ./rom_name 的整个 <game> 块，返回 (start, end, block)"""
    want = _esc(rom_name)
    for m in _GAME_BLOCK_RE.finditer(text):
        blk = m.group(0)
        pm = _PATH_RE.search(blk)
        if pm and pm.group(1).strip() == want:
            return m.start(), m.end(), blk
    return None


def _extract_tag(block, tag):
    """从一段 <game> 块里取出 <tag> 的值，没有就返回 None（不解析 XML，纯文本）"""
    m = re.search(r'<%s>\s*(.*?)\s*</%s>' % (tag, tag), block, re.S)
    return m.group(1) if m else None


def upsert_gamelist(es_path, entries, dry_run=False):
    """entries: [{'rom_name','title','desc','image','developer','genre','players'}]

    策略：**文本级替换/插入**，不重新解析序列化。
    插入点取 </gameList> 之前，且不 rstrip 原有内容，保证原有字节序列完全不变。
    返回 (新增数, 更新数, gamelist 路径)
    """
    gl = os.path.join(es_path, 'gamelist.xml')
    if os.path.isfile(gl):
        with open(gl, 'rb') as f:
            raw = f.read()
        bom = raw.startswith(b'\xef\xbb\xbf')
        text = raw.decode('utf-8-sig' if bom else 'utf-8', 'replace')
    else:
        bom = True
        text = _GL_HEAD

    if '</gameList>' not in text:
        # 文件损坏，重建骨架（避免越写越坏）
        text = _GL_HEAD

    nl = _detect_newline(text)
    added = updated = 0
    for e in entries:
        rom_name = e['rom_name']
        image = e.get('image')
        hit = _find_block(text, rom_name)
        # **绝不覆盖已有的 <image>**：这一次没找到/没导入封面时，
        # e['image'] 是 None，若直接重建 block 会把原条目里本来就有的
        # <image> 删掉（0.4.0 实机踩过：装 GBA 的 GT3锦标赛 时，
        # 它原本指向 ./media/GT3锦标赛/boxfront.png，被我们抹成了无图）。
        # 封面只做增量补充：新图优先，没有新图就沿用旧的。
        if hit and not image:
            old_img = _extract_tag(hit[2], 'image')
            if old_img:
                image = old_img
        block = build_gamelist_block(rom_name, e.get('title'), e.get('desc'),
                                     image, e.get('developer'),
                                     e.get('genre'), e.get('players'), nl=nl)
        if hit:
            s, en, _old = hit
            text = text[:s] + block + text[en:]
            updated += 1
        else:
            idx = text.rfind('</gameList>')
            text = text[:idx] + block + nl + text[idx:]
            added += 1

    if not dry_run:
        if not os.path.isdir(es_path):
            os.makedirs(es_path, exist_ok=True)
        with open(gl, 'wb') as f:
            if bom:
                f.write(b'\xef\xbb\xbf')
            f.write(text.encode('utf-8'))
    return added, updated, gl


def already_installed(es_path, rom_name):
    return os.path.exists(os.path.join(es_path, rom_name))


def install(conn, game, rom_bytes=None, progress=None, dry_run=False,
            copy_media=True):
    """安装一个游戏。progress(phase, done, total, msg)

    phase: 'check' | 'copy' | 'm3u' | 'art' | 'gamelist' | 'done'

    copy_media=True 时，顺带把封面复制到 /roms/<系统>/<img|media>/，
    并写进 gamelist 的 <image>（P2 新增；NAS 上没有封面就静默跳过）。
    """
    es_path = game['es_path']
    if not es_path:
        return {'ok': False, 'error': '该系统未映射到 ES 目录'}

    srcs, total = rom_sources(conn, game)
    if not srcs:
        return {'ok': False, 'error': '元数据里没有 ROM 路径'}

    missing = [s for s, _n, sz in srcs if sz is None]
    if missing:
        return {'ok': False, 'error': 'NAS 上找不到源文件: %s' % missing[0]}

    if progress:
        progress('check', 0, total, '检查空间')

    need = total
    if game['is_multi']:
        need += 1024  # m3u 很小
    avail = free_bytes(es_path)
    if avail < need + config.MIN_FREE_BYTES:
        return {'ok': False,
                'error': '空间不足：需要 %s，可用 %s（保留 %s 余量）'
                         % (human(need), human(avail), human(config.MIN_FREE_BYTES))}

    title = game['title']
    multi = bool(game['is_multi'])

    if multi:
        # 多碟：碟片放进以媒体目录/标题命名的子目录，并在同级生成 .m3u
        sub = sanitize(game['media_dir'] or title)
        dest_dir = os.path.join(es_path, sub)
        m3u_name = sanitize(title) + '.m3u'
        m3u_path = os.path.join(es_path, m3u_name)
    else:
        dest_dir = es_path
        m3u_name = None
        m3u_path = None

    if dry_run:
        return {'ok': True, 'dry_run': True, 'dest': dest_dir,
                'm3u': m3u_path, 'bytes': total, 'files': len(srcs)}

    os.makedirs(dest_dir, exist_ok=True)

    done = 0
    written = []
    for src, name, sz in srcs:
        dst = os.path.join(dest_dir, name)
        tmp = dst + '.part'
        with open(src, 'rb') as fi, open(tmp, 'wb') as fo:
            while True:
                chunk = fi.read(512 * 1024)
                if not chunk:
                    break
                fo.write(chunk)
                done += len(chunk)
                if progress:
                    progress('copy', done, total, name)
        os.replace(tmp, dst)
        written.append((name, sz))

    if multi and m3u_path:
        if progress:
            progress('m3u', total, total, '生成播放列表')
        with open(m3u_path, 'w', encoding='utf-8') as f:
            for name, _sz in written:
                f.write('%s/%s\n' % (sub, name))
        gl_rom = m3u_name
    else:
        gl_rom = written[0][0]

    # ---- 封面 ----------------------------------------------------------------
    # 素材在 NAS 上按 media/<ROM名去扩展名>/ 放，可直接推导（见 media.py）。
    # 掌机侧写**显式路径**给 ES，所以不需要猜目录约定；
    # 但目录名要沿用该系统既有习惯（nes 用 img/、gba 用 media/）。
    image_rel = None
    art = {'ok': False, 'copied': None, 'reason': None}
    if copy_media:
        if progress:
            progress('art', total, total, '复制封面')
        art = _install_cover(game, es_path, gl_rom, dry_run=dry_run)
        if art.get('ok'):
            image_rel = art['gamelist_path']

    desc = game['desc'] if 'desc' in game.keys() else None
    if progress:
        progress('gamelist', total, total, '写入 gamelist.xml')
    added, updated, glpath = upsert_gamelist(es_path, [{
        'rom_name': gl_rom, 'title': title, 'desc': desc,
        'developer': game['developer'] if 'developer' in game.keys() else None,
        'image': image_rel,
    }])

    if progress:
        progress('done', total, total, '完成')
    return {'ok': True, 'dest': dest_dir, 'm3u': m3u_path, 'bytes': total,
            'files': len(srcs), 'gamelist': glpath,
            'gamelist_added': added, 'gamelist_updated': updated,
            'rom_name': gl_rom, 'image': image_rel, 'art': art}


def _install_cover(game, es_path, rom_name, dry_run=False):
    """把 NAS 上的封面复制到掌机，并返回 gamelist 用的相对路径

    返回 {'ok', 'copied', 'gamelist_path', 'reason'}；没有封面时 ok=False
    且带 reason，**不视为安装失败**（很多游戏 NAS 上本来就没图）。

    掌机侧有**两套布局**（实测 2026-10-11）：
      flat   nes: img/1943.png                 —— 散文件
      subdir gba: media/黄金太阳/boxfront.png   —— 每游戏一子目录
    先探测该系统在用哪套，再按同款写；否则新装的 GBA 游戏会跟同系统
    另外 462 个游戏长得不一样，还可能撞上已存在的同名目录。
    """
    nas_dir = game['nas_dir']
    nas_sys = os.path.join(config.NAS_ROOT, nas_dir)
    media_dir = game['media_dir'] if 'media_dir' in game.keys() else None

    src, rel = media_mod.find_asset(nas_sys, rom_name, 'box_front', media_dir)
    if not src:
        return {'ok': False, 'reason': 'NAS 上没有封面', 'copied': None}

    ext = os.path.splitext(src)[1].lower() or '.png'

    # 决定目录名 + 布局：沿用该系统已有写法
    gl = os.path.join(es_path, 'gamelist.xml')
    gl_text = None
    if os.path.isfile(gl):
        try:
            with open(gl, 'rb') as f:
                gl_text = f.read().decode('utf-8-sig', 'replace')
        except OSError:
            gl_text = None
    sub, layout = media_mod.pick_media_dir(es_path, gl_text)

    rel_path, parts = media_mod.target_image_rel(sub, layout, rom_name, ext)
    dst = os.path.join(es_path, *parts)
    gl_path = './' + rel_path

    # 子目录式布局下，若同名**文件**已存在（历史遗留的散文件），
    # makedirs 会失败 —— 先探一下给出清楚的原因，别让安装无谓报错。
    if layout == media_mod.LAYOUT_SUBDIR:
        first = os.path.join(es_path, parts[0], parts[1])
        if os.path.isfile(first):
            return {'ok': False, 'copied': None,
                    'reason': '同名文件挡路，无法建封面目录: %s' % first}

    if dry_run:
        return {'ok': True, 'copied': dst, 'bytes': None,
                'gamelist_path': gl_path, 'layout': layout}

    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        n = media_mod.copy_atomic(src, dst)
    except OSError as e:
        return {'ok': False, 'reason': '复制封面失败: %s' % e, 'copied': None}

    return {'ok': True, 'copied': dst, 'bytes': n, 'source': src,
            'gamelist_path': gl_path, 'layout': layout}


def remove(conn, game, to_trash=True, dry_run=False):
    """卸载：ROM（+m3u +子目录）移入回收站，并删除 gamelist 条目"""
    es_path = game['es_path']
    srcs, total = rom_sources(conn, game)
    base_src = os.path.join(config.NAS_ROOT, game['nas_dir'])
    rom_name = None
    art_stems = []
    if game['is_multi']:
        rom_name = sanitize(game['title']) + '.m3u'
        art_stems.append(sanitize(game['title']))
    elif srcs:
        rom_name = srcs[0][1]
    # 封面基准名：优先 NAS 上 ROM 去扩展名（安装时就按它命名的）
    if srcs:
        art_stems.append(media_mod.rom_stem(srcs[0][1]))
    # 兜底：gamelist 用的 rom_name 去扩展名
    if rom_name:
        art_stems.append(media_mod.rom_stem(rom_name))
    # 去重且保序
    _seen = set()
    _uniq = []
    for s in art_stems:
        if s and s.lower() not in _seen:
            _seen.add(s.lower())
            _uniq.append(s)
    art_stems = _uniq

    moved = []
    if rom_name:
        gl = os.path.join(es_path, 'gamelist.xml')
        if os.path.isfile(gl) and not dry_run:
            with open(gl, 'rb') as f:
                raw = f.read()
            bom = raw.startswith(b'\xef\xbb\xbf')
            text = raw.decode('utf-8-sig' if bom else 'utf-8', 'replace')
            hit = _find_block(text, rom_name)
            if hit:
                s, en, blk = hit
                # 连同该块前面的缩进一起删，但保留其后的换行结构
                while s > 0 and text[s - 1] in ' \t':
                    s -= 1
                new = text[:s] + text[en:]
                # 若删除后留下空行，收掉一个
                if new[:s].endswith('\r\n') and new[s:s + 2] == '\r\n':
                    new = new[:s] + new[s + 2:]
                elif new[:s].endswith('\n') and new[s:s + 1] == '\n':
                    new = new[:s] + new[s + 1:]
                with open(gl, 'wb') as f:
                    if bom:
                        f.write(b'\xef\xbb\xbf')
                    f.write(new.encode('utf-8'))

    stamp = time.strftime('%Y%m%d-%H%M%S') if (to_trash and not dry_run) else None
    trash = None

    def _trash_dir():
        """延后建目录：只有真的要移动文件时才建。
        否则「卸载一个本地根本没有的游戏」会留下一个空的时间戳目录。
        """
        nonlocal trash
        if trash is None:
            trash = os.path.join(config.TRASH_DIR, stamp)
            os.makedirs(trash, exist_ok=True)
        return trash

    for src, name, _sz in srcs:
        dst_file = os.path.join(es_path, name)
        if game['is_multi']:
            dst_file = os.path.join(es_path, sanitize(game['media_dir'] or game['title']), name)
        if not os.path.exists(dst_file):
            continue
        if dry_run:
            moved.append(dst_file)
            continue
        if to_trash:
            shutil.move(dst_file, os.path.join(_trash_dir(), name))
        else:
            os.remove(dst_file)
        moved.append(dst_file)

    m3u_moved = False
    if rom_name:
        m3u = os.path.join(es_path, rom_name)
        if os.path.exists(m3u) and not dry_run:
            if to_trash:
                shutil.move(m3u, os.path.join(_trash_dir(), rom_name))
            else:
                os.remove(m3u)
            m3u_moved = True

    # ---- 封面 ----------------------------------------------------------------
    # 两套布局都要清（实测 nes 散文件 / gba 每游戏子目录）：
    #   flat   img/1943.png
    #   subdir media/1943/boxfront.png  -> 要连空目录一起收掉
    art_moved = []
    if art_stems:
        for sub in media_mod._KNOWN_MEDIA_DIRS:
            d = os.path.join(es_path, sub)
            if not os.path.isdir(d):
                continue
            # flat：<stem>.<图片扩展名>
            for stem in art_stems:
                for fn in _images_with_stem(d, stem):
                    p = os.path.join(d, fn)
                    if p in art_moved:
                        continue
                    if dry_run:
                        art_moved.append(p)
                        continue
                    if to_trash:
                        shutil.move(p, os.path.join(_trash_dir(), fn))
                    else:
                        os.remove(p)
                    art_moved.append(p)
            # subdir：<stem>/ 里的图片；只在该目录**整体**是素材时才删
            for stem in art_stems:
                sub_dir = _find_dir_ci(d, stem)
                if not sub_dir:
                    continue
                imgs = _images_in_dir(sub_dir)
                # 只在目录里全是图片（没有别的东西）时才动手，避免误删
                try:
                    allnames = os.listdir(sub_dir)
                except OSError:
                    continue
                if not imgs:
                    continue
                leftover = [n for n in allnames
                            if n not in imgs]
                for fn in imgs:
                    p = os.path.join(sub_dir, fn)
                    if p in art_moved:
                        continue
                    if dry_run:
                        art_moved.append(p)
                        continue
                    if to_trash:
                        shutil.move(p, os.path.join(_trash_dir(), fn))
                    else:
                        os.remove(p)
                    art_moved.append(p)
                # 素材目录空了才删；里面有 video.mp4 之类非图片就保留
                if not dry_run and not leftover:
                    try:
                        os.rmdir(sub_dir)
                    except OSError:
                        pass

    if not dry_run and not moved and not m3u_moved and not art_moved:
        # 本地没有这个游戏：不能谎报"已卸载"
        return {'ok': True, 'nothing': True, 'moved': [],
                'trash': trash, 'rom_name': rom_name, 'art_moved': []}

    return {'ok': True, 'moved': moved, 'trash': trash, 'rom_name': rom_name,
            'art_moved': art_moved}


def _find_dir_ci(d, stem):
    """在目录 d 里按大小写不敏感找子目录 stem，返回绝对路径或 None"""
    try:
        names = os.listdir(d)
    except OSError:
        return None
    want = stem.lower()
    for n in names:
        if n.lower() != want:
            continue
        p = os.path.join(d, n)
        if os.path.isdir(p):
            return p
    return None


def _images_in_dir(d):
    """列出目录里所有纯图片文件名"""
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        p = os.path.join(d, n)
        if os.path.isfile(p) and os.path.splitext(n)[1].lower() in media_mod._IMG_EXTS:
            out.append(n)
    return out


def _images_with_stem(d, stem):
    """列出目录里所有 <stem>.<图片扩展名> 的文件（大小写不敏感）

    只认纯图片扩展名，避免把同名 ROM 误当封面删掉。
    """
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    want = stem.lower()
    for n in names:
        base, ext = os.path.splitext(n)
        if base.lower() == want and ext.lower() in media_mod._IMG_EXTS:
            out.append(n)
    return out
