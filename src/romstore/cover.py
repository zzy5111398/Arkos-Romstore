# -*- coding: utf-8 -*-
"""封面与简介的**后台**加载（0.5.0 卡片网格的前置条件）

## 为什么必须有这个模块

0.5.0 把列表换成了封面网格，一屏要画 12 张图。这些图全来自 NAS 的 SMB，
单张成本实测 0.1~0.3 秒：读 300 KB（JPEG 解码 + 纯 Python 最近邻缩放，
本机 libSDL2 缺 SDL_BlitScaled，见 sdl.ImageCache）。

  * **同步加载 = 界面冻住**。一屏 12 张就是 1~4 秒，期间手柄无响应、
    画面停在上一帧，用户会以为死机。
  * **简介更糟**。`metadata.pegasus.txt` 里 `description` 占全文 80~91%，
    一次读完就是几 MB；如果沿用 0.4.2 那种「选中哪款就现读一次」的做法，
    每换一款游戏都要重读几 MB —— 完全不能接受。

所以这里的两个加载器统一把「网络 + 解码」这类慢活挪到后台线程，
主线程只做 blit 和文字。卡片是一张张**渐进填满**的，不会卡。

## 线程分工的硬约束（改代码前必看）

1. **SDL surface 可以跨线程传递**：worker 造好 -> 加锁塞进字典 -> 主线程 blit。
   中间有锁交接，不存在两个线程同时碰同一个 surface，这是安全的。
   但调用方必须**接管所有权**，不能既拿走又留在 ImageCache 里 ——
   那样淘汰时会被二次 free，SDL 直接崩（所以用 `make_many`，它不进缓存）。
2. **TTF 字体渲染不是线程安全的**。所以本模块里**没有任何一行画字**，
   所有文字一律留在主线程。
3. worker 里的 `find_asset` / `parse_descriptions` 都是纯文件 IO + 字符串处理，
   线程安全。

## 一致性

两个加载器都带 `generation`（世代号）。切换系统时 `clear()` 会递增世代，
worker 发现自己手里的任务是旧世代的就直接丢弃 —— 避免「切了系统还把上一套
的封面贴上来」这种竞态。游戏 id 是 SQLite 的全局 AUTOINCREMENT，跨系统不重复，
所以就算漏判也不会贴错图，但丢弃更干净。
"""
import os
import json
import threading
import time

from . import config
from . import media as media_mod
from . import meta
from . import sdl

# 简介在内存里的上限。实测最大的 FC 系统 520 款、整份 metadata 几 MB，
# 全量留在内存完全可接受；这个上限只是防止遇到畸形文件时无限膨胀。
_DESC_MAX_CHARS = 8 * 1024 * 1024

# 统计 ROM 大小时要跳过的顶层目录：里面是刮削素材（图片/视频），
# 不是 ROM 本体。不跳过的话 PSP 光 media/ 就有上千个文件要 stat，
# 而它们对「这个游戏多大」毫无贡献。
_SKIP_DIRS = ('media', 'images', 'img', 'image', 'video', 'videos',
              'covers', 'savestate', 'saves')


