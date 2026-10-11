# -*- coding: utf-8 -*-
"""批量导入某个系统已装游戏的封面（不改动 ROM，只补图）

为什么需要它（0.4.1 实测）：

    /roms/gba/gamelist.xml 里 469 条 <game> **全都写了** <image>，
    但引用的 ./media/<游戏名>/boxfront.* 在掌机上**一个文件都没有**
    —— 那 457 个素材目录从来没被搬过来。ES 打开 GBA 就是一片空图框。

    逐款用「安装」补图是不行的：这些游戏**已经装好了**，重跑 install
    会重传 ROM（GBA 单包几 MB，152 款白写几百 MB）。所以这里只做封面。

与 install.py 的关系：
  * 复用同一套 media.find_asset / pick_media_dir / target_image_rel
    —— 命名与布局规则**只有一份**，不会出现「批量导入的图跟单个安装的图长得不一样」
  * 复用 install.upsert_gamelist 写 gamelist
    —— 它已经有「绝不覆盖已有 <image>」的保护，但本次是**主动更新**，
       所以这里显式传入新路径（image 非 None 时会正常替换）

幂等：已存在且字节相同的封面跳过（用 size + 首尾块比对，不做全量读）。
"""
import os
import time

from . import config
from . import index as idx
from . import install as inst
from . import mapp
from . import media as media_mod


def _same_file(src, dst):
    """粗略判断目标是否已是同一个文件：先比大小，再比首尾 4 KB。

    不做全量 sha256 —— CIFS 上读满 700 KB × 400 次是纯粹的浪费，
    而封面这种静态素材，size 相同 + 首尾相同已经足够可靠。
    """
    try:
        ss = os.path.getsize(src)
        ds = os.path.getsize(dst)
    except OSError:
        return False
    if ss != ds:
        return False
    if ss == 0:
        return True
    n = min(4096, ss)
    try:
        with open(src, 'rb') as a, open(dst, 'rb') as b:
            if a.read(n) != b.read(n):
                return False
            if ss > n:
                a.seek(-n, os.SEEK_END)
                b.seek(-n, os.SEEK_END)
                if a.read(n) != b.read(n):
                    return False
        return True
    except OSError:
        return False


def _existing_cover(dst):
    """在 dst 所在目录里**大小写不敏感**地找同名封面，返回真实路径或 None

    为什么需要（0.4.1 实机 bug）：
      NAS 上封面文件名大小写不统一 —— `boxFront.png` 与 `boxfront.png` 并存。
      我们写盘时统一用小写 `boxfront.<ext>`，于是在**大小写敏感的 exFAT** 上：

        NAS:   media/火焰纹章.../boxFront.png
        掌机:  media/火焰纹章.../boxFront.png   ← 上次原样落盘（大写 F）
        本次算出的 dst: .../boxfront.png        ← 小写 f，与上面**不是同一个文件**

      `os.path.getsize(dst)` 直接抛 OSError → `_same_file` 判 False
      → **同一张图每次都被当"缺"重新导入**（9 个游戏无限重复，纯浪费 CIFS 带宽）。

      修法：先按大小写不敏感找目录里已有的 `boxfront.*`，找到就用真实路径去比。
    """
    d = os.path.dirname(dst)
    want = os.path.basename(dst).lower()
    try:
        names = os.listdir(d)
    except OSError:
        return None
    for n in names:
        if n.lower() == want:
            return os.path.join(d, n)
    return None


