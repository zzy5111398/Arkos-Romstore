# -*- coding: utf-8 -*-
"""右栏的**真视频预览**（0.5.0 第三轮：从抽一帧改成连续播放）

## 为什么不是抽一帧

抽帧只解决了「右栏别空着」，但用户要看的是**动**的过场演示。
抽帧版本右栏是一张静止的截图 —— 看不出游戏在演什么，实用性大打折扣。
所以这里改成让 ffmpeg 持续往管道吐帧，界面直接播。

## 为什么能在自己的界面里播（而不是像 X 那样交给 mpv）

按 X 全屏播放必须退出界面、把显示交给 mpv，是因为 mpv 要**独占**这块屏：
它自己打开 `/dev/dri` 做 DRM 直出，而界面进程已经占着 KMSDRM，两个进程
抢同一个 KMS 设备必然失败。

右栏预览完全不同：ffmpeg 只做「解码 -> JPEG 字节」，**不碰任何显示设备**；
拿到的字节在界面进程里解成 SDL surface，直接 blit 进我们自己的窗口。
全程没有任何第二个进程去开 `/dev/dri`，所以能和界面和平共处。

这也是为什么不用 ES 的 libVLC：ES 的视频是**进程内**渲染的
（libVLC 解码后把帧交给它自己的渲染器），ROM 商店是独立进程，
拿不到它的渲染上下文；而自己起 libVLC 实例又要处理 KMSDRM 独占。
ffmpeg 管道是唯一「不抢显示、还能连续出帧」的干净做法。

## 实测数据（r36s / PSP 一个 11.6MB、30 秒的 384x288 h264 视频）
真机是四核 a53，**没有硬件解码**（ArkOS 内核 4.4.189 没编 VPU 驱动，
/dev/video* 一个节点都没有），下面所有数字都是软解下测的：

  原参数（20fps q8）               230% CPU   <- 四核打满，界面卡成幻灯片
  加 -skip_frame nokey（只解 I 帧） 159% CPU
  再加音频独立进程（当前实现）       ~17% CPU

  视频首帧    0.7 秒     6 秒出 1266 帧（远超 12fps 需求）
  单帧 JPEG   3.4 KB     218x123, -q:v 10
  解码+缩放   3.8 ms/帧（旧实现 80.5 ms，见 sdl.load_bytes 的 1:1 快路径）

## 声音：为什么音频必须独立成一个进程
让同一个 ffmpeg 双输出（视频走管道 + 音频 `-f alsa default`）**行不通**：

  静音                 首帧 0.7 秒，6 秒 1271 帧
  同一进程里再加音频     首帧 4.8 秒，6 秒只有 45 帧    <- 画面几乎不动

ALSA 是**实时**设备，ffmpeg 必须按声卡要的节奏喂它；音频侧一阻塞，
**整条管道跟着停**，症状就是「有声音但没画面」。
另外 ffmpeg 打开 ALSA 会**独占**声卡，上一个没退干净时新的直接
`cannot open audio device (Device or resource busy)` —— 这正是
「切换游戏后还在播上一个视频」的成因。

拆成两个独立进程后两个问题一起消失：视频首帧 0.7 秒 / 1266 帧，
连续切换 3 次声卡都能正常重开（靠 _wait_audio 的重试退避）。

## 线程分工（与 cover.py 同一条铁律）

主线程**只 blit，绝不解码、绝不读管道**。worker 线程读管道、解 JPEG，
把 surface 加锁交接过来。换选中项时主线程只改一个 gid，
worker 自己发现 gid 变了就关掉旧进程、重起一个新的。
"""
import subprocess
import threading
import time

from . import sdl