class CoverLoader(object):
    """后台把 NAS 封面解码 + 缩放成小 surface，主线程只 blit

    每款游戏的**封面**只解一次码，产出 CARD_IMG_W x CARD_IMG_H 的缩略图，
    网格卡片和右栏预览区的占位**共用同一张**（按 1:1 贴，不缩放）。

    视频预览**不在这里**做 —— 它是「连续播放」而不是「加载资源」，
    生命周期和线程模型都不同，放在 vplayer.py 里。
    """

    # 缓存多少款游戏的缩略图。12 张可见 + 一点余量，内存约 2 MB。
    _LIMIT = 64
    # 队列上限：手柄快速翻页时别把几千款游戏全排进队列
    _QUEUE_MAX = 96

    def __init__(self, b, w, h):
        self.b = b
        self.w, self.h = w, h
        self._imgs = sdl.ImageCache(b)
        self._cache = {}        # gid -> surface（所有权归本加载器）
        self._video = {}        # gid -> 视频绝对路径或 None
        self._novideo = set()   # 已确认没有预览视频的 gid，不再重查
        self._order = []        # LRU 顺序
        self._queued = set()    # 已排队未完成
        self._queue = []        # 待处理任务（gid, row_dict）
        self._lock = threading.Lock()
        self._gen = 0
        self._stop = False
        self._t = threading.Thread(target=self._loop)
        self._t.daemon = True
        self._t.start()

    # ------------------------------------------------------------ 主线程侧
    def thumb(self, gid):
        """取已就绪的缩略图；还没加载好返回 None（调用方画占位块）"""
        with self._lock:
            s = self._cache.get(gid)
        return s if s else None

    def video(self, gid):
        """该游戏的预览视频绝对路径；None = 没有或还没查出来"""
        with self._lock:
            return self._video.get(gid)

    def ready(self, gid):
        """封面是否已经拿到结果（成功或失败），用于判断要不要显示「载入中」"""
        with self._lock:
            return gid in self._cache

    def pump(self, games, top, count, sel):
        """把可见卡片塞进加载队列（每帧调用，必须便宜）

        选中的那张插到队首 —— 用户最想先看到的就是它。
        再往后多排一屏做预取，翻页时基本已经是现成的。

        这里**只管封面**。视频预览由 vplayer 独立负责（它是常驻进程，
        生命周期跟「当前选中项」走，不是「这一屏卡片」走）。
        """
        end = min(len(games), top + count * 2)
        hi = []
        lo = []
        for i in range(top, end):
            hi.append(games[i]) if i == sel else lo.append(games[i])
        for g in hi:
            self._request(g['id'], g, front=True)
        for g in lo:
            self._request(g['id'], g)

    def clear(self):
        """切换系统时调用：丢弃全部缓存与排队任务"""
        with self._lock:
            self._gen += 1
            self._cache.clear()
            self._video.clear()
            self._novideo.clear()
            self._order = []
            self._queued.clear()
            self._queue = []

    def shutdown(self):
        self._stop = True

    def _request(self, gid, row, front=False):
        with self._lock:
            if gid in self._cache or gid in self._queued:
                return
            if not front and len(self._queue) >= self._QUEUE_MAX:
                return          # 队列满了就跳过，够不着就不排
            self._queued.add(gid)
            if front:
                self._queue.insert(0, (gid, self._gen, row))
            else:
                self._queue.append((gid, self._gen, row))

    # ------------------------------------------------------------ worker
    def _loop(self):
        while not self._stop:
            with self._lock:
                job = self._queue.pop(0) if self._queue else None
            if job is None:
                time.sleep(0.02)
                continue
            gid, gen, row = job
            surf = None
            try:
                # 素材目录必须用 **src_dir**，不能用 nas_dir。
                #
                # 实测（2026-10-12）：一个 ES 系统可能被拆到两个 NAS 目录
                # （PS1/PS1 hack、DC/DC hack、SS/SS plus 共用同一份 metadata，
                # ROM 文件互补分布），而**刮削素材跟着 ROM 文件走** ——
                # `PS1 hack/media` 恰好 60 个子目录、`DC hack/media` 36 个、
                # `SS plus/media` 510 个，与跨目录游戏的条数一一对应。
                #
                # 这里原本写的是 nas_dir，于是索引虽然已经把游戏解析到
                # 真正有文件的那个目录，素材却还在旧目录里找：
                #   SS 封面命中率 21.3%（160/751）、PS1 78.3%、DC 74.3%，
                # **恰好等于「本目录命中数」**，跨目录的那部分全军覆没。
                # 见 index._resolve / size 的同类修复。
                src_dir = row['src_dir'] or row['nas_dir']
                base = os.path.join(config.NAS_ROOT, src_dir)
                md = row['media_dir'] if 'media_dir' in row.keys() else None
                cover = media_mod.find_asset(base, row['rom_rel'] or '',
                                            'box_front', md)
                # 封面与视频同在 media/<stem>/ 下，一次 listdir 解决两个问题
                video = media_mod.find_video(base, row['rom_rel'] or '', md)
                if cover[0]:
                    surf = self._imgs.make_many(cover[0], [(self.w, self.h)])[0]
            except Exception:
                # 素材是锦上添花。任何异常都退化成「无封面 / 无预览」，
                # 绝不让 NAS 抖动把界面带崩。
                surf = None
                video = None
            with self._lock:
                self._queued.discard(gid)
                if gen != self._gen:
                    # 期间切了系统，这张图已经不该要了 —— 释放掉别泄漏
                    if surf:
                        self.b.sdl.SDL_FreeSurface(surf)
                    continue
                self._video[gid] = video
                if surf is None:
                    self._novideo.add(gid)
                self._store(gid, surf)
            # 只有选中的那一款才接着抽帧。
            # 放在锁外面：ffmpeg 是子进程，耗时 0.9 秒（实测），
            # 期间绝不能占着锁，否则主线程查表会被卡住。

    def _store(self, gid, surf):
        self._cache[gid] = surf or False
        self._order.append(gid)
        while len(self._order) > self._LIMIT:
            k = self._order.pop(0)
            old = self._cache.pop(k, None)
            if old:
                self.b.sdl.SDL_FreeSurface(old)