def plan(nas_dir, es_name=None, only_missing=True):
    """算出这个系统要补哪些封面，返回计划 dict（不写盘）

    返回:
      {
        'es_path', 'nas_sys', 'media_dir', 'layout',
        'items': [ {game_id, title, rom_name, src, rel, dst, gl_path,
                    size, action} ... ],   # action: copy | skip
        'counts': {'copy': n, 'skip': n, 'nocover': n, 'bytes': n},
      }
    """
    conn = idx.connect()
    try:
        # 宽容解析：ES 名（gba）与 NAS 目录名（GBA）都认
        r = mapp.resolve(nas_dir)
        if not r:
            return {'error': '系统 %s 未映射到 ES（不在映射表中）' % nas_dir}
        nas_dir, _es_name, es_path, _conf, _note = r
        nas_sys = os.path.join(config.NAS_ROOT, nas_dir)
        if not os.path.isdir(nas_sys):
            return {'error': 'NAS 上没有目录 %s（挂载了吗？）' % nas_sys}
        if not os.path.isdir(es_path):
            return {'error': '掌机上没有 ES 目录 %s' % es_path}

        games = idx.games_of(conn, nas_dir, 0, 100000)

        # 布局：从 gamelist 反推该系统既有写法（与 install._install_cover 同源）
        gl = os.path.join(es_path, 'gamelist.xml')
        gl_text = None
        if os.path.isfile(gl):
            try:
                with open(gl, 'rb') as f:
                    gl_text = f.read().decode('utf-8-sig', 'replace')
            except OSError:
                gl_text = None
        media_dir, layout = media_mod.pick_media_dir(es_path, gl_text)

        items = []
        n_copy = n_skip = n_nocover = 0
        tot_bytes = 0
        for g in games:
            rom_name = _rom_name_of(g)
            if not rom_name:
                continue
            # 已装在掌机上的才算（本命令的对象是"已装游戏补图"）
            #
            # ⚠️ 顺序很重要（0.4.1 性能修正）：
            #   `already_installed` 只 stat 本机 exFAT（便宜），
            #   `find_asset` 要查 NAS（CIFS 往返，贵）。
            #   **必须先判断已装、再查 NAS** —— 否则 8210 款游戏全查一遍 NAS，
            #   `--gaps` 跑一分钟都出不来。
            if not inst.already_installed(es_path, rom_name):
                continue
            src, _rel = media_mod.find_asset(
                nas_sys, rom_name, 'box_front',
                g['media_dir'] if 'media_dir' in g.keys() else None)
            if not src:
                n_nocover += 1
                items.append({'game_id': g['id'], 'title': g['title'],
                              'rom_name': rom_name, 'action': 'nocover'})
                continue
            ext = os.path.splitext(src)[1].lower() or '.png'
            rel_path, parts = media_mod.target_image_rel(
                media_dir, layout, rom_name, ext)
            dst = os.path.join(es_path, *parts)
            gl_path = './' + rel_path

            if only_missing:
                # 大小写不敏感地找掌机上已有的同名封面（修 0.4.1 重复导入 bug）
                exist = _existing_cover(dst)
                if exist and _same_file(src, exist):
                    # 已有文件大小写可能与我们的目标名不同 —— gamelist 必须写
                    # **磁盘上的真实名字**，否则大小写敏感的 exFAT 上 ES 读不到
                    real_rel = rel_path
                    if os.path.basename(exist) != os.path.basename(dst):
                        real_rel = '/'.join(rel_path.split('/')[:-1]
                                            + [os.path.basename(exist)])
                    n_skip += 1
                    items.append({'game_id': g['id'], 'title': g['title'],
                                  'rom_name': rom_name, 'src': src, 'dst': exist,
                                  'gl_path': './' + real_rel, 'action': 'skip'})
                    continue
            try:
                sz = os.path.getsize(src)
            except OSError:
                sz = 0
            n_copy += 1
            tot_bytes += sz
            items.append({'game_id': g['id'], 'title': g['title'],
                          'rom_name': rom_name, 'src': src, 'dst': dst,
                          'gl_path': gl_path, 'size': sz, 'action': 'copy'})

        return {'es_path': es_path, 'nas_sys': nas_sys, 'media_dir': media_dir,
                'layout': layout, 'items': items,
                'counts': {'copy': n_copy, 'skip': n_skip,
                           'nocover': n_nocover, 'bytes': tot_bytes}}
    finally:
        conn.close()