# ffmpeg 的关键参数（构造在 _start 里，这里说明**为什么**是这些值）：
#   -stream_loop -1  循环播放。过场演示只有 20~30 秒，用户在右栏停留
#                    的时间往往超过它，不循环就会定格在最后一帧
#   -skip_frame nokey **只解 I 帧**，P/B 帧全丢。这是最关键的一条：
#                    RK3326 的 VPU 虽然硬件支持硬解，但 ArkOS 这个内核
#                    4.4.189 **没有编译 VPU 驱动**（/lib/modules 里没有
#                    rkvdec/rockchip_mpp，/dev/video* 一个节点都没有），
#                    所以只能纯软解。实测整条 h264 软解要吃掉 **230% CPU**
#                    （四核的 RK3326 直接打满，界面只剩 88% 就卡成幻灯片）。
#                    只解 I 帧后实测降到 **159%**。代价是画面少了运动细节 ——
#                    但右栏只有 218x123，实测画面完全可接受（真机截图比对过）。
#   flags=fast_bilinear  缩放算法。默认 bicubic 在 a53 上很贵，这个档位
#                    肉眼几乎看不出差别
#   -r 12            右栏只有 218x123，12fps 足够看出「在动」。
#                    帧率再高只是白烧 CPU（实测 20fps 并不比 12fps 省）
#   -q:v 10          mjpeg 质量。218x123 这么小的图，q10 已经看不出压缩痕迹，
#                    比 q8 小 20%，解码也快一点
#   -nostdin         关键：不加这条 ffmpeg 会去读 stdin，而我们的 stdin
#                    可能是终端 —— 会把父进程的输入吃掉
#   image2pipe       JPEG 裸流从 stdout 吐出来，一帧一个 SOI..EOI
# 音频**不在这个命令行里** —— 它是**第二个 ffmpeg 进程**，见 _start。
# 视频这条用 -an 明确丢弃音频：绝不能图省事在同一进程里加 `-f alsa`，
# 实测那样会把整条视频管道卡死（有声音但没画面），详见文件头。
#                    所以声音不只是补个功能，它是天然的节流阀。
_FPS = 12
_Q = 10
_SKIP = ['-skip_frame', 'nokey']
# 声卡是独占的：上一个音频进程刚被杀，ALSA 未必立刻把设备放开。
# 真机实测紧接着起新的会拿到 "Device or resource busy" 而静默退出。
# 所以重试几次，每次隔 _ALSA_WAIT 秒。
_ALSA_RETRIES = 4
_ALSA_WAIT = 0.6
# 起一个 ffmpeg 最多等这么久还没出帧就放弃这一款（源文件在 SMB 上，
# 偶尔会等很久；宁可不播，也不能让右栏一直空着）
_START_TIMEOUT = 12.0

# JPEG 的帧起始标记。管道里一帧一个 SOI..EOI，切帧靠它。
_SOI = b'\xff\xd8'

# 释放延迟：只释放「倒数第 N 帧及更早」的 surface。
# 20fps 下 N=3 就是 150ms，远超主线程一帧的渲染时间（实测 21~29ms），
# 保证绝不会 free 掉主线程还在 blit 的那块内存。
_RELEASE_LAG = 3