class DescLoader(object):
    """整份简介在后台**一次**读完，按需查表

    0.4.2 是「选中哪款就 `parse_descriptions` 现读一次」，每换一款游戏重读
    几 MB metadata，在 SMB 上是数秒级卡顿。0.5.0 改成：进系统时后台把
    **整个系统**的简介读进内存（一次 IO），之后切换选中项是 O(1) 查表。
    """

    def __init__(self):
        self._desc = {}
        self._loaded = False      # 本系统是否已读完
        self._loading = False
        self._gen = 0
        self._lock = threading.Lock()
        self._t = None

    def start(self, nas_dir, pairs):
        """pairs: [(gid, title), ...]；后台解析这些标题的简介"""
        with self._lock:
            self._gen += 1
            self._desc = {}
            self._loaded = False
            self._loading = True
            gen = self._gen
        if self._t and self._t.is_alive():
            return                  # 上一批还在跑，让它跑完（结果会被世代丢弃）
        self._t = threading.Thread(target=self._load, args=(nas_dir, pairs, gen))
        self._t.daemon = True
        self._t.start()

    def get(self, gid):
        """简介正文；还在加载返回 None"""
        with self._lock:
            if gid in self._desc:
                return self._desc[gid]
            return None if not self._loaded else ''

    def loading(self):
        with self._lock:
            return self._loading and not self._loaded

    def clear(self):
        with self._lock:
            self._gen += 1
            self._desc = {}
            self._loaded = False
            self._loading = False

    def _load(self, nas_dir, pairs, gen):
        out = {}
        total = 0
        try:
            mp = os.path.join(config.NAS_ROOT, nas_dir, config.METADATA_NAME)
            if os.path.exists(mp):
                want = set(t for _g, t in pairs)
                by_title = {}
                for g, t in pairs:
                    by_title.setdefault(t, []).append(g)
                got = meta.parse_descriptions(mp, want)
                for title, text in got.items():
                    total += len(text)
                    if total > _DESC_MAX_CHARS:
                        break
                    for g in by_title.get(title, ()):
                        out[g] = text
        except Exception:
            out = {}
        with self._lock:
            if gen != self._gen:
                return              # 已切系统，丢弃
            self._desc = out
            self._loaded = True
            self._loading = False


