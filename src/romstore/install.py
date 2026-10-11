# -*- coding: utf-8 -*-
"""安装 / 卸载

一次「安装」做三件事：
  1. 复制 ROM（单碟 1 个文件；多碟 N 个文件 + 生成 .m3u）
  2. 写 / 更新 gamelist.xml 里的一条 <game>（中文标题 + 描述）
  3. （P1 不做）复制封面 —— 封面走 CIFS 现取，见文档 §7.3

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
        block = build_gamelist_block(rom_name, e.get('title'), e.get('desc'),
                                     e.get('image'), e.get('developer'),
                                     e.get('genre'), e.get('players'), nl=nl)
        hit = _find_block(text, rom_name)
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
            copy_media=False):
    """安装一个游戏。progress(phase, done, total, msg)

    phase: 'check' | 'copy' | 'm3u' | 'gamelist' | 'done'
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

    desc = game['desc'] if 'desc' in game.keys() else None
    if progress:
        progress('gamelist', total, total, '写入 gamelist.xml')
    added, updated, glpath = upsert_gamelist(es_path, [{
        'rom_name': gl_rom, 'title': title, 'desc': desc,
        'developer': game['developer'] if 'developer' in game.keys() else None,
    }])

    if progress:
        progress('done', total, total, '完成')
    return {'ok': True, 'dest': dest_dir, 'm3u': m3u_path, 'bytes': total,
            'files': len(srcs), 'gamelist': glpath,
            'gamelist_added': added, 'gamelist_updated': updated,
            'rom_name': gl_rom}


def remove(conn, game, to_trash=True, dry_run=False):
    """卸载：ROM（+m3u +子目录）移入回收站，并删除 gamelist 条目"""
    es_path = game['es_path']
    srcs, total = rom_sources(conn, game)
    base_src = os.path.join(config.NAS_ROOT, game['nas_dir'])
    rom_name = None
    if game['is_multi']:
        rom_name = sanitize(game['title']) + '.m3u'
    elif srcs:
        rom_name = srcs[0][1]

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

    if not dry_run and not moved and not m3u_moved:
        # 本地没有这个游戏：不能谎报"已卸载"
        return {'ok': True, 'nothing': True, 'moved': [],
                'trash': trash, 'rom_name': rom_name}

    return {'ok': True, 'moved': moved, 'trash': trash, 'rom_name': rom_name}