def _rom_name_of(game):
    """从索引行推出装到掌机后的 ROM 文件名（单碟/多碟一致于 install.py）"""
    if game['is_multi']:
        return inst.sanitize(game['title']) + '.m3u'
    if game['rom_rel']:
        return os.path.basename(game['rom_rel'].replace('\\', '/'))
    return None


def run(nas_dir, progress=None, dry_run=False, only_missing=True,
        update_gamelist=True, limit=None):
    """执行批量导入。progress(phase, done, total, msg)

    phase: 'plan' | 'cover' | 'gamelist' | 'done'
    """
    p = plan(nas_dir, only_missing=only_missing)
    if p.get('error'):
        return {'ok': False, 'error': p['error']}

    todo = [it for it in p['items'] if it['action'] == 'copy']
    if limit:
        todo = todo[:limit]

    if progress:
        progress('plan', 0, max(1, len(todo)),
                 '待导入 %d 个（跳过 %d / 无封面 %d）'
                 % (len(todo), p['counts']['skip'], p['counts']['nocover']))

    free = inst.free_bytes(p['es_path'])
    if not dry_run and free < p['counts']['bytes'] + config.MIN_FREE_BYTES:
        return {'ok': False,
                'error': '空间不足：需要 %s，可用 %s（保留 %s 余量）'
                         % (inst.human(p['counts']['bytes']), inst.human(free),
                            inst.human(config.MIN_FREE_BYTES))}

    if dry_run:
        return {'ok': True, 'dry_run': True, 'plan': p, 'todo': len(todo),
                'clen': len(todo), 'will_gamelist': update_gamelist,
                'bytes': sum(it.get('size') or 0 for it in todo)}

    done = 0
    copied = skipped_fail = 0
    total_b = sum(it.get('size') or 0 for it in todo)
    written_b = 0
    ok_items = []
    for it in todo:
        try:
            os.makedirs(os.path.dirname(it['dst']), exist_ok=True)
            nb = media_mod.copy_atomic(it['src'], it['dst'])
            written_b += nb
            copied += 1
            ok_items.append(it)
        except OSError as e:
            skipped_fail += 1
            it['error'] = str(e)
        done += 1
        if progress:
            progress('cover', done, len(todo), it['title'][:24])

    added = updated = 0
    if update_gamelist and ok_items:
        if progress:
            progress('gamelist', len(todo), len(todo), '写入 gamelist.xml')
        entries = [{'rom_name': it['rom_name'], 'image': it['gl_path']}
                   for it in ok_items]
        added, updated, _gl = inst.upsert_gamelist(p['es_path'], entries)

    # 大小写修正：磁盘上若已有封面但文件名大小写与我们的标准名不同
    # （NAS 源是 boxFront.png 这类），gamelist 必须写**磁盘上的真实名**，
    # 否则在大小写敏感的 exFAT 上 ES 读不到。这里只对"本来就不需要重传、
    # 但真实名比标准名更准确"的项补一次 gamelist。
    fixed = 0
    if update_gamelist:
        fix_entries = []
        for it in p['items']:
            if it['action'] != 'skip' or not it.get('gl_path'):
                continue
            d = it.get('dst') or ''
            glp = it['gl_path']
            std_tail = glp.split('/')[-1]
            if d and os.path.basename(d) != std_tail:
                fix_entries.append({'rom_name': it['rom_name'], 'image': glp})
        if fix_entries:
            _a, _u, _gl = inst.upsert_gamelist(p['es_path'], fix_entries)
            fixed = _u + _a
            added += _a
            updated += _u

    if progress:
        progress('done', len(todo), len(todo), '完成')
    return {'ok': True, 'es_path': p['es_path'], 'media_dir': p['media_dir'],
            'layout': p['layout'], 'copied': copied,
            'failed': skipped_fail, 'skipped': p['counts']['skip'],
            'nocover': p['counts']['nocover'], 'bytes': written_b,
            'gamelist_added': added, 'gamelist_updated': updated,
            'case_fixed': fixed, 'seconds': None}