class SizeLoader(object):
    """ROM 文件大小在后台统计（0.5.0 右栏「大小」显示）

    ## 为什么不直接读 `games.rom_bytes`

    那个字段**只有安装时才会被写**（`index.set_rom_bytes`，见 install.py），
    扫库阶段 `build()` 存的是 None。也就是说：
      - 8210 款里只有「装过的那些」有值，其余全是空；
      - 而右栏恰恰最需要大小的是**还没装**的游戏（装了体积一眼就知道）。

    所以只能在显示时现取。代价是 NAS 在 SMB 上：

    ## 为什么必须后台 + 目录级批量 stat

    实测逐个 `os.path.getsize()` 是 **10.7 ms/款**（每一次都是 SMB 往返），
    主线程里同步做一款就掉 10 帧 —— 一屏 15 款直接卡死。

    但用 `os.scandir()` **一次遍历整个系统目录**、对每个 DirEntry 直接
    `e.stat()`（复用了目录读取时内核已经取回的 inode 信息，不额外往返），
    实测：
        PSP  491 款 -> 0.111 秒（0.2 ms/款）
        GBA  440 款 -> 0.100 秒（0.2 ms/款）
    比逐个 stat 快 **50 倍**，一个系统在后台线程里 0.1 秒就扫完。

    ## 键用相对路径，且不区分大小写

    部分系统（SS）目录是**分层**的：`009/Lunar 2 ... .chd`、`012/DEEP_FEAR...chd`，
    实测 751 款里 99 款带子目录、505 款在顶层找不到。所以这里：
      - 用**相对路径**做键（`os.path.relpath`），而不是纯文件名；
      - 查表时按同样规则拼键；
      - **大小写不敏感** —— NAS 与掌机文件名大小写常不一致，且 NAS 侧
        （ext4）区分大小写、掌机（exFAT）不区分，索引里的名字未必和
        scandir 返回的完全一致。这是本项目反复踩过的坑（见 batchmedia
        的 `_existing_cover`）。

    多碟游戏（is_multi）取**各碟之和**，与安装时传输的字节数口径一致。
    """

    def __init__(self):
        self._sizes = {}        # 相对路径(小写) -> 字节数
        self._gen = 0
        self._lock = threading.Lock()
        self._t = None
        self._dir = None        # 已扫描的 NAS 目录，用于判断缓存是否还对得上

    def start(self, nas_dir, extra_dirs=()):
        """后台扫描 nas_dir（以及它所有游戏实际用到的 src_dir）的大小表

        extra_dirs 一般传该系统下所有游戏出现过的 src_dir 集合 ——
        同一 ES 系统拆成多个目录时（SS / SS plus）必须都扫，
        否则落在兄弟目录里的游戏查不到大小。
        """
        extra = tuple(extra_dirs)
        with self._lock:
            self._gen += 1
            self._sizes = {}
            self._dir = nas_dir
            gen = self._gen
        if self._t and self._t.is_alive():
            return                  # 上一批还在跑，结果会被世代丢弃
        self._t = threading.Thread(target=self._scan, args=(nas_dir, gen, extra))
        self._t.daemon = True
        self._t.start()

    def lookup(self, game, total_cache=None):
        """返回该游戏的字节数；查不到返回 None

        total_cache 是个 dict，用来记住「这款是多碟、总量已算过」，
        避免每帧对多碟游戏重复求和。
        """
        gid = game['id']
        hit = total_cache.get(gid) if total_cache is not None else None
        if hit is not None:
            return hit[0]
        # 源目录要用 **src_dir**：同一 ES 系统拆到多个 NAS 目录时
        # （实测 SS / SS plus ROM 编号互补），大小表是按各自目录建的，
        # 用 nas_dir 会查不到。见 index._resolve。
        base = os.path.join(config.NAS_ROOT, game['src_dir'] or game['nas_dir'])
        rels = []
        if game['roms_json']:
            try:
                rels = json.loads(game['roms_json'])
            except ValueError:
                rels = []
        if not rels and game['rom_rel']:
            rels = [game['rom_rel']]
        total = 0
        got = False
        for r in rels:
            k = r.replace('\\', '/').lstrip('/').lower()
            with self._lock:
                sz = self._sizes.get(k)
            if sz is None:
                continue
            total += sz
            got = True
        val = total if got else None
        if total_cache is not None:
            total_cache[gid] = (val,)
        return val

    def clear(self):
        with self._lock:
            self._gen += 1
            self._sizes = {}
            self._dir = None

    def _scan(self, nas_dir, gen, extra_dirs):
        out = {}
        # **要连兄弟目录一起扫**：同一个 ES 系统可能被拆到多个 NAS 目录
        # （实测 SS / SS plus 共用一份 metadata，ROM 编号 1-160 与 161-752
        # 互补），索引已把每款游戏解析到真正有文件的那个 src_dir。
        # 只扫 nas_dir 的话，落在兄弟目录里的那几百款就查不到大小。
        roots = [nas_dir] + [d for d in extra_dirs if d != nas_dir]
        for root_dir in roots:
            self._scan_one(root_dir, out)
        with self._lock:
            if gen != self._gen:
                return              # 已切系统，丢弃
            self._sizes = out

    def _scan_one(self, nas_dir, out):
        try:
            root = os.path.join(config.NAS_ROOT, nas_dir)
            for dirpath, _dirnames, filenames in os.walk(root):
                # 跳过 media/ 等素材目录：里面全是图片视频，
                # 对 ROM 大小毫无贡献，却要额外 stat 几千个文件。
                rel_dir = os.path.relpath(dirpath, root)
                if rel_dir == '.':
                    rel_dir = ''
                if rel_dir.split(os.sep)[0] in _SKIP_DIRS:
                    _dirnames[:] = []
                    continue
                try:
                    entries = os.scandir(dirpath)
                except OSError:
                    continue
                with entries:
                    for e in entries:
                        try:
                            if not e.is_file():
                                continue
                            sz = e.stat().st_size
                        except OSError:
                            continue
                        if rel_dir:
                            key = (rel_dir + '/' + e.name).lower()
                        else:
                            key = e.name.lower()
                        # 同名文件累加（分层目录里可能有重名）。
                        # **跨目录不覆盖**：兄弟目录里若有同名文件，
                        # 本目录的优先（它才是这个 nas_dir 名副其实的内容）。
                        if key in out and nas_dir != self._dir:
                            continue
                        out[key] = out.get(key, 0) + sz
        except Exception:
            pass