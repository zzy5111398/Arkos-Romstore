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

## 两条后端：ffmpeg 管道（ArkOS）/ mpv 帧服务（AURKNIX）

0.6.0 移植到 AURKNIX/ROCKNIX 时发现那台机器有一个硬事实：

    ROCKNIX **没有 ffmpeg 命令行**（/usr/bin 下没有，全盘也没有），
    但**装了 mpv 0.36**（自带 ffmpeg 6.0.1 库 + rkvdec 硬解）。

所以本模块变成两条后端，由 create() 按平台与可用性挑一条：

  * ``VideoPlayer``    —— ffmpeg 管道。ArkOS 上跑了很久的那条，**一个字节没动**。
  * ``MpvFramePlayer`` —— mpv 帧服务。ROCKNIX 用，见下面那段说明。
  * ``NullPlayer``     —— 两个都缺时的空转后端（不做任何事，也不会忙等）。

### 为什么 mpv 那条反而"解码能力更强"

ffmpeg 那条为了保证 RK3326 软解不卡，用了 ``-skip_frame nokey``（只解 I 帧），
画面是"动起来但丢细节"。ROCKNIX 这台（RK3566）有真正的 VPU，
``mpv --hwdec=auto-safe`` 实测能选到 drm/rkvdec 硬解，于是**不需要再丢帧**。

### mpv 这条怎么把画面取出来

mpv 的 ``--vo=image`` 会把每一帧写成 ``<outdir>/00000001.jpg``，
worker 只读新出现的那一个文件、解码、blit —— 与 ffmpeg 管道等价，
但用的是 mpv 的完整解码链路（含硬解）。

### 为什么要 ``--video-sync=audio``（这条是整件事的关键）

``--vo=image`` 是**全速解码**的：实测 5 秒素材 2 秒就吐完 150 帧，
画面会变成快进。必须让 mpv 的播放时钟停下来等画面 —— 实测
``--ao=null --video-sync=audio`` 就够（null 声卡本身按真实时间走钟）：
5 秒素材耗时 6 秒、8 秒出 93 帧 / 16 秒出 189 帧，正好 12fps 实时。