def systems_with_gaps():
    """列出所有系统「NAS 有素材、掌机缺图」的规模，供体检用

    返回 [ {nas_dir, es_name, es_path, installed, missing, nocover, bytes} ]
    """
    conn = idx.connect()
    out = []
    try:
        for r in idx.systems(conn):
            nas_dir = r['nas_dir']
            es_path = r['es_path']
            if not os.path.isdir(es_path):
                continue
            p = plan(nas_dir)
            if p.get('error'):
                continue
            c = p['counts']
            installed = c['copy'] + c['skip'] + c['nocover']
            if c['copy']:
                out.append({'nas_dir': nas_dir, 'es_name': r['es_name'],
                            'es_path': es_path, 'installed': installed,
                            'missing': c['copy'], 'have': c['skip'],
                            'nocover': c['nocover'], 'bytes': c['bytes']})
    finally:
        conn.close()
    out.sort(key=lambda d: -d['missing'])
    return out


# ---------------------------------------------------------------- 封面体检
#
# 为什么需要 audit（0.4.1 实机教训）：
#
#   用户报「GBA 封面看不到」。查了三层，结论完全不同：
#     ① gamelist 的 <image> 路径 —— 470 条全对，文件也全在 ✅
#     ② ES 却看不到 —— 因为 **ES 进程启动(23:16)早于 gamelist 写入(23:17)**
#        它读的还是旧表；重启后 imagecache.db 从 22:57 跳到 23:37，封面就出来了
#     ③ 中途还撞上 `/roms` exFAT 损坏被 remount-ro，写入静默失败
#
#   所以「封面看不到」有**三层**可能，只看一层会误判：
#     层1 引用层：gamelist 里的 <image> 指向的文件在不在磁盘上
#     层2 内容层：文件在，但内容坏没坏（exFAT 截断会留满垃圾/零字节）
#     层3 生效层：ES 有没有真的重读（看 imagecache.db 的 mtime 与条目）
#
#   本函数把三层一次查清，免得再靠一堆临时 plink 命令拼凑。

ES_CACHE = '/home/ark/.emulationstation/imagecache.db'
ES_CONF = '/home/ark/.emulationstation/es_settings.cfg'


def _read_gamelist(es_path):
    p = os.path.join(es_path, 'gamelist.xml')
    if not os.path.isfile(p):
        return None
    try:
        with open(p, 'rb') as f:
            return f.read().decode('utf-8-sig', 'replace')
    except OSError:
        return None


def _es_cache():
    """解析 ES 的 imagecache.db

    格式是每行 `<图片路径>|<字节数>|` 或 `<图片路径>|<字节数>|<宽>|<高>`。
    **写出宽高即代表 ES 成功解码过这张图**，是「封面链路通了」的铁证。
    注意它**只缓存当前浏览到的系统**，条目少不代表有问题。
    """
    rows = {}
    try:
        with open(ES_CACHE, 'r', errors='replace') as f:
            for line in f:
                line = line.rstrip('\n')
                if not line or '|' not in line:
                    continue
                parts = line.split('|')
                path = parts[0]
                size = parts[1] if len(parts) > 1 else ''
                dims = None
                if len(parts) >= 4 and parts[2] and parts[3]:
                    try:
                        dims = (int(parts[2]), int(parts[3]))
                    except ValueError:
                        dims = None
                rows[path] = {'size': size, 'dims': dims}
    except OSError:
        return None
    return rows