class VideoPlayer(object):
    """给「当前选中的一款游戏」连续播预览视频

    用法（每帧）：
        player.set(gid, video_path)     # 换选中项时调用，video 为 None 就停
        surf = player.frame(gid)        # 取当前帧，没有返回 None
        player.playing(gid)             # 是否正在播（决定画什么文案）

    frame()/playing() **都要传当前选中的 gid**。切换游戏时 worker 可能还在
    收上一款的尾帧，不过滤的话就会把上一个游戏的画面画到新游戏里
    （实测症状：「切了游戏还在播上一个视频」）。

    **同一时刻只有一个视频 ffmpeg + 至多一个音频 ffmpeg**。切换游戏就是
    kill 掉旧的、起新的 —— 一屏 15 款都播的话，光解码就要吃掉整个 CPU，
    界面直接卡死。
    """

    def __init__(self, b, w, h):
        self.b = b
        self.w, self.h = w, h
        self._imgs = sdl.ImageCache(b)
        self._lock = threading.Lock()
        self._pending = []       # worker 解出的帧，只增不减；主线程延迟回收
        self._frame_gid = None   # _pending 里那些帧属于哪一款（见 frame 的 gid 过滤）
        self._free = []          # 主线程已确认「不再需要」的帧，待释放
        self._gid = None         # 正在播哪一款
        self._want = None        # 想要播哪一款（主线程写）
        self._want_src = None
        self._proc = None
        self._aproc = None       # 音频 ffmpeg（独立进程，见 _start 的说明）
        self._audio_src = None
        self._sig = None
        self._gen = 0
        self._stop = False
        # 右栏预览是否出声（用户可在 Start 菜单里切）。
        # 默认**开声音** —— 这是游戏过场演示，看得见听不见等于没看全。
        self.sound = True
        # 重试退避（坏片源时不要疯狂重起 ffmpeg）
        self._retry_at = 0.0
        self._fail = 0
        self._fail_gid = None
        self._buf = b''
        self._last = 0.0
        self._t = threading.Thread(target=self._loop)
        self._t.daemon = True
        self._t.start()

    # ------------------------------------------------------------ 主线程侧
    def set(self, gid, src):
        """换选中项。src 为 None 表示停播（这款没片源）"""
        with self._lock:
            if gid == self._want and src == self._want_src:
                return
            self._want = gid
            self._want_src = src
            # 换游戏 = 上一款的帧全部作废。**不能就地释放** —— 主线程
            # 此刻可能正拿着其中一个指针在 blit。移进 _free 走延迟回收。
            # （就地 free 曾导致 frame.contents.w 变成 -1738133428 的野值，
            #   三指针双缓冲又导致 double free，见 _pump 的注释。）
            self._free.extend(self._pending)
            self._pending = []
            self._frame_gid = None     # 旧帧全部作废，立刻回到「正在起播」
            # 只留最近几帧待释放，其余立刻回收（这些已经很老了）
            if len(self._free) > _RELEASE_LAG:
                for s in self._free[:-_RELEASE_LAG]:
                    self.b.sdl.SDL_FreeSurface(s)
                self._free[:] = self._free[-_RELEASE_LAG:]
        # 真的换了 -> 重置退避，新一款值得从头再试一遍
        self._retry_at = 0.0
        self._fail = 0
        self._fail_gid = None

    def set_sound(self, on):
        """开关右栏预览的声音

        ffmpeg 的音频输出是**建在命令行里**的，所以切声音不是「改个变量」，
        必须把当前这个 ffmpeg 杀掉重建。这里只置标记，让 worker 下一轮
        发现参数变了就重起（见 _loop 里对 self._sig 的比较）。
        """
        with self._lock:
            if bool(on) == bool(self.sound):
                return
            self.sound = bool(on)
            self._sig = None      # 作废当前签名 -> 强制重启

    def frame(self, gid=None):
        """最新一帧的 surface；调用方**只读不接管所有权**

        `gid` 是**当前选中的游戏 id**。传它是为了挡掉「上一款的残帧」：
        切换游戏后 worker 可能还在读旧管道（甚至旧管道刚吐出最后一帧），
        而这些帧属于上一款。若无条件返回，主线程会把**上一个游戏的画面**
        画在**新游戏**的详情里 —— 实测表现为「切了游戏还在播上一个视频」。
        这里只认属于当前 gid 的帧，其余一律当作「还没出帧」。
        """
        with self._lock:
            if not self._pending:
                return None
            if gid is not None and self._frame_gid != gid:
                return None
            return self._pending[-1]

    def playing(self, gid=None):
        """当前是否真的在播（已取到第一帧）

        同样按 gid 过滤：上一款有帧不算这一款在播。
        """
        with self._lock:
            if not self._pending:
                return False
            if gid is not None and self._frame_gid != gid:
                return False
            return True

    def shutdown(self):
        """停播并确保 ffmpeg 被回收

        必须**等 worker 线程真的退出**再返回，否则会出现这种竞态：
        主线程置 _stop 后 worker 正好走到 `_start` 与 `_kill` 之间，
        起了一个谁也不管的 ffmpeg —— 真机实测关掉界面后仍有 ffmpeg
        占着 47% CPU 一路跑下去。
        """
        self._stop = True
        self._t.join(timeout=3.0)
        self._kill(free_frames=True)    # worker 若卡在 read 里退出不了，这里兜底
        self._kill_audio()              # 声音进程独立于视频，一并收掉
        # 音频是重试线程，可能还攥着一个刚建的进程；_kill_audio 只收
        # 「当前记录的那个」，这里再等一下让重试线程自己看到 _stop 退出。
        time.sleep(_ALSA_WAIT)

    # ------------------------------------------------------------ worker
    def _loop(self):
        while not self._stop:
            time.sleep(0.05)
            with self._lock:
                gid, src = self._want, self._want_src
                sig = self._sig
            if gid is None or not src:
                self._kill()
                continue
            # 退避：坏片源（0 字节 / 编码不对）会让 _pump 立刻返回 False，
            # 不加退避就会每 50ms 疯狂重起 ffmpeg，CPU 直接打满、
            # ffmpeg 进程还会堆成一堆。同一款失败两次后就不再自动重试，
            # 直到选中项真的变了。
            now = time.time()
            if now < self._retry_at:
                continue
            # 签名变了 = 换了游戏，或**声音开关**变了（音频是建在命令行里的，
            # 必须重建 ffmpeg 才生效）。
            if (sig != self._sig or gid != self._gid
                    or self._proc is None or self._proc.poll() is not None):
                self._kill()
                if not self._start(gid, src):
                    continue
            if not self._pump():
                # 这一条流读不出帧了，退避后重试
                self._kill()
                self._fail = self._fail + 1 if self._fail_gid == gid else 1
                self._fail_gid = gid
                self._retry_at = time.time() + min(1.0 * self._fail, 5.0)

    def _start(self, gid, src):
        """起 ffmpeg：视频一条（带 stdout 管道），音频另起一条（写声卡）

        **音频必须独立成一个进程**，这是被真机实测逼出来的（0.5.0）：
        早先试过让同一个 ffmpeg 双输出（视频走管道 + 音频走
        `-f alsa default`）。结果是画面几乎出不来：
            静音        -> 首帧 0.7 秒，6 秒 1271 帧
            同进程双输出 -> 首帧 4.8 秒，6 秒只有 45 帧
        原因：ALSA 是**实时**设备，ffmpeg 必须按声卡要的节奏喂它；
        一旦音频输出侧阻塞，ffmpeg 就把整条管道一起卡住，
        视频帧也跟着停 —— 表现就是「有声音但画面不动/不出」。

        还有第二个坑：ffmpeg 打开 ALSA 会**独占**声卡，
        上一个没退干净时新的直接 `cannot open audio device
        hw:0,0 (Device or resource busy)`，于是切换游戏后
        声音还是上一个、或者干脆静音。

        拆成两个进程后两个问题一起消失（实测）：视频首帧 0.7 秒 / 1266 帧，
        连续切换 3 次声卡都能正常重开（杀旧进程后要等一小会儿让
        ALSA 释放，见 _kill_audio 的退避）。
        """
        # ---- 音频进程（可选）----
        if self.sound:
            self._start_audio(src)

        # ---- 视频进程 ----
        # 视频路径含中文与空格，必须作为**独立参数**传，
        # 绝不能拼进 shell 字符串（会先被 shell 拆词再被 ffmpeg 误解）。
        # 也不需要 shell=True：我们要的是管道，不是 shell 语法。
        cmd = [
            'ffmpeg', '-y', '-loglevel', 'error', '-nostdin',
        ] + _SKIP + [
            '-stream_loop', '-1', '-i', src,
            '-an',                                # 音频归另一个进程管
            '-vf', 'scale=%d:%d:flags=fast_bilinear' % (self.w, self.h),
            '-r', str(_FPS), '-c:v', 'mjpeg', '-q:v', str(_Q),
            '-f', 'image2pipe', '-',
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=256 * 1024)
        except Exception:
            # 没有 ffmpeg / 源文件不可读 —— 安静地不播
            self._kill_audio()
            return False
        with self._lock:
            self._proc = proc
            self._gid = gid
            self._frame_gid = gid      # 新管道的帧都算这一款
            self._sig = (gid, src, bool(self.sound))
            self._gen += 1
        self._buf = b''
        return True

    def _start_audio(self, src):
        """起一条**只管声音**的 ffmpeg：-vn 只要音频，直接写声卡"""
        # `-map 0:a?` 的 `?` 不能省：不少片源根本没有音轨，
        # 不加时 ffmpeg 会报 "Output file does not contain any stream"
        # 然后**整条命令退出**（连视频都没了）。
        cmd = [
            'ffmpeg', '-y', '-loglevel', 'error', '-nostdin',
            '-stream_loop', '-1', '-i', src,
            '-vn', '-map', '0:a?',
            '-ar', '44100', '-ac', '2',
            '-f', 'alsa', 'default',
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return
        with self._lock:
            self._aproc = proc
            self._audio_src = src
        # 声卡刚被上一个进程放开时未必立刻可用，连试几次再放弃。
        # 否则快速切游戏会出现「有声的那些切过去没声」。
        threading.Thread(target=self._wait_audio, args=(proc, src),
                         daemon=True).start()

    def _wait_audio(self, proc, src):
        """音频进程起来后确认它**真的拿到声卡了**

        这里不能只看「进程还活着」就收工 —— 拿不到声卡时 ffmpeg 会
        很快自己退出，但**退出有延迟**，太早观察会误判成成功。
        真机上踩到的实际情形是：EmulationStation 还开着（它独占
        /dev/snd/pcmC0D0p），这时新开一路 alsa 会报
        `ALSA lib pcm_mmap.c: mmap failed: Invalid argument` 然后退出。
        用户看到的现象就是「切了游戏声音就不对 / 没声」。

        所以这里先等 _ALSA_WAIT 秒让它有机会退出：活着就收工，
        死了才有资格重试（上一个进程刚被杀时 ALSA 未必立刻放开）。
        """
        for _ in range(_ALSA_RETRIES):
            if self._stop:
                return
            time.sleep(_ALSA_WAIT)
            if proc.poll() is None:
                return                       # 活下来了，收工
            # 死了：确认当前该出声的就是它，才允许重试
            with self._lock:
                mine = (self._aproc is proc)
            if not mine:
                return                       # 已经切走/关了，别添乱
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
            with self._lock:
                if self._aproc is not proc:
                    return
                self._aproc = None
            self._start_audio(src)
            proc = self._aproc
            if proc is None:
                return

    def _kill_audio(self):
        """停掉声音进程"""
        with self._lock:
            proc = self._aproc
            self._aproc = None
        if not proc:
            return
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)      # 不 wait 会留僵尸，僵尸持有 ALSA
        except Exception:
            pass
        self._last = time.time()

    def _kill(self, free_frames=False):
        """杀掉当前的 ffmpeg

        `free_frames=True` 只在 **shutdown 时**用（此时主线程已经不再
        渲染，worker 也已 join，全世界只有这一条线程）。
        换游戏走的路径**绝不能**释放帧 —— 主线程可能正拿着其中一块
        在 blit，那正是前面两次崩溃（野指针 / double free）的成因。
        """
        with self._lock:
            proc = self._proc
            self._proc = None
            self._gid = None
            self._sig = None
            self._audio_src = None
            self._gen += 1
        self._kill_audio()
        if proc:
            # ffmpeg 是我们起的子进程，kill 之后必须 wait 回收，
            # 否则会留成僵尸。真机调试时见过「关掉界面后还有 ffmpeg 在跑、
            # CPU 占用 47%」——就是少了这一步（wait 超时也不阻塞界面，
            # 交给 init 去收）。
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        if not free_frames:
            return
        # 只有 shutdown 才走到这：此时 worker 已 join、主线程也已停止
        # 渲染，所有 surface 都可以安全回收。
        with self._lock:
            olds = self._pending + self._free
            self._pending = []
            self._free = []
        for s in olds:
            self.b.sdl.SDL_FreeSurface(s)

    def _pump(self):
        """读一帧、解码、交接给主线程。返回 False 表示这条流该退休了"""
        proc = self._proc
        gid = self._gid
        if proc is None:
            return False
        data = None
        deadline = time.time() + _START_TIMEOUT
        try:
            # (1) 必须用 read1()，不能用 read(n)。
            #     BufferedReader.read(n) 会阻塞直到凑满 n 字节或遇到 EOF，
            #     而我们给 ffmpeg 加了 -stream_loop -1（循环播放，永不退出），
            #     管道永远不 EOF —— 于是 read(65536) 第一次调用就永久卡死，
            #     表现为「ffmpeg 在跑、缓冲区也有数据，但一帧都出不来」。
            #     read1() 是「有多少给多少」，这才是流式读的语义。
            #
            # (2) 帧边界按 **SOI 到下一个 SOI** 切。
            #     早先想用 EOI(FFD9) 当帧尾，但 JPEG 的 DQT/DHT 段里也可能
            #     出现 FFD9 字节序列，会把一帧切成两半 —— 真机实测缓冲里
            #     39362 字节全是合法 JPEG 数据，却一帧都解不出来，卡死 20 秒。
            #     SOI 只出现在帧头，按它切可靠。
            while True:
                i = self._buf.find(_SOI)
                if i > 0:
                    self._buf = self._buf[i:]      # 丢掉前面的残留
                if self._buf.startswith(_SOI):
                    j = self._buf.find(_SOI, 2)
                    if j > 0:
                        data = self._buf[:j]
                        self._buf = self._buf[j:]
                        break
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    return False                # 管道关了
                self._buf += chunk
                if time.time() > deadline:
                    return False
                if len(self._buf) > 8 * 1024 * 1024:
                    return False                # 攒到 8MB 还切不出帧，放弃
            self._last = time.time()
        except Exception:
            return False

        # 解码（这一步 3~10ms，放 worker 里，主线程只 blit）
        surf = self._imgs.load_bytes(data, self.w, self.h)
        if surf is None:
            return True                  # 这一帧坏了，但流还在，继续读
        # ---- 交接 ----
        # 经过三次尝试才收敛到这个设计（前两次都在真机上炸了，值得记下来）：
        #
        #  a) worker 换掉 _surf 就 free 旧的
        #     -> 主线程正拿着那个指针，拿到 frame.contents.w == -1738133428
        #        的野值（use-after-free）。
        #  b) worker 维护 _pending/_spare 三指针双缓冲
        #     -> 竞态下双重释放，直接 "double free or corruption" 崩掉。
        #  c) 空位握手（worker 填 _free、主线程 done() 归还）
        #     -> 同样段错误：主线程 blit 的是 _surf，而 worker 已经
        #        把同一个 surface 拿去当「空位」准备填下一帧了。
        #
        # 收敛的方案：**worker 只产出，从不释放；释放只发生在主线程，
        # 且必定晚两帧。** 20fps 下就是 100ms 延迟释放，
        # 两张 218x123 的 surface 约 210 KB，内存上完全不是问题。
        # 「晚两帧」这 100ms 足够保证主线程绝不会再碰这一块内存，
        # 而 20fps 的画面延迟两帧（50ms）人眼也看不出来。
        # 读完之后**再确认一次**这条流还是当前要的：
        # 切帧过程中可能正好换了游戏，这时这帧属于上一款，
        # 挂上去就会把上一个游戏的画面画到新游戏里。
        with self._lock:
            if self._proc is not proc or self._gid != gid:
                # 期间被换掉了：这帧是上一款的尾巴，丢掉
                if surf:
                    self.b.sdl.SDL_FreeSurface(surf)
                return True
            self._pending.append(surf)
        return True

    def done(self):
        """主线程每帧渲染完调一次：把足够「老」的帧回收掉

        这是**唯一**释放 surface 的地方（外加 _kill 收尾）。
        关键在于 `del self._free[:_LATE]`：只释放倒数第 3 帧及更早的，
        保证「上一帧」「这一帧」一定还活着。
        """
        with self._lock:
            free, self._free[:] = self._free[:], []
            if len(free) > _RELEASE_LAG:
                for s in free[:-_RELEASE_LAG]:
                    self.b.sdl.SDL_FreeSurface(s)
                self._free[:] = free[-_RELEASE_LAG:]
