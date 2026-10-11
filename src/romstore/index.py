# -*- coding: utf-8 -*-
"""L1 结构化索引（SQLite）

设计要点：
  - 建索引只读 metadata.pegasus.txt，**不 stat ROM 文件**
    （CIFS 上每次 stat 都是一次网络往返，11,000 次会拖到几十秒）
    ROM 体积在详情页按需 stat 并回写缓存
  - 增量：用 (mtime, size) 文件级指纹，实测 78 个文件零误判
  - 去重：不同 NAS 目录常指向同一 ES 目录（DC 与 DC hack 内容字节一致、
    FC 与 FC hack 有重名 ROM），按 (es_path, rom 文件名小写) 去重，
    非 hack 目录优先；被去重的行标记 dup=1 而非删除
"""
import json
import os
import sqlite3
import time

from . import config
from . import mapp
from . import meta

SCHEMA = """
CREATE TABLE IF NOT EXISTS systems(
  nas_dir   TEXT PRIMARY KEY,
  es_name   TEXT NOT NULL,
  es_path   TEXT NOT NULL,
  conf      TEXT,
  note      TEXT,
  priority  INTEGER DEFAULT 100,
  n_games   INTEGER DEFAULT 0,
  mtime     REAL,
  size      INTEGER,
  built_at  REAL
);
CREATE TABLE IF NOT EXISTS games(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  nas_dir    TEXT NOT NULL,
  es_name    TEXT NOT NULL,
  es_path    TEXT NOT NULL,
  title      TEXT NOT NULL,
  sort_by    TEXT,
  rom_rel    TEXT,
  is_multi   INTEGER DEFAULT 0,
  roms_json  TEXT,
  media_dir  TEXT,
  core       TEXT,
  has_boxart INTEGER DEFAULT 0,
  rom_bytes  INTEGER,
  dup        INTEGER DEFAULT 0,
  -- ROM 文件**真实**所在的 NAS 子目录。通常等于 nas_dir，
  -- 但当一个 ES 系统被拆到多个目录（实测 SS / SS plus 共用同一份
  -- metadata、ROM 编号互补 001-160 与 161-752）时，它指向真正有文件的那个。
  -- 见 build() 里的 _resolve。
  src_dir    TEXT
);
CREATE INDEX IF NOT EXISTS ix_games_sys   ON games(es_path);
CREATE INDEX IF NOT EXISTS ix_games_dir   ON games(nas_dir);
CREATE INDEX IF NOT EXISTS ix_games_title ON games(title);
CREATE INDEX IF NOT EXISTS ix_games_dup   ON games(dup);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


SCHEMA_VERSION = '2'


def connect():
    """打开索引库。

    只在首次创建或 schema 版本变化时才执行建表脚本
    ——每次连接都跑一遍 executescript 会白白花掉几百毫秒。
    """
    config.ensure_dirs()
    fresh = not os.path.exists(config.DB_PATH)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    need = fresh
    if not fresh:
        try:
            r = conn.execute("SELECT v FROM kv WHERE k='schema'").fetchone()
            need = (not r) or (r['v'] != SCHEMA_VERSION)
        except sqlite3.DatabaseError:
            need = True
    if need:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.execute("INSERT OR REPLACE INTO kv(k,v) VALUES('schema',?)",
                     (SCHEMA_VERSION,))
        conn.commit()
    return conn


def _migrate(conn):
    """给已存在的表补上新增的列

    `CREATE TABLE IF NOT EXISTS` 在表已存在时是**空操作**，光 bump
    SCHEMA_VERSION 不会把新列加到老库上 —— 必须显式 ALTER。
    这里用「查 pragma 再决定」的写法，可以重复执行。
    """
    cols = set(r['name'] for r in conn.execute('PRAGMA table_info(games)'))
    if 'src_dir' not in cols:
        conn.execute('ALTER TABLE games ADD COLUMN src_dir TEXT')


def _kv_get(conn, k, d=None):
    r = conn.execute('SELECT v FROM kv WHERE k=?', (k,)).fetchone()
    return r['v'] if r else d


def _kv_set(conn, k, v):
    conn.execute('INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)', (k, str(v)))


def _priority(nas_dir):
    """越小越优先。带 hack/plus 的降级，避免覆盖原版"""
    s = nas_dir.lower()
    if 'hack' in s:
        return 80
    if 'plus' in s or ' v' in s:
        return 60
    return 10


# 统计 ROM 存在性时要跳过的素材目录（与 cover.SizeLoader 一致）
_MEDIA_DIRS = frozenset(('media', 'images', 'img', 'image', 'video', 'videos',
                         'covers', 'saves', 'savestate'))


def _file_index(nas_dir):
    """列出 nas_dir 下所有 ROM 文件的相对路径（小写）-> 真实相对路径

    递归下钻（实测 SS 里有 31 个、SS plus 里有 147 个文件放在
    `009/xxx.chd` 这样的分层子目录里），跳过素材目录。
    一次遍历约 0.03~0.11 秒（SMB），只在建索引时做一次。
    """
    root = os.path.join(config.NAS_ROOT, nas_dir)
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == '.':
            rel_dir = ''
        # 就地裁剪，避免走进 media/ 这些大目录
        keep = []
        for d in dirnames:
            tail = (rel_dir + '/' + d if rel_dir else d).split(os.sep)[0]
            if tail.lower() not in _MEDIA_DIRS:
                keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            rel = (rel_dir + '/' + f) if rel_dir else f
            out[rel.lower()] = rel
    return out


def _resolve(nas_dir, rom, own_index, sibling_indexes):
    """定位一个 ROM 的真实来源目录，返回 (rom_rel, src_dir)

    先在本目录找；找不到就到**同一个 ES 系统的兄弟目录**里找。

    ## 为什么需要这一层

    实测 `SS` 与 `SS plus` 两个目录映射到同一个 ES 系统（`saturn`），
    而且**共用完全相同的一份 metadata.pegasus.txt**（752 条逐条一致）——
    但 ROM 文件被拆到了两边：

        SS        编号 001~160（147 个文件真实存在）
        SS plus   编号 003~752（446 个文件真实存在）

    metadata 里的 `file:` 字段对两者是同一套值（都写着 `001.chd`…），
    于是：
      - 在 `SS` 里只有 147/752 能对上，**605 款指向不存在的文件**；
      - 在 `SS plus` 里同理对不上另一批；
      - 而 `_dedup` 按 `(es_path, rom 文件名)` 去重，`SS` 优先级更高，
        导致 `SS plus` **全部 752 条被判成重复**，一个都露不出来。

    用户看到的现象就是「SS 装不了、剩下的都在 SS plus 里」。

    修法就是在这里跨目录回退：metadata 说的是**整个 ES 系统的清单**，
    文件实际在哪算哪个目录的。跨目录找到后把真实目录记进 `src_dir`，
    安装（install.rom_sources）、大小统计（cover.SizeLoader）都改用它。

    兄弟目录按 `_priority` 排序后取**第一个命中的**，
    所以原版目录（priority 10）总是优先于 plus/hack（60/80）。
    """
    if not rom:
        return None, None
    rel = rom.replace('\\', '/').lstrip('/')
    if rel.lower() in own_index:
        return own_index[rel.lower()], nas_dir
    for sd, idx in sibling_indexes:
        hit = idx.get(rel.lower())
        if hit:
            return hit, sd
    return rel, nas_dir          # 都没有：保留原值，装的时候再报错


def scan_nas():
    """列出 NAS 根目录下所有带 metadata 的子目录 -> [(dir, mtime, size)]"""
    out = []
    try:
        names = sorted(os.listdir(config.NAS_ROOT))
    except OSError as e:
        return out, str(e)
    for d in names:
        p = os.path.join(config.NAS_ROOT, d)
        if not os.path.isdir(p):
            continue
        mp = os.path.join(p, config.METADATA_NAME)
        try:
            st = os.stat(mp)
        except OSError:
            continue
        out.append((d, st.st_mtime, st.st_size))
    return out, None


def build(progress=None, force=False):
    """建/更新索引。progress(stage, cur, total, msg) 可选"""
    conn = connect()
    t0 = time.time()
    dirs, err = scan_nas()
    if err:
        return {'ok': False, 'error': '无法读取 %s: %s' % (config.NAS_ROOT, err)}

    # ---- 先把所有目录的 es_path 定下来，并建好文件索引 ----
    # 必须在主循环**之前**做完：某个 ROM 到底在哪个兄弟目录里，
    # 只有把兄弟都看过才知道（见 _resolve 的说明）。
    # 只给「有兄弟的系统」建索引 —— 独占的系统不需要跨目录查找，
    # 而建索引要遍历整个目录树（68 个系统全建要多花好几秒）。
    es_of = {}
    for d, _mt, _sz in dirs:
        m = mapp.lookup(d)
        if m:
            es_of[d] = m[1]              # es_path
    by_es = {}
    for d, ep in es_of.items():
        by_es.setdefault(ep, []).append(d)

    total = len(dirs)
    built, skipped, failed = 0, 0, []
    for i, (d, mt, sz) in enumerate(dirs):
        if progress:
            progress('scan', i, total, d)
        m = mapp.lookup(d)
        if not m and d not in [u[0] for u in mapp.UNSUPPORTED]:
            skipped += 1
            continue
        if not m:
            # 明确不支持，但记录一行便于界面解释
            conn.execute(
                'INSERT OR REPLACE INTO systems'
                '(nas_dir,es_name,es_path,conf,note,priority,n_games,mtime,size,built_at)'
                ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                (d, '', '', 'unsupported',
                 dict((u[0], u[1]) for u in mapp.UNSUPPORTED).get(d, ''),
                 9999, 0, mt, sz, time.time()))
            continue

        es_name, es_path, conf, note = m
        row = conn.execute('SELECT mtime,size,n_games FROM systems WHERE nas_dir=?',
                           (d,)).fetchone()
        if (not force and row and row['mtime'] == mt and row['size'] == sz
                and row['n_games']):
            skipped += 1
            conn.execute('UPDATE systems SET es_name=?,es_path=?,conf=?,note=?,priority=?'
                         ' WHERE nas_dir=?',
                         (es_name, es_path, conf, note, _priority(d), d))
            continue

        mp = os.path.join(config.NAS_ROOT, d, config.METADATA_NAME)
        try:
            _hdr, games = meta.parse(mp)
        except Exception as e:
            failed.append((d, str(e)))
            continue

        # 只有**有兄弟目录**的系统才需要文件索引。没有兄弟时保持旧行为
        # （直接采信 metadata 的 file: 值），这样 68 个系统里绝大多数
        # 都不必遍历目录树 —— 遍历在 SMB 上不便宜（实测每系统 0.03~0.11 秒）。
        sibs = [sd for sd in by_es.get(es_path, []) if sd != d]
        if sibs:
            own_index = _file_index(d)
            sibling_indexes = [(sd, _file_index(sd)) for sd in sibs]
            # 先找优先级小的（原版优先于 plus/hack），保证同一款游戏
            # 不会因为目录顺序拿到 hack 版
            sibling_indexes.sort(key=lambda kv: (_priority(kv[0]), kv[0]))
        else:
            own_index = {}
            sibling_indexes = []

        conn.execute('DELETE FROM games WHERE nas_dir=?', (d,))
        rows = []
        n_moved = 0
        for g in games:
            rp = g.rom
            if g.roms:
                rp = g.roms[0]
            if sibling_indexes:
                rp, sd = _resolve(d, rp, own_index, sibling_indexes)
                if sd != d:
                    n_moved += 1
            else:
                sd = d
            rows.append((
                d, es_name, es_path, g.title, g.sort_by, rp,
                1 if g.is_multi else 0,
                json.dumps(g.roms, ensure_ascii=False) if g.roms else None,
                g.media_dir(), g.core, 1 if g.has_boxart() else 0, None, 0, sd))
        conn.executemany(
            'INSERT INTO games(nas_dir,es_name,es_path,title,sort_by,rom_rel,'
            'is_multi,roms_json,media_dir,core,has_boxart,rom_bytes,dup,src_dir)'
            ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        conn.execute(
            'INSERT OR REPLACE INTO systems'
            '(nas_dir,es_name,es_path,conf,note,priority,n_games,mtime,size,built_at)'
            ' VALUES(?,?,?,?,?,?,?,?,?,?)',
            (d, es_name, es_path, conf, note, _priority(d),
             len(rows), mt, sz, time.time()))
        built += 1

    if built == 0 and not force:
        # 没有任何系统被重建，去重结果不会变，跳过整表重扫
        conn.commit()
        conn.close()
        return {'ok': True, 'built': 0, 'skipped': skipped, 'failed': failed,
                'dup': -1, 'stale_systems': 0, 'total_dirs': total,
                'seconds': round(time.time() - t0, 3)}

    if progress:
        progress('dedup', 0, 1, '去重')
    dup_n = _dedup(conn)
    if progress:
        progress('gc', 0, 1, '清理失效条目')
    stale = _mark_stale(conn, set(d for d, _, _ in dirs))

    conn.commit()
    conn.close()
    return {'ok': True, 'built': built, 'skipped': skipped, 'failed': failed,
            'dup': dup_n, 'stale_systems': stale, 'total_dirs': total,
            'seconds': round(time.time() - t0, 3)}


def _dedup(conn):
    """按 (es_path, rom 文件名小写) 去重，优先 priority 小的 nas_dir"""
    conn.execute('UPDATE games SET dup=0')
    seen = {}
    rows = conn.execute(
        'SELECT g.id,g.es_path,g.rom_rel,g.title,s.priority,g.nas_dir'
        ' FROM games g JOIN systems s ON s.nas_dir=g.nas_dir'
        ' ORDER BY s.priority ASC, g.nas_dir ASC, g.id ASC').fetchall()
    dups = []
    for r in rows:
        rp = r['rom_rel'] or r['title']
        base = os.path.basename(rp.replace('\\', '/')).lower()
        key = (r['es_path'], base)
        if key in seen:
            dups.append(r['id'])
        else:
            seen[key] = r['id']
    if dups:
        conn.executemany('UPDATE games SET dup=1 WHERE id=?', [(i,) for i in dups])
    return len(dups)


def _mark_stale(conn, present):
    """NAS 上已消失的目录：标记失效，但不删（防断网误判）"""
    stale = 0
    for r in conn.execute('SELECT nas_dir FROM systems').fetchall():
        if r['nas_dir'] not in present:
            stale += 1
    return stale


def systems(conn, include_unsupported=False):
    """返回系统列表（按游戏数降序），带中文注释

    用一次 GROUP BY 聚合代替逐系统关联子查询：
    实测 68 个系统的关联子查询要 1.74 秒，改成 LEFT JOIN 聚合后降到毫秒级。
    """
    q = ('SELECT s.*, COALESCE(c.n, 0) AS avail'
         ' FROM systems s'
         ' LEFT JOIN (SELECT nas_dir, COUNT(*) AS n FROM games'
         '            WHERE dup=0 GROUP BY nas_dir) c ON c.nas_dir = s.nas_dir')
    if not include_unsupported:
        q += " WHERE s.conf IN ('ok','maybe')"
    q += ' ORDER BY avail DESC, s.nas_dir ASC'
    return conn.execute(q).fetchall()


def games_of(conn, nas_dir, offset=0, limit=100):
    return conn.execute(
        'SELECT * FROM games WHERE nas_dir=? AND dup=0'
        ' ORDER BY sort_by ASC, title ASC LIMIT ? OFFSET ?',
        (nas_dir, limit, offset)).fetchall()


def count_games(conn, nas_dir):
    r = conn.execute('SELECT COUNT(*) c FROM games WHERE nas_dir=? AND dup=0',
                     (nas_dir,)).fetchone()
    return r['c']


def get_game(conn, gid):
    return conn.execute('SELECT * FROM games WHERE id=?', (gid,)).fetchone()


def set_rom_bytes(conn, gid, nbytes):
    conn.execute('UPDATE games SET rom_bytes=? WHERE id=?', (nbytes, gid))
    conn.commit()


def stats(conn):
    t = conn.execute('SELECT COUNT(*) c FROM games WHERE dup=0').fetchone()['c']
    d = conn.execute('SELECT COUNT(*) c FROM games WHERE dup=1').fetchone()['c']
    s = conn.execute("SELECT COUNT(*) c FROM systems WHERE conf='ok'").fetchone()['c']
    sm = conn.execute("SELECT COUNT(*) c FROM systems WHERE conf='maybe'").fetchone()['c']
    return {'games': t, 'dup': d, 'sys_ok': s, 'sys_maybe': sm}