def audit(nas_dir=None, check_content=True, content_sample=0, progress=None):
    """封面三层体检

    参数:
      nas_dir        只查某个 NAS 目录或 ES 名（如 'gba' / 'GBA'）；None = 查全部已装系统
      check_content  是否抽查图片内容（防 exFAT 截断留下的坏图）
      content_sample 每个系统抽查多少个文件，0 = 全查
      progress       progress(done, total, name) 回调，全量查时给个进度提示

    返回 { 'systems': [ {...} ], 'totals': {...}, 'es': {...} }
    """
    conn = idx.connect()
    sys_rows = []
    try:
        if nas_dir:
            # 宽容解析：用户可能给 ES 名（gba）或 NAS 目录名（GBA）
            r = mapp.resolve(nas_dir)
            if not r:
                return {'error': '系统 %s 未映射到 ES（试试 --systems 看有哪些）'
                                 % nas_dir}
            cand = [{'nas_dir': r[0], 'es_name': r[1], 'es_path': r[2]}]
        else:
            cand = [{'nas_dir': r['nas_dir'], 'es_name': r['es_name'],
                     'es_path': r['es_path']} for r in idx.systems(conn)]
    finally:
        conn.close()

    n_cand = len(cand)
    for ci, c in enumerate(cand, 1):
        es_path = c['es_path']
        gl = _read_gamelist(es_path)
        if gl is None:
            continue
        if progress:
            progress(ci, n_cand, '%s (%s)' % (c['nas_dir'], c['es_name']))
        # 层1：引用层 —— 逐条比对 <image> 与磁盘
        n_img = n_ok = n_miss = n_nolabel = 0
        broken = []
        for gg in _iter_games(gl):
            img = gg.get('image')
            if not img:
                n_nolabel += 1
                continue
            n_img += 1
            rel = img[2:] if img.startswith('./') else img.lstrip('/')
            full = rel if os.path.isabs(img) else os.path.join(es_path, rel)
            if os.path.isfile(full):
                n_ok += 1
            else:
                n_miss += 1
                if len(broken) < 10:
                    broken.append({'rom': gg.get('path', '?'), 'image': img})

        # 层2：内容层 —— 抽查图片是不是真的图（exFAT 截断会留垃圾）
        n_bad_content = 0
        if check_content and n_ok:
            files = _collect_images(es_path, gl, content_sample)
            for fp in files:
                if not _looks_like_image(fp):
                    n_bad_content += 1

        sys_rows.append({
            'nas_dir': c['nas_dir'], 'es_name': c['es_name'],
            'es_path': es_path,
            'games': _count_tag(gl, '<game>'),
            'img_refs': n_img, 'img_ok': n_ok, 'img_missing': n_miss,
            'no_label': n_nolabel,
            'bad_content': n_bad_content,
            'broken': broken,
        })

    # 层3：生效层 —— ES 缓存状态
    es_info = {'cache_path': ES_CACHE, 'exists': os.path.isfile(ES_CACHE)}
    if es_info['exists']:
        try:
            st = os.stat(ES_CACHE)
            es_info['mtime'] = st.st_mtime
        except OSError:
            es_info['mtime'] = 0
        cache = _es_cache()
        es_info['entries'] = len(cache) if cache is not None else 0
        es_info['decoded'] = (sum(1 for v in (cache or {}).values()
                                  if v.get('dims')) if cache else 0)
    gl_paths = set()
    for s in sys_rows:
        glp = os.path.join(s['es_path'], 'gamelist.xml')
        if os.path.isfile(glp):
            try:
                gl_paths.add(os.path.getmtime(glp))
            except OSError:
                pass
    es_info['gamelist_newest'] = max(gl_paths) if gl_paths else 0

    # ES 主进程的启动时间 —— 比缓存 mtime 更可靠（ES 是**懒写**缓存的：
    # 只有切换系统或退出时才落盘，光看缓存 mtime 会误报"没重读"）
    es_info['proc_start'] = _es_proc_start()

    # 判定优先级：
    #   ① 有进程启动时间 → 它晚于 gamelist 修改，就是已重读（最准）
    #   ② 没进程信息（不在掌机上跑）→ 退回比缓存 mtime
    if es_info['gamelist_newest']:
        if es_info.get('proc_start'):
            es_info['stale'] = es_info['gamelist_newest'] > es_info['proc_start']
        elif es_info.get('mtime'):
            es_info['stale'] = es_info['gamelist_newest'] > es_info['mtime']

    tot = {'systems': len(sys_rows)}
    for k in ('games', 'img_refs', 'img_ok', 'img_missing',
              'no_label', 'bad_content'):
        tot[k] = sum(s[k] for s in sys_rows)
    return {'systems': sys_rows, 'totals': tot, 'es': es_info}