想出声就把 ao 换成真实的声卡。**ao 用列表写法 ``alsa,pulse,null``**：
前面两个谁在就用谁，都拿不到就退到 null —— 而 null 依然提供实时时钟，
所以「没声音」这个失败模式**不会**把画面变成快进。
"""
import os
import signal
import subprocess
import threading
import time

from . import config
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

    # 有 ffmpeg 才用得上这个后端（create() 会先问 tool('ffmpeg')）。
    # 属性留着是为了和 MpvFramePlayer / NullPlayer 接口一致：
    # ui.py 只看这一个值决定"视频区该不该画那句正在播放"。
    available = True

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
                running = self._sig          # 当前在跑的进程用的是哪套参数
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
            # 想要的签名：换了游戏、或**声音开关**变了都要重起
            #
            # ⚠️ 0.6.0 修的真 bug：老写法是先 `sig = self._sig` 再判
            # `sig != self._sig` —— 同一个值和自己比，**永远为假**，
            # 于是 set_sound() 实际上什么都没发生（声音开关要等下次切
            # 游戏才生效）。现在改成"在读到的 running"和"当场算出的
            # want"比，语义才真的是"要不要换"。
            want = (gid, src, bool(self.sound))
            if (running != want or self._proc is None
                    or self._proc.poll() is not None):
                self._kill()
                if not self._start(gid, src):
                    # 起不来（没有 ffmpeg / 片源读不到）也要退避，
                    # 否则会每 50ms 重试一次 Popen，白烧 CPU
                    self._fail = self._fail + 1 if self._fail_gid == gid else 1
                    self._fail_gid = gid
                    self._retry_at = time.time() + max(
                        0.5, min(1.0 * self._fail, 5.0))
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


# ======================================================================
#  mpv 帧服务（AURKNIX / ROCKNIX）—— 见文件头的说明
# ======================================================================

_MPV_FPS = 12        # 右栏只有 218x123，12fps 足够看出"在动"（与 ffmpeg 那条取齐）
_MPV_Q = 70          # --vo-image-jpeg-quality（1~100，越大越清晰也越大）
# 文件刚落盘时可能还在被 mpv 写。12fps 下帧间隔 83ms，等 15ms 完全来得及；
# 等太久就变成白丢帧了。
_FILE_SETTLE = 0.015
# 起 mpv 到出第一帧的容忍时间。源在 NAS 上（rclone 首读偶尔慢），给宽一点。
_MPV_START_TIMEOUT = 15.0
# 起播失败后的最小退避 —— 不然会每 50ms 重试一次，白烧 CPU
_START_RETRY_MIN = 0.5

# mpv 的硬解开关。auto-safe = 「能硬解就硬解，不行自动回落软解」。
# ⚠️ 不能写 auto：ArkOS 上实测过，拿不到 /dev/dri 时 auto 会直接
# "Could not open codec" 一个字都不出；auto-safe 才会老实回落。
_HWDEC = 'auto-safe'


def _ao_sound_chain():
    """预览出声时的 ao 候选链

    平台档案给的 ao（ArkOS=alsa / ROCKNIX=alsa,pulse）后面**一定要补 null**：
    它是"声卡拿不到"时唯一还能提供实时时钟的后端，少了它画面会随音频
    初始化失败一起变成快进（见文件头）。
    """
    p = getattr(config, 'PREVIEW_AO', '') or ''
    chain = [x.strip() for x in p.split(',') if x.strip()]
    if 'null' not in chain:
        chain.append('null')
    return ','.join(chain)


def which(name):
    """在 PATH 里找可执行文件，找不到返回 ''

    只想要"存在且可执行"这一个语义，所以不绕 shutil（真机是 Python 3.7.5，
    shutil.which 是有的，但这里一行循环更直白）。
    """
    if not name:
        return ''
    if os.path.sep in name:
        return name if os.access(name, os.X_OK) else ''
    for d in (os.environ.get('PATH') or '/usr/bin:/bin').split(os.pathsep):
        if not d:
            continue
        p = os.path.join(d, name)
        try:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
        except OSError:
            continue
    return ''


_TOOL_CACHE = {}


def tool(name):
    """which() 的缓存版（worker 会反复问同一个名字）"""
    if name not in _TOOL_CACHE:
        _TOOL_CACHE[name] = which(name)
    return _TOOL_CACHE[name]


def _mpv_env():
    """起 mpv 用的环境：把私有库目录挂到 LD_LIBRARY_PATH **最前面**

    为什么（Mali 掌机实测，2026-10-14）：
      RK3326/RK3566 的 Mali 固件会把 `libgbm.so` / `libgbm.so.1` /
      `libgbm.so.1.0.0` 三个名字**全抢走**指向闭源 `libMali.so`。
      mpv 链接的是 `libgbm.so.1` → 解析到 libMali，而它不导出 mpv 要的
      `gbm_surface_create_with_modifiers`，于是加载即崩：
          mpv: symbol lookup error: undefined symbol: gbm_surface_create_with_modifiers
      那是**加载期**（lazy binding 被关）错误，所以换任何 `--vo` 都救不了，
      一个帧都出不来。很容易被误判成「视频格式不支持」。

      安装器会把 Mesa 的真 libgbm 单独解到 `<store>/lib/mpv/`（mpv-libgbm-fix.sh），
      这里只给**这一个 mpv 进程**用。

    ⚠️ 绝不能污染全局环境：Mali 是 ES/GPU 加速的依赖，让它加载 Mesa 的
      libgbm 会黑屏。所以是「复制 os.environ 再改」，不是 os.environ 赋值。
    """
    env = dict(os.environ)
    libdir = os.path.join(config.STORE_DIR, 'lib', 'mpv')
    if os.path.isdir(libdir):
        old = env.get('LD_LIBRARY_PATH', '')
        env['LD_LIBRARY_PATH'] = (libdir + (':' + old if old else ''))
    return env


def _is_tmpfs(path):
    """path 是不是落在 tmpfs 上

    ⚠️ 必须自己解析 /proc/self/mounts，不能只看路径名。它是
    `设备 挂载点 类型 选项 0 0`（**没有括号**）的格式，取第 3 个字段；
    按 `mount` 命令那种 `... on /x type t (rw,...)` 的格式写正则会永远失效
    —— 0.5.0 在 exFAT 只读判定上踩过一次，这里不重蹈。
    用「最长匹配的挂载点」判归属，覆盖 /x 与 /x/y 同时挂载的情形。
    """
    try:
        path = os.path.realpath(path)
        best, fstype = '', ''
        with open('/proc/self/mounts', 'r') as f:
            for line in f:
                f_ = line.split()
                if len(f_) < 3:
                    continue
                mp = f_[1].replace('\\040', ' ')
                if mp == path or path.startswith(mp.rstrip('/') + '/'):
                    if len(mp) >= len(best):
                        best, fstype = mp, f_[2]
        return fstype == 'tmpfs'
    except Exception:
        return False


_FRAME_DIR = []


def frame_dir():
    """mpv 写帧的临时目录

    **必须落在 tmpfs 上**：每秒 12 个 jpg，写 SD 卡会折寿，而且这堆文件
    只活几百毫秒。优先用平台档案给的 preview_tmp，其次 /dev/shm、/tmp；
    一个 tmpfs 都找不到才退到 store 目录里（那种情况也能跑，只是费卡）。
    """
    if _FRAME_DIR:
        return _FRAME_DIR[0]
    cands = []
    for c in (getattr(config, 'PREVIEW_TMP', ''), '/dev/shm', '/tmp'):
        if c and os.path.isdir(c) and c not in cands:
            cands.append(c)
    d = ''
    for c in cands:
        if not _is_tmpfs(c):
            continue
        t = os.path.join(c, 'romstore-prev')
        try:
            os.makedirs(t, exist_ok=True)
        except OSError:
            continue
        d = t
        break
    if not d:
        d = os.path.join(config.STORE_DIR, 'cache', 'frames')
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    _FRAME_DIR.append(d)
    return d


def _wipe_dir(d):
    """清空帧目录（切游戏 / 收摊时必须清，否则会把上一款的画面播进新款）"""
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        try:
            os.remove(os.path.join(d, n))
        except OSError:
            pass


def _wipe_upto(d, name):
    """删掉编号 <= name 的帧

    mpv 是 --loop-file=inf，帧号只增不减，不删的话 /tmp 会被几千个 jpg
    填满（实测 16 秒 189 个，一路涨）。删已经在写的那个也无妨：Linux
    unlink 之后文件的 fd 仍然有效，mpv 把这一帧写完就结束，不会报错。
    """
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        if n <= name:
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass


# 识别「本站帧服务」的命令行特征。用它来扫孤儿，**不能放宽**：
# 只认 `--vo-image-outdir=` 且值里带 romstore-prev 的那种。
_SWEEP_ARG = '--vo-image-outdir='


def _is_ours(argv):
    """argv 是不是本站起的 mpv 帧服务

    帧目录只有两种来源（见 frame_dir）：`<tmp>/romstore-prev` 或
    `<STORE_DIR>/cache/frames`，所以按这两个特征判归属足够精确，
    不会误伤用户自己开的、同样用 --vo=image 的 mpv。
    """
    for a in argv:
        if a.startswith(_SWEEP_ARG):
            # 先统一分隔符再比尾 —— 命令行里可能写成 `.../cache/frames/`
            v = a[len(_SWEEP_ARG):].replace('\\', '/').rstrip('/')
            return 'romstore-prev' in v or v.endswith('cache/frames')
    return False


def sweep_stale(quiet=True):
    """收掉上一次遗留的 mpv 帧服务进程，返回杀掉的个数

    ## 为什么必须有这个

    mpv 是界面进程的**子进程**，正常退出时会连带收掉（shutdown -> _kill）。
    但下面这两种情况收不掉，mpv 会被 init 收养（`ppid=1`）继续跑：

      * 界面进程吃到 SIGKILL（还有 0.6.0 之前的版本：SIGTERM 默认动作
        也会直接终止，连 `finally` 都不走 —— 这是真机上踩到的那次）；
      * 外层 `RomStore.sh` 自己被强杀。

    ## 残留的代价（真机实测，不是推测）

    残留 10 分钟就在 `/tmp`（488MB tmpfs）里攒了 **2741 个 jpg / 22MB**，
    而且它**仍然占着声卡** —— 新起的预览会拿到
    `Device or resource busy` 变成静音。实测一口气残留过 **3 个**：
    父进程被杀 3 次，每次留一个，三个 mpv 各自从 1 开始编号，写进同一个
    目录，读帧的一方拿到的是三份互相覆盖的乱序画面。

    ## 为什么扫 /proc 而不是 pkill

    因为要**按命令行里的参数**匹配，而 BusyBox 的 pkill `-f` 在这台机器上
    行为不稳：同一个模式，匹配 mpv 的 `--vo-image-outdir=...` 有效，
    匹配 `python3 /storage/.romstore/run.py` 却毫无反应（实测）。
    /proc 扫描是确定的，也不依赖 procps 装没装。

    只在 `MpvFramePlayer.__init__` 里调一次（那时本进程还没起任何 mpv，
    不会误杀自己）。
    """
    me = os.getpid()
    # /proc/<pid>/cmdline 是 \0 分隔的字节串
    try:
        pids = [n for n in os.listdir('/proc') if n.isdigit()]
    except OSError:
        return 0
    killed = 0
    for n in pids:
        pid = int(n)
        if pid == me:
            continue
        try:
            with open('/proc/%s/cmdline' % n, 'rb') as f:
                raw = f.read()
        except (OSError, IOError):
            continue
        if not raw:
            continue                      # 内核线程，cmdline 是空的
        argv = [x.decode('utf-8', 'replace') for x in raw.split(b'\0') if x]
        if not _is_ours(argv):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
            if not quiet:
                print('清理遗留的预览进程 pid=%d' % pid)
        except OSError:
            pass
    if killed:
        # 给它一点时间真正退出，再把帧目录扫干净
        time.sleep(0.2)
        _wipe_dir(frame_dir())
    return killed


class NullPlayer(object):
    """两个解码器都没有时的空转后端

    存在的意义：UI 不必到处写 `if vplayer`。`available=False` 时右栏只显示
    封面，不再假报"正在播放预览…"。

    **必须什么都不做**：早先（0.6.0 移植时）缺 ffmpeg 时 worker 会每 50ms
    重试一次 subprocess.Popen，白白烧 CPU —— 那正是加这个类的直接原因。
    """

    available = False
    sound = False

    def set(self, gid, src):
        pass

    def set_sound(self, on):
        pass

    def frame(self, gid=None):
        return None

    def playing(self, gid=None):
        return False

    def done(self):
        pass

    def shutdown(self):
        pass


class MpvFramePlayer(object):
    """用 mpv 的 `--vo=image` 当解帧服务，对外接口与 VideoPlayer 完全一致

    ui.py 只用到 5 个方法（set / frame / done / set_sound / shutdown）
    加 2 个属性（sound / available），所以两个类可以互换。

    ⚠️ 帧的交接（_pending/_free/done 的延迟释放）是从 VideoPlayer 抄过来的，
    **不是随手重复** —— 那套握手在真机上炸过三次（use-after-free /
    double free / 段错误）才收敛，注释里记着为什么。这里保持逐行一致，
    免得下次改一处忘一处。
    """

    available = True

    def __init__(self, b, w, h):
        self.b = b
        self.w, self.h = w, h
        self._imgs = sdl.ImageCache(b)
        self._lock = threading.Lock()
        self._pending = []       # worker 解出的帧，只增不减；主线程延迟回收
        self._frame_gid = None
        self._free = []
        self._gid = None
        self._want = None
        self._want_src = None
        self._sig = None         # 当前**在跑**的进程对应的签名
        self._proc = None
        self._stop = False
        self.sound = True
        self._retry_at = 0.0
        self._fail = 0
        self._fail_gid = None
        self._lastname = ''      # 已经读过的最大帧号
        self._first_at = 0.0     # 本次起播的时间（判"出第一帧"超时）
        self._dir = frame_dir()
        # 有没有做过"收尸"（见 _start）。**不能放在 __init__ 里**：
        # 无头模式（run.py --script 导出界面图）也会构造本对象，而那时
        # 真机上可能正开着一个商店在跑 —— 在 __init__ 里扫就会把人家
        # 正在用的 mpv 杀掉。（0.6.0 开发时就撞上过这个坑。）
        self._swept = False
        _wipe_dir(self._dir)
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
            self._free.extend(self._pending)
            self._pending = []
            self._frame_gid = None
            if len(self._free) > _RELEASE_LAG:
                for s in self._free[:-_RELEASE_LAG]:
                    self.b.sdl.SDL_FreeSurface(s)
                self._free[:] = self._free[-_RELEASE_LAG:]
        self._retry_at = 0.0
        self._fail = 0
        self._fail_gid = None

    def set_sound(self, on):
        """开关右栏预览的声音

        ao 是**建在 mpv 命令行里**的，所以切声音必须把当前这个 mpv 杀掉重建。
        这里只改标记并作废 `_sig`，worker 下一轮发现「在跑的 `_sig` 与想要的
        不一致」就自己重起（见 _loop 的判定）。
        """
        with self._lock:
            if bool(on) == bool(self.sound):
                return
            self.sound = bool(on)

    def frame(self, gid=None):
        """最新一帧的 surface；调用方**只读不接管所有权**（语义同 VideoPlayer）"""
        with self._lock:
            if not self._pending:
                return None
            if gid is not None and self._frame_gid != gid:
                return None
            return self._pending[-1]

    def playing(self, gid=None):
        with self._lock:
            if not self._pending:
                return False
            if gid is not None and self._frame_gid != gid:
                return False
            return True

    def done(self):
        """主线程每帧渲染完调一次：把足够「老」的帧回收掉（同 VideoPlayer）"""
        with self._lock:
            free, self._free[:] = self._free[:], []
            if len(free) > _RELEASE_LAG:
                for s in free[:-_RELEASE_LAG]:
                    self.b.sdl.SDL_FreeSurface(s)
                self._free[:] = free[-_RELEASE_LAG:]

    def shutdown(self):
        """停播、收 mpv、把帧目录扫干净

        收尾必须连**目录**一起清：帧是写在 tmpfs 上的，留着就是一堆没人要的
        jpg 占内存（直到重启才消失）。
        """
        self._stop = True
        self._t.join(timeout=3.0)
        self._kill(free_frames=True)
        _wipe_dir(self._dir)

    # ------------------------------------------------------------ worker
    def _loop(self):
        while not self._stop:
            time.sleep(0.03)
            with self._lock:
                gid, src = self._want, self._want_src
                running = self._sig
            if gid is None or not src:
                self._kill()
                continue
            now = time.time()
            if now < self._retry_at:
                continue
            want = (gid, src, bool(self.sound))
            if (running != want or self._proc is None
                    or self._proc.poll() is not None):
                self._kill()
                if not self._start(gid, src):
                    # 起不来（mpv 被杀 / 片源读不到）就退避，别 50ms 一次
                    self._fail = self._fail + 1 if self._fail_gid == gid else 1
                    self._fail_gid = gid
                    self._retry_at = time.time() + max(
                        _START_RETRY_MIN, min(1.0 * self._fail, 5.0))
                    continue
            if not self._pump(gid):
                self._kill()
                self._fail = self._fail + 1 if self._fail_gid == gid else 1
                self._fail_gid = gid
                self._retry_at = time.time() + min(1.0 * self._fail, 5.0)

    def _start(self, gid, src):
        """起 mpv：--vo=image 把帧写成 <dir>/00000001.jpg 序列

        命令行里每一条为什么是那个值，见文件头；尤其 `--video-sync=audio`
        与 `--ao=...,null` 这两条 —— 它们决定画面是实时播还是快进。
        """
        mpv = tool('mpv')
        if not mpv:
            return False
        if not self._swept:
            # 收掉上一次运行遗留的 mpv（被强杀时收不掉，见 sweep_stale）。
            # 必须在**第一次起自己的 mpv 之前**做：残留的那只在往同一个
            # 帧目录里灌，两边混在一起，读出来的画面是乱序的；它还占着
            # 声卡，会让新起的预览变成静音。
            # 只扫一次 —— 这也是"上一次的尸体"，跟之后每次切游戏无关。
            sweep_stale()
            self._swept = True
        _wipe_dir(self._dir)
        self._lastname = ''
        ao = _ao_sound_chain() if self.sound else 'null'
        cmd = [
            mpv, '--no-config', '--no-input-terminal',
            '--no-input-default-bindings', '--msg-level=all=error',
            '--vo=image', '--vo-image-format=jpg',
            '--vo-image-jpeg-quality=%d' % _MPV_Q,
            '--vo-image-outdir=%s' % self._dir,
            '--vf=scale=%d:%d,fps=%d' % (self.w, self.h, _MPV_FPS),
            '--loop-file=inf',
            '--video-sync=audio',
            '--ao=' + ao,
            '--hwdec=' + _HWDEC,
            src,      # 路径含中文与空格，必须是独立参数，绝不能拼进 shell 串
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=_mpv_env())
        except Exception:
            return False
        with self._lock:
            self._proc = proc
            self._gid = gid
            self._frame_gid = gid     # 新进程的帧都算这一款
            self._sig = (gid, src, bool(self.sound))
        self._first_at = time.time()
        return True

    def _pump(self, gid):
        """取一帧。返回 False = 这条流该退休了（起播超时）"""
        try:
            names = os.listdir(self._dir)
        except OSError:
            names = []
        if not names:
            # 一帧都还没出来。注意这里**不能**据此判定失败 ——
            # mpv 起进程 + 打开 NAS 上的源文件本来就要时间。
            return (time.time() - self._first_at) <= _MPV_START_TIMEOUT
        # 帧名是零填充序号，字符串序 == 数值序，直接比大小就是"比上次新"
        fresh = [n for n in names if n > self._lastname]
        if not fresh:
            return True
        fresh.sort()
        # 落后了就丢中间那些，直接追最新的一帧；同时只取"落盘够久"的，
        # 免得读到一半的文件（mpv 可能正在写）
        pick = ''
        for n in reversed(fresh):
            try:
                if (time.time()
                        - os.path.getmtime(os.path.join(self._dir, n))
                        >= _FILE_SETTLE):
                    pick = n
                    break
            except OSError:
                continue
        if not pick:
            return True
        self._lastname = pick
        data = None
        try:
            with open(os.path.join(self._dir, pick), 'rb') as f:
                data = f.read()
        except OSError:
            data = None
        # 读过的（含被丢掉的）立刻删 —— 见 _wipe_upto 的说明
        _wipe_upto(self._dir, pick)
        if not data:
            return True
        surf = self._imgs.load_bytes(data, self.w, self.h)
        if surf is None:
            return True                  # 这一帧坏了，进程还在，下一轮继续
        with self._lock:
            if self._proc is None or self._gid != gid:
                # 期间换了游戏：这帧属于上一款，丢掉
                self.b.sdl.SDL_FreeSurface(surf)
                return True
            self._pending.append(surf)
            # VideoPlayer 那边靠主线程的 done() 兜底就够了（它每帧都调）；
            # 这里再包一层上限：主线程万一卡在 NAS 的 IO 上，12fps 会一路堆。
            if len(self._pending) > _RELEASE_LAG + 2:
                self._free.extend(self._pending[:-1])
                self._pending = self._pending[-1:]
        return True

    def _kill(self, free_frames=False):
        """杀掉当前 mpv，并把帧目录清空

        `free_frames=True` 只在 shutdown 时用（此时 worker 已 join、主线程
        已停止渲染，全世界只剩一条线程），理由同 VideoPlayer._kill。
        """
        with self._lock:
            proc = self._proc
            self._proc = None
            self._gid = None
            self._sig = None
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)       # 不 wait 会留僵尸
            except Exception:
                pass
            # 只有真的收过进程才清目录 —— _loop 在「没选中任何游戏」时
            # 每 30ms 就会调一次 _kill()，无条件清就是白扫目录
            _wipe_dir(self._dir)
            self._lastname = ''
        if not free_frames:
            return
        with self._lock:
            olds = self._pending + self._free
            self._pending = []
            self._free = []
        for s in olds:
            self.b.sdl.SDL_FreeSurface(s)


def create(b, w, h):
    """按平台与可用性挑一个后端（UI 只认这个工厂，不认具体类）

    优先级：

      1. AURKNIX/ROCKNIX **且**装了 mpv -> mpv 帧服务
         （这台有 rkvdec 硬解，不必再像 ArkOS 那样只解 I 帧）
      2. 有 ffmpeg 命令行            -> 老的管道实现（ArkOS 一直跑的那条）
      3. 有 mpv                     -> mpv 帧服务
      4. 都没有                      -> NullPlayer（右栏只显示封面）
    """
    mpv = tool('mpv')
    ff = tool('ffmpeg')
    plat = (getattr(config, 'PLATFORM', '') or '').lower()
    if mpv and (plat == 'rocknix' or not ff):
        return MpvFramePlayer(b, w, h)
    if ff:
        return VideoPlayer(b, w, h)
    if mpv:
        return MpvFramePlayer(b, w, h)
    return NullPlayer()


def describe():
    """一行摘要：右栏内嵌预览走的是哪条后端（--probe / 自检 / 排障用）"""
    mpv = tool('mpv')
    ff = tool('ffmpeg')
    plat = (getattr(config, 'PLATFORM', '') or '').lower()
    if mpv and (plat == 'rocknix' or not ff):
        who = 'mpv 帧服务（--vo=image, hwdec=%s, %d fps）' % (_HWDEC, _MPV_FPS)
    elif ff:
        who = 'ffmpeg 管道（只解 I 帧, %d fps）' % _FPS
    elif mpv:
        who = 'mpv 帧服务（--vo=image, hwdec=%s, %d fps）' % (_HWDEC, _MPV_FPS)
    else:
        who = '不可用（既没有 ffmpeg 也没有 mpv）—— 右栏只显示封面'
    return '%s\n                工具: mpv=%s  ffmpeg=%s  落帧目录=%s' % (
        who, mpv or '（无）', ff or '（无）', frame_dir())