def _es_proc_start():
    """拿 ES 主进程的启动时间（epoch 秒），拿不到返回 0

    读 /proc/<pid>/stat 的第 22 个字段（starttime，单位 clock tick），
    配合 /proc/uptime 换算成绝对时间。比调用 ps 轻量，也不怕 shell 转义。

    两个坑（0.4.1 实机踩过）：
      * **`/proc/<pid>/comm` 最多 15 字符** —— `emulationstation` 会被截成
        `emulationstatio`（少个 n），用全名相等判断**永远匹配不上**。
        所以用**前缀匹配**。
      * comm 里可能有非 UTF-8 字节（其它进程的烂名字），读取必须带 errors。
    """
    import time as _t
    try:
        with open('/proc/uptime', 'r') as f:
            uptime = float(f.read().split()[0])
    except (OSError, ValueError):
        return 0
    try:
        hz = os.sysconf('SC_CLK_TCK') or 100
    except (ValueError, OSError):
        hz = 100
    now = _t.time()
    best = 0
    try:
        pids = os.listdir('/proc')
    except OSError:
        return 0
    for pid in pids:
        if not pid.isdigit():
            continue
        try:
            with open('/proc/%s/comm' % pid, 'r', errors='replace') as f:
                comm = f.read().strip()
        except OSError:
            continue
        if not comm.startswith('emulationstatio'):
            continue
        try:
            with open('/proc/%s/stat' % pid, 'r', errors='replace') as f:
                rest = f.read().rsplit(')', 1)[1].split()
            starttime = int(rest[19]) / float(hz)   # 第 22 字段
        except (OSError, IndexError, ValueError):
            continue
        started = now - (uptime - starttime)
        best = max(best, started)
    return best


def _iter_games(text):
    """文本级切分 <game> 块，抽 <path> 与 <image>（不重新序列化 XML）"""
    import re
    for m in re.finditer(r'<game>(.*?)</game>', text, re.S):
        block = m.group(1)
        p = re.search(r'<path>(.*?)</path>', block)
        i = re.search(r'<image>(.*?)</image>', block)
        yield {'path': p.group(1) if p else None,
               'image': i.group(1) if i else None}


def _count_tag(text, tag):
    return text.count(tag)


def _collect_images(es_path, gl, limit):
    """收集 gamelist 引用到的图片绝对路径（用于内容抽查）"""
    out = []
    for gg in _iter_games(gl):
        img = gg.get('image')
        if not img:
            continue
        rel = img[2:] if img.startswith('./') else img.lstrip('/')
        full = rel if os.path.isabs(img) else os.path.join(es_path, rel)
        if os.path.isfile(full):
            out.append(full)
            if limit and len(out) >= limit:
                break
    return out


_IMG_MAGIC = (
    b'\x89PNG\r\n\x1a\n',      # PNG
    b'\xff\xd8\xff',           # JPEG
    b'GIF87a', b'GIF89a',      # GIF
    b'BM',                     # BMP
    b'RIFF',                   # WEBP (RIFF....WEBP)
)


def _looks_like_image(path):
    """文件头是不是已知图片格式 —— exFAT 截断会留下全零/垃圾，这里能抓出来"""
    try:
        with open(path, 'rb') as f:
            head = f.read(16)
    except OSError:
        return False
    if not head:
        return False
    for mg in _IMG_MAGIC:
        if head.startswith(mg):
            return True
    return False
