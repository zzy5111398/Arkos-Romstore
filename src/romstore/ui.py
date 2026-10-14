# -*- coding: utf-8 -*-
"""SDL2 中文界面

屏幕 640x480（R36S / X35S）。**游戏页是双栏**（0.5.0 起）：
  ┌ 顶栏 44px  系统名 + 「第几张/共几张 款」
  ├ 卡片网格区      │ 右栏详情区（常驻 DETAIL_W=232）
  │ 封面卡片 4x4   │ 游戏标题（两行）
  │ 选中项高亮边框   │ 已安装 / 多碟 标签
  │ 已装的有绿点     │ 视频预览区（16:9 画面）
  │                │ 简介正文（右摇杆上下滚动）
  └ 底栏 38px  按键提示

系统页仍是单列列表（只几十行，不需要网格）。

**卡片上不写标题**（0.6.0，用户要求）：原来标题占卡片底部两行，紧贴在封面正下方、
底色又与封面边框同色，看着像一条压在封面底部的黑带。去掉后封面撑满整格
（本 NAS 的封面原图就是正方形，1:1 铺满不留白），游戏名由右栏详情栏负责。
腾出来的高度没有浪费 —— 卡片从 5x4 的 71px 变成 **4x4 的 91px**，
同时顶栏/底栏各收窄 8/6px 把可用高度从 378 抬到 392。
唯一的例外是「确实没有封面」的游戏：那时仍在格子里居中写名字兜底。

**选中态只用 2px 亮边框表示，不铺色块**（0.5.0 二轮）：早先在选中卡片下面
铺一片蓝底，等于拿蓝方片盖住封面 —— 封面恰恰是浏览时最有用的识别信息。

**正文用 13px 小字**（f_tiny）：16px 在 640x480 上偏大，右栏简介一屏只剩 8 行，
能看到的字太少。13px 是文泉驿仍能认读汉字的下限。

封面与简介**不在主线程加载**（单张封面从 NAS 读 + 解码实测 0.1~0.3 秒，
一屏 16 张同步加载会冻住界面）。见 cover.py。

预览视频有两条路，见 vplayer.py：
  * 右栏常驻预览（**在界面进程里**出画面）——
    ArkOS 走 ffmpeg 管道；AURKNIX/ROCKNIX 没有 ffmpeg 命令行，改走
    mpv 的 --vo=image 帧服务，并带 rkvdec 硬解。
  * 按 X 全屏预览 —— 退出本进程，由外层 RomStore.sh 用 mpv 播，
    回来后用 `--resume <id>` 把网格光标恢复到那张卡片（见 request_play/goto_game）。
    请求通过退出码 42 + 请求文件传递。

按键（掌机没有键盘，全部动作都有手柄绑定）：
  游戏页  十字键/左摇杆  网格内二维移动（到边停住，**不绕回**）
          右摇杆上下     滚动右栏简介（**独立于网格导航**，互不干扰）
          A            安装          X            全屏播放预览视频
          Y            卸载          B            返回系统页
          L1 / R1      上/下翻页     Start        菜单（重扫 / 换源 / 退出）
  系统页  十字键  上下移动    A  进入    B  返回

**A 和 X 在所有层级含义一致**（0.4.2 时 X 在列表页是安装、详情页才是播放，
两个层级含义不同很容易按错；0.5.0 没有详情页了，统一掉）。

软键盘（screen='kb'，掌机没键盘，所有文本都靠它输入）：
  十字键/左摇杆  选字符       A            输入该字符
  Y              退格          X            完成
  L1 / R1        切换字符页    B            取消

  —— 下面这些键盘键只用于桌面调试，掌机上按不到 ——
  Enter 确认 / Esc 返回 / PageUp·PageDown 翻页 / I 安装 / D 卸载
  B·N 滚动简介（等价于右摇杆上下） / M 菜单 / R 重扫 / Q 退出
"""
import os
import threading
import time

from . import config
from . import cover as cover_mod
from . import index as idx
from . import install as inst
from . import sdl
from . import source as src
from . import vplayer as vplayer_mod

W, H = 640, 480
# 顶栏 / 底栏高度。0.6.0 各收窄 8px / 6px：卡片去掉标题后要的是**更大的封面**，
# 而可用高度是硬约束（4 行卡片时卡宽 = (可用高 - 3*间距) / 4）。
#   378 -> 392  让 4 列 4 行能摆下 91px 的卡片（原来只够 88px）
HEADER_H = 44
FOOTER_H = 38
ROW_H = 30

# ---- 卡片网格 + 右栏详情（0.5.0 布局，0.6.0 去掉卡片标题）------------------
#  顶栏
# ┌──────────────────────────────┬─────────────┐
# │ 卡片网格（**只有封面**）        │ [标签] 游戏标题│
# │                              │ 视频预览区   │
# │                              │ 简介（可滚动）│
# └──────────────────────────────┴─────────────┘
#  底栏
DETAIL_W = 232      # 右栏宽度
PAD = 10            # 网格左边距
CARD_GAP = 8        # 卡片间距
# 卡片上**不写标题**（用户明确要求，0.6.0）。原来标题占卡片底部两行，
# 但它紧贴在封面正下方、底色又与封面边框同色，看着像一条压在封面底部的
# 黑带。去掉之后封面撑满整格，游戏名由右栏详情栏负责。
# 例外的只有「确实没有封面」的那些 —— 那时仍在格子里写名字兜底，
# 否则一整格黑方块认不出是哪款（见 _draw_card）。
CARD_TEXT_H = 0
# 卡片封面区取 **1:1**（去掉标题后卡片就是正方形）。
# 本 NAS 的封面原图实测就是正方形（480x480 / 600x600），1:1 正好铺满，
# 既不留白也不变形。
CARD_ASPECT = 1
# 网格"要多大"，见 _grid_metrics()：
#   卡片尺寸与"每屏几张"是一对矛盾（卡越大每屏越少），
#   这里先钉死每屏**至少**这么多张，然后在满足它的方案里挑卡片最大的。
#   实测 640x480 下这个值给出 **4 列 x 4 行 = 16 张，卡宽 91px**。
#   想再大就把它调小：调成 9 会得到 3x3 / 124px，调成 12 得到 4x3 / 91px。
GRID_TARGET_CARDS = 16
# 卡宽下限。低于它就别再往下试更小的卡片了（会排得像马赛克）
CARD_MIN = 48
# 右栏视频预览区尺寸。视频按 16:9 铺满右栏。
# 高度 123 是量出来的：右栏共 376px，标题(最多两行 48)+视频(123)+分隔(10)
# 之后简介还剩约 175px = 11 行，够看也够滚。再高就把简介挤没了。
# 宽度 = DETAIL_W(232) - 左边距(10) - 分隔线占位(4) = 218。
# ⚠️ 这个 218 必须和 ffmpeg 的 scale 目标一致，否则画面会被横向裁掉一条。
PREVIEW_W = 218
PREVIEW_H = 123

# **封面缩略图的规范化尺寸**。后台加载器按这一个尺寸解码网格封面，
# 一款游戏只解码一次，右栏预览区在视频就绪前也复用它做占位。
# 取值 = 4 列布局下卡片的内区尺寸（卡宽 91 - 左右各 2px 边框 = 87），见 _grid_metrics()。
# ⚠️ 换布局（改 GRID_TARGET_CARDS / DETAIL_W）要跟着改这两个数，
# 否则封面会被放大 -> 糊。缩略图是**正方形**，所以宽高相同。
# 内存：87*87*4 = 30KB/张，缓存上限 8 张 -> 240KB，可以忽略。
# ⚠️ 视频帧是**另一个尺寸**（PREVIEW_W x PREVIEW_H）——两者共用一个尺寸
# 会把画面拉变形。
CARD_IMG_W = 87
CARD_IMG_H = 87

# ---- 「播放预览视频」的进程间协议 -------------------------------------------
# 本进程占着 KMS/DRM 显示，没法在自己进程里解视频（掌机没有 python 解码器绑定），
# 所以由外层 RomStore.sh 负责播放。约定：
#   1. 退出码 EXIT_PLAY(42)
#   2. 请求文件 PLAY_REQ 两行：第 1 行视频绝对路径，第 2 行游戏 id
# RomStore.sh 见到 42 就播第 1 行那个文件，播完用 --resume <第 2 行> 把界面拉回来。
EXIT_PLAY = 42
PLAY_REQ = '/tmp/romstore-play.req'
# 留 26px 给「当前位置 / 总数」指示条，避免与最后一行重叠
VIS_ROWS = (H - HEADER_H - FOOTER_H - 26) // ROW_H

# 主题（ARGB）
BG = 0xFF1E1E1E
HEADER_BG = 0xFF2A2A2A
SEL_BG = 0xFF35507A
SEL_EDGE = 0xFF5C8FD6
TEXT = (232, 232, 232)
TEXT_DIM = (150, 150, 150)
TEXT_MUTE = (110, 110, 110)
ACCENT = (255, 186, 92)
GREEN = (138, 214, 138)
# rect()/fill() 要的是 ARGB 整数，而 ACCENT/GREEN/WARN 这些是 RGB 元组
# （txt() 用的）。直接混用会 TypeError，真机上才发现 —— 所以统一走 argb()。
def argb(rgb, a=255):
    return ((a << 24) | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2])

WARN = (240, 200, 110)
RED = (238, 122, 122)
WHITE = (255, 255, 255)

# 进度阶段 -> 中文
PHASE_CN = {
    'check': '检查空间',
    'copy': '复制文件',
    'm3u': '生成播放列表',
    'art': '复制封面',
    'gamelist': '写入游戏列表',
    'done': '完成',
    'scan': '扫描 NAS',
    'dedup': '去重',
    'gc': '清理失效条目',
    'mount': '连接共享',
}

# 软键盘字符页。逗号故意不提供 —— cifs 的 -o 选项串是逗号分隔的，会串味。
KB_PAGES = [
    [list('1234567890'), list('-.:/_')],                        # 0 数字（IP 常用）
    [list('qwertyuiop'), list('asdfghjkl'), list('zxcvbnm')],   # 1 小写
    [list('QWERTYUIOP'), list('ASDFGHJKL'), list('ZXCVBNM')],   # 2 大写
    [list('@#$%&*+=?!'), list('-_.~;:\'"'), list('()[]{}<>/')],  # 3 符号
]
KB_NAMES = ['数字', '小写', '大写', '符号']

# 「修改商店源」页的可编辑字段
SRC_FIELDS = [
    ('host', '主机'),
    ('share', '共享名'),
    ('user', '账号'),
    ('password', '密码'),
]
# 该页下方的动作项
SRC_ACTIONS = [
    ('scan', '扫描局域网'),
    ('save', '保存并连接'),
    ('back', '返回'),
]


def wrap(text, fonts, maxw, maxlines=99):
    """按像素宽度对中文做换行（中文按字符切，保留原文的换行）

    **宽度是累加的，不重算整串**。早先每个字符都调 `fonts.width(cur + ch)`
    把已累积的整行重新量一遍，那是 O(n^2)；真机实测一条 40 字简介要
    **30 毫秒**，而右栏每帧都要重排一次 —— 30fps 下光这一项就吃掉九成 CPU。
    改成逐字符量自身宽度再累加，同一段文字降到 0.1 毫秒量级。
    """
    lines = []
    for para in text.split('\n'):
        para = para.strip()
        if not para:
            continue
        cur = ''
        w = 0
        for ch in para:
            cw = fonts.width(ch)
            if w + cw <= maxw:
                cur += ch
                w += cw
            else:
                if cur:
                    lines.append(cur)
                cur = ch
                w = cw
                if len(lines) >= maxlines:
                    return lines[:maxlines]
        if cur:
            lines.append(cur)
        if len(lines) >= maxlines:
            break
    return lines[:maxlines]


class App(object):

    def __init__(self, headless=False, dumpdir=None, size=(W, H)):
        self.b = sdl.Bind()
        self.headless = headless
        self.dumpdir = dumpdir
        self.ww, self.wh = size

        font_path = config.FONT_CJK
        if not os.path.exists(font_path) and os.path.exists(config.FONT_CJK_ALT):
            font_path = config.FONT_CJK_ALT
        self.f_title = sdl.Fonts(self.b, font_path, 24)
        self.f_row = sdl.Fonts(self.b, font_path, 20)
        self.f_small = sdl.Fonts(self.b, font_path, 16)
        self.f_big = sdl.Fonts(self.b, font_path, 30)
        # f_tiny（13px）：0.5.0 二轮按反馈加的。16px 在 640x480 上做正文偏大，
        # 卡片标题两行就占 32px、右栏简介一屏只剩 8 行 —— 一屏能看到的字太少。
        # 13px 是文泉驿在这个分辨率下**仍能认读汉字**的下限（12px 开始糊）。
        self.f_tiny = sdl.Fonts(self.b, font_path, 13)

        self.canvas = sdl.Canvas(self.b, self.ww, self.wh)
        self.imgs = sdl.ImageCache(self.b)
        self.display = None
        if not headless:
            self.display = sdl.Display(self.b, self.ww, self.wh)
            # KMSDRM 实际屏幕尺寸可能与请求不同，按实际重建画布
            if (self.display.w, self.display.h) != (self.ww, self.wh):
                self.canvas.free()
                self.ww, self.wh = self.display.w, self.display.h
                self.canvas = sdl.Canvas(self.b, self.ww, self.wh)

        self.inp = sdl.Input(self.b)
        self.inp.open_controllers()

        self.screen = 'systems'
        self.sys_rows = []
        self.sys_sel = 0
        self.sys_top = 0
        self.games = []
        self.game_sel = 0
        self.game_top = 0
        # 0.5.0：详情不再是独立页面，而是网格右栏的常驻内容，
        # 所以没有 detail_game / detail_scroll 这类状态了。
        self.msg = None
        # Start 菜单（Start/Select 打开）：掌机没有键盘，退出/重扫/换源都收在这里
        self.menu_open = False
        self.menu_sel = 0
        self.menu_items = [('refresh', '重扫 NAS'),
                           ('source', '修改商店源'),
                           ('sound', '预览声音：开'),   # 标签随状态变，见 render_menu
                           ('quit', '退出商店')]

        # 商店源设置（source.json 是唯一真相，开机挂载脚本也读它）
        self.source = src.load()
        self.src_sel = 0
        self.src_back_screen = 'systems'
        self.kb = None          # 软键盘状态
        self.netscan = None     # 局域网扫描状态

        self.status = ''
        self.scanning = False
        self.progress = None
        # ---- 传输速度计（进度页的「速度 xx MB/s」用）--------------------------
        # 样本来自 install.py 每写满 512KB 回调一次，**由 worker 线程写、
        # 主线程读**，所以必须加锁：主线程正在遍历样本列表时 worker 恰好
        # pop 掉一个，就是 IndexError 蹦在渲染里（画面直接崩）。
        self._sp_lock = threading.Lock()
        self._sp = []            # [(时刻, 已传字节)]，滑动窗口样本
        self._sp_hist = []       # 最近若干次瞬时速度，画成下面的小柱状图
        self._sp_at = 0.0        # 最后一次采样的时刻
        self.t_start = 0.0       # 本次任务开始时刻（算已用时间）
        self.worker = None
        self.worker_result = None
        self.running = True
        self.exit_code = 0          # 非 0 时由 run.py 透传给外层 shell（见 EXIT_PLAY）
        self.conn = None
        self.installed_cache = {}
        self.desc_lines_cache = {}   # (游戏id, 宽, 行数) -> 已排好版的简介行
        # 右栏简介的滚动位置（首行索引）。右摇杆上下 / B、N 键改变它；
        # 换游戏时归零，否则从上一个游戏滚到一半的位置看下一个游戏会莫名其妙。
        self.desc_scroll = 0
        # 卡片/右栏标题的排版缓存（游戏id, 卡宽, 'card'|'detail'）-> 行列表。
        # 文本固定、宽度固定，排一次就够；不缓存的话每帧要逐字量像素宽度。
        self.card_title_cache = {}
        # 后台加载器：封面解码/缩放与简介解析都在 worker 线程，
        # 主线程只 blit 和查表（详见 cover.py 顶部的线程约束说明）
        self.covers = cover_mod.CoverLoader(self.b, CARD_IMG_W, CARD_IMG_H)
        self.descs = cover_mod.DescLoader()
        # 右栏视频**播放器**（vplayer）：随「当前选中项」走，跟封面加载器
        # 完全独立 —— 封面是「加载一次的资源」，视频是「一直在放的内容」。
        #
        # ⚠️ 必须用工厂挑后端，不能直接 new VideoPlayer（0.6.0）：
        #   ArkOS   有 ffmpeg 命令行 -> ffmpeg 管道（老实现）
        #   ROCKNIX **没有 ffmpeg**，只有 mpv -> mpv 帧服务（带硬解）
        #   两个都没有                 -> NullPlayer（什么都不做）
        # 早先写死 VideoPlayer，在 ROCKNIX 上会退化成"每 50ms 失败重试一次
        # 的忙等"、而右栏还一直显示「正在播放预览…」在骗人。
        self.vplayer = vplayer_mod.create(self.b, PREVIEW_W, PREVIEW_H)
        # 「X 全屏预览」是交给外层 RomStore.sh 用 mpv 播的，所以这条路能不能走
        # 取决于**本机有没有 mpv**，跟上面那个内嵌后端是两码事
        # （ROCKNIX 上：内嵌走 mpv 能用，全屏也走 mpv 能用，一致）。
        self.has_mpv = bool(vplayer_mod.tool('mpv'))
        # ROM 文件大小：后台 scandir 整个系统目录一次建表（实测 0.1 秒），
        # 之后每帧查表是 O(1)。逐个 getsize 是 10.7ms/款，绝不能放主线程。
        self.sizes = cover_mod.SizeLoader()
        # 多碟游戏的总量算一次就缓存住，避免每帧重复求和
        self._size_cache = {}

        self._open_index()

    # ------------------------------------------------------------ 数据
    def _open_index(self):
        self.conn = idx.connect()
        self.reload_systems()
        if not self.sys_rows:
            self.start_scan(auto=True)

    def reload_systems(self):
        try:
            self.sys_rows = list(idx.systems(self.conn))
        except Exception:
            self.sys_rows = []
        if self.sys_sel >= len(self.sys_rows):
            self.sys_sel = max(0, len(self.sys_rows) - 1)

    def current_system(self):
        if not self.sys_rows:
            return None
        return self.sys_rows[self.sys_sel]

    def load_games(self):
        s = self.current_system()
        if not s:
            self.games = []
            return
        self.games = list(idx.games_of(self.conn, s['nas_dir'], 0, 5000))
        self.game_sel = 0
        self.game_top = 0
        self.desc_scroll = 0
        # 换系统 = 换一批素材与 metadata，两个加载器都要清干净再起新的。
        # 视频播放器也要停：gid 会撞车（游戏 id 是全局自增，但同一个 id
        # 在新旧列表里指向不同游戏），不主动停就会接着播上一套系统的画面。
        self.covers.clear()
        self.descs.clear()
        self.sizes.clear()
        self._size_cache = {}
        self.vplayer.set(None, None)
        self.desc_lines_cache = {}
        self.card_title_cache = {}
        self.descs.start(s['nas_dir'],
                         [(g['id'], g['title']) for g in self.games])
        # 后台建大小表。**要把这个系统所有游戏用到的 src_dir 都扫上** ——
        # 同一 ES 系统拆到多个 NAS 目录时（SS / SS plus）漏扫就查不到大小。
        # 表回来之前右栏**不显示**大小（而不是显示 0 或 ?），
        # 实测 PSP/GBA 都是 0.1 秒，用户基本感知不到这段空窗。
        self.sizes.start(s['nas_dir'],
                         set(g['src_dir'] or s['nas_dir'] for g in self.games))

    def stats_line(self):
        try:
            st = idx.stats(self.conn)
            return '共 %d 款 · %d 个系统' % (st['games'], st['sys_ok'] + st['sys_maybe'])
        except Exception:
            return ''

    # ------------------------------------------------------------ 扫描
    def start_scan(self, auto=False):
        if self.scanning:
            return
        self.scanning = True
        self.progress = {'phase': '准备', 'done': 0, 'total': 1, 'msg': ''}

        def prog(stage, cur, total, msg):
            self.progress = {'phase': stage, 'done': cur, 'total': total, 'msg': msg}

        def work():
            try:
                r = idx.build(progress=prog)
            except Exception as e:
                r = {'ok': False, 'error': str(e)}
            self.worker_result = ('scan', r)

        self.worker = threading.Thread(target=work)
        self.worker.daemon = True
        self.worker.start()
        if not auto:
            self.screen = 'progress'

    # ------------------------------------------------------------ 安装
    def start_install(self, game):
        if self.worker and self.worker.is_alive():
            return
        self.progress = {'phase': '准备', 'done': 0, 'total': 1, 'msg': game['title']}
        self.speed_reset()

        def prog(phase, done, total, msg):
            self.progress = {'phase': phase, 'done': done, 'total': total, 'msg': msg}
            # 只有 'copy' 阶段真的在搬字节，也只有它的 done 是"字节数"。
            # 'check'/'m3u'/'art'/'gamelist' 的 done 是 0 或者等于 total，
            # 拿它们算速度会得出 0 或天文数字。
            if phase == 'copy' and total > 1:
                self.speed_sample(done)

        def work():
            try:
                r = inst.install(self.conn, game, progress=prog)
            except Exception as e:
                r = {'ok': False, 'error': str(e)}
            self.worker_result = ('install', r)

        self.screen = 'progress'
        self.worker = threading.Thread(target=work)
        self.worker.daemon = True
        self.worker.start()

    def start_remove(self, game):
        if self.worker and self.worker.is_alive():
            return

        def work():
            try:
                r = inst.remove(self.conn, game)
            except Exception as e:
                r = {'ok': False, 'error': str(e)}
            self.worker_result = ('remove', r)

        self.screen = 'progress'
        self.progress = {'phase': '卸载', 'done': 0, 'total': 1, 'msg': game['title']}
        self.worker = threading.Thread(target=work)
        self.worker.daemon = True
        self.worker.start()

    # ------------------------------------------------------------ 商店源
    def open_source(self, back_screen='systems'):
        """进入「修改商店源」页（每次都重读磁盘，避免外部改过看不到）"""
        self.source = src.load()
        self.src_sel = 0
        self.src_back_screen = back_screen
        self.kb = None
        self.screen = 'source'

    def start_hostscan(self):
        """扫描本网段开 445 的主机（掌机没键盘，这是选主机的主要方式）"""
        if self.worker and self.worker.is_alive():
            return
        self.netscan = {'running': True, 'hosts': [], 'sel': 0,
                        'done': 0, 'total': 254, 'msg': ''}
        self.screen = 'hostscan'

        def prog(cur, total, ip):
            self.netscan['done'] = cur
            self.netscan['total'] = total
            self.netscan['msg'] = ip

        def work():
            try:
                hs = src.scan_hosts(progress=prog)
            except Exception:
                hs = []
            self.netscan['hosts'] = hs
            self.netscan['running'] = False

        self.worker = threading.Thread(target=work)
        self.worker.daemon = True
        self.worker.start()

    def start_mount(self):
        """保存 source.json 并重新挂载（脚本与开机挂载共用同一把 flock）"""
        if self.worker and self.worker.is_alive():
            return
        cfg = src.save(self.source)
        self.source = cfg
        self.progress = {'phase': 'mount', 'done': 0, 'total': 1,
                         'msg': src.unc_path(cfg)}

        def work():
            try:
                r = src.mount(cfg, force=True)
            except Exception as e:
                r = {'ok': False, 'error': str(e)}
            self.worker_result = ('mount', r)

        self.screen = 'progress'
        self.worker = threading.Thread(target=work)
        self.worker.daemon = True
        self.worker.start()

    def open_kb(self, key):
        label = dict(SRC_FIELDS).get(key, key)
        page = 0 if key == 'host' else 1     # 主机是 IP，从数字页起步
        self.kb = {'key': key, 'label': label, 'page': page, 'r': 0, 'c': 0,
                   'buf': self.source.get(key, '')}
        self.screen = 'kb'

    def commit_kb(self):
        kb = self.kb
        if not kb:
            return
        key = kb['key']
        val = src.sanitize(kb['buf']).strip()
        if key in ('host', 'share'):
            val = val.strip().strip('/')
        self.source[key] = val
        self.kb = None
        self.screen = 'source'

    # ------------------------------------------------------------ 渲染
    def txt(self, s, x, y, rgb, font=None):
        f = font or self.f_row
        surf = f.render(s, rgb)
        if surf:
            self.canvas.blit(surf, x, y)

    def txt_r(self, s, xr, y, rgb, font=None):
        f = font or self.f_row
        w = f.width(s)
        self.txt(s, xr - w, y, rgb, f)

    def clip(self, s, maxw, font=None):
        """按像素宽度截断并加省略号（详情页左栏窄，长路径会顶到封面）"""
        f = font or self.f_small
        s = s or ''
        if f.width(s) <= maxw:
            return s
        ell = '…'
        ew = f.width(ell)
        out = ''
        for ch in s:
            if f.width(out + ch) + ew > maxw:
                break
            out += ch
        return out + ell

    def header(self, title, sub=''):
        self.canvas.rect(0, 0, self.ww, HEADER_H, HEADER_BG)
        self.canvas.rect(0, HEADER_H - 2, self.ww, 2, SEL_EDGE)
        self.txt(title, 16, 6, ACCENT, self.f_title)
        if sub:
            self.txt_r(sub, self.ww - 16, 14, TEXT_DIM, self.f_small)

    def footer(self, hints):
        """底栏按键提示。用 f_tiny（13px）：网格屏要摆 6 组提示，
        16px 下一行塞不下，而且底栏字大反而抢卡片标题的视觉重心。

        每项的宽度**每帧只量一次**并累加，不做「量两遍再比较」——
        Fonts.width() 要进 TTF_SizeUTF8，早先逐项调两遍让底栏成了
        帧时间的可见开销，而这里只是要判断放不放得下。
        """
        f = self.f_tiny
        y0 = self.wh - FOOTER_H
        self.canvas.rect(0, y0, self.ww, FOOTER_H, HEADER_BG)
        self.canvas.rect(0, y0, self.ww, 2, 0xFF3A3A3A)
        x = 14
        limit = self.ww - 12
        for key, label in hints:
            kw = f.width(key)
            lw = f.width(label)
            # 放不下就整体停手，不要让最后几项糊到屏幕外面去
            if x + kw + lw > limit:
                break
            self.txt(key, x, y0 + 14, ACCENT, f)
            self.txt(label, x + kw + 6, y0 + 14, TEXT_DIM, f)
            x += kw + 6 + lw + 18

    def draw_sel_bar(self, y):
        self.canvas.rect(8, y - 3, self.ww - 16, ROW_H, SEL_BG)
        self.canvas.rect(8, y - 3, 4, ROW_H, SEL_EDGE)

    # ------------------------------------------------------------ 系统屏
    def render_systems(self):
        self.header('ROM 商店', self.stats_line())
        if not self.sys_rows:
            self.txt('索引为空。按 Start 打开菜单，选「重扫 NAS」。', 20, 120, WARN, self.f_row)
            self.footer([('Start', '菜单')])
            return
        top = max(0, min(self.sys_top, len(self.sys_rows) - VIS_ROWS))
        self.sys_top = top if len(self.sys_rows) > VIS_ROWS else 0
        y = HEADER_H + 8
        for i in range(top, min(top + VIS_ROWS, len(self.sys_rows))):
            r = self.sys_rows[i]
            if i == self.sys_sel:
                self.draw_sel_bar(y)
            name = r['nas_dir']
            note = r['note'] or ''
            mark = '' if r['conf'] == 'ok' else ' ⚠'
            col = TEXT if i == self.sys_sel else TEXT_DIM
            self.txt(name, 22, y, col, self.f_row)
            nw = self.f_row.width(name) + 22
            if note:
                self.txt(note + mark, nw + 14, y, WARN if mark else TEXT_MUTE, self.f_small)
            self.txt_r('%d' % r['avail'], self.ww - 20, y + 2, GREEN, self.f_small)
            y += ROW_H
        if len(self.sys_rows) > VIS_ROWS:
            self.txt_r('%d/%d' % (self.sys_sel + 1, len(self.sys_rows)),
                       self.ww - 20, self.wh - FOOTER_H - 22, TEXT_MUTE, self.f_small)
        self.footer([('A', '进入'), ('L1/R1', '翻页'), ('Start', '菜单')])

    # ------------------------------------------------------------ 游戏屏（卡片网格 + 右栏详情）
    def _grid_metrics(self):
        """算网格的列数、可见行数、卡宽，以及**右栏的起始 x**

        640x480 减去顶栏/底栏，左栏留给网格，右栏 DETAIL_W 给详情。

        ## 为什么要"搜"而不是直接定 5 列（0.6.0）

        卡片尺寸和"每屏几张"是一对矛盾：4 行卡片时卡宽被**高度**卡住
        （= (可用高 - 3*间距) / 4），而列数少了又会被**宽度**卡住。
        老写法先钉死 5 列再算卡宽，于是去掉标题腾出来的高度全白费了 ——
        卡宽被宽度锁死在 71px，多出来的行高没人用。

        现在反过来：枚举列数(2~6) x 行数，每个组合取
        `min(宽度允许的卡宽, 高度允许的卡宽)`，然后在**每屏张数 >=
        GRID_TARGET_CARDS** 的组合里挑卡宽最大的那个。

        实测 640x480：4 列 x 4 行 / 卡宽 91px / 每屏 16 张（可用高 392 正好
        用完）。5 列时卡宽只有 71px，3 列时虽然能到 124px 但每屏只剩 9 张。

        **右栏 x 必须在这里一并算出来**：早先在 render_games 里另算一遍，
        两处的 PAD 数法不一致，网格右边正好压掉右栏分隔线 10 px。
        同一份几何数据只算一次，才不会这类 bug 再回来。
        """
        left_w = self.ww - DETAIL_W - PAD * 2
        avail_h = self.wh - HEADER_H - FOOTER_H - 6
        best = None                      # (卡宽, 张数) -> 越大越好
        pick = None
        for cols in range(2, 7):
            cw_w = (left_w - CARD_GAP * (cols - 1)) // cols
            if cw_w < CARD_MIN:
                continue
            max_rows = (avail_h + CARD_GAP) // (cw_w + CARD_GAP)
            for rows in range(max_rows, 0, -1):
                cw = min(cw_w, (avail_h - CARD_GAP * (rows - 1)) // rows)
                if cw < CARD_MIN:
                    continue
                if cols * rows < GRID_TARGET_CARDS:
                    continue
                key = (cw, cols * rows)   # 先比卡多大，一样大再比谁更紧凑
                if best is None or key > best:
                    best, pick = key, (cols, rows, cw)
        if pick is None:
            # 连目标张数都凑不齐（超小屏 / 右栏被调得很宽）：退到
            # "能塞几列塞几列、卡片尽量大"，保证界面不会画不出来
            cols = max(1, (left_w + CARD_GAP) // (CARD_MIN + CARD_GAP))
            cw = max(1, (left_w - CARD_GAP * (cols - 1)) // cols)
            rows = max(1, (avail_h + CARD_GAP) // (cw + CARD_GAP))
            pick = (cols, rows, cw)
        cols, rows, cw = pick
        ch = cw + CARD_TEXT_H
        detail_x = PAD * 2 + cols * cw + (cols - 1) * CARD_GAP
        return cols, max(1, rows), cw, ch, detail_x

    def _grid_viewport(self, cols, rows):
        """算出网格首行的起始下标（同时写回 self.game_top），并返回它

        视口是**粘性状态**：它只在选中项要跑出可见区时才动，一次动一行。
        绝不能每帧从选中项反推（`top = sel - per_page//2` 那种）——
        反推出来的必然是"居中跟随"，光标会被钉在中间一行不动，
        而任何按键都会带着整屏一起滚。0.6.0 之前就是这个毛病，
        用户的原话是「按右键只会向下换行」「网格会出现横向滚动」。

        抽成独立方法是为了能离线测（见 tests/test_nav.py）——
        它只碰 game_sel / game_top / games，不需要 SDL 和字体。
        """
        n = len(self.games)
        top_row = self.game_top // cols
        total_rows = (n + cols - 1) // cols
        max_top_row = max(0, total_rows - rows)
        top_row = max(0, min(top_row, max_top_row))
        sel_row = self.game_sel // cols
        if sel_row < top_row:                       # 往上跑出去了
            top_row = sel_row
        elif sel_row > top_row + rows - 1:          # 往下跑出去了
            top_row = sel_row - rows + 1
        self.game_top = top_row * cols
        return self.game_top

    def render_games(self):
        s = self.current_system()
        if not s:
            self.screen = 'systems'
            return self.render_systems()
        # 位置指示并进顶栏。0.6.0 之前它在网格右下角（wh-FOOTER_H-22），
        # 那时网格只有 3 行、够不着那儿；现在网格铺满可用高度，
        # 那个位置**正好压在最后一行卡片上**，所以挪到顶栏副标题。
        self.header(s['nas_dir'] + ('  ' + s['note'] if s['note'] else ''),
                    '%d/%d 款' % (self.game_sel + 1, len(self.games)))
        if not self.games:
            self.txt('该系统没有可安装的游戏。', 20, 120, WARN, self.f_row)
            self.footer([('B', '返回')])
            return

        cols, rows, cw, ch, detail_x = self._grid_metrics()
        per_page = cols * rows
        # 视口：只纵向、只在光标要跑出可见区时才滚一行。
        # 4 行可见时，光标在第 1~3 行完全不滚，从第 3 行再往下才滚
        # —— 也就是用户要的「第四行开始滚动」。详见 _grid_viewport。
        top = self._grid_viewport(cols, rows)

        # 通知后台把可见卡片（外加下一屏预取）去加载 —— 每帧调用，必须便宜
        self.covers.pump(self.games, top, per_page, self.game_sel)
        # 告诉视频播放器该播哪一款。set() 内部有「没变就直接返回」的
        # 判断，所以每帧调它零成本 —— 只有换选中项时才会重起 ffmpeg。
        g = self.games[self.game_sel]
        self.vplayer.set(g['id'], self.video_of(g))

        for k in range(per_page):
            i = top + k
            if i >= len(self.games):
                break
            cx = PAD + (k % cols) * (cw + CARD_GAP)
            cy = HEADER_H + 4 + (k // cols) * (ch + CARD_GAP)
            self._draw_card(self.games[i], cx, cy, cw, ch, i == self.game_sel)

        # 右栏（x 由 _grid_metrics 给出，和网格用的是同一份几何数据）
        self._draw_detail(detail_x, HEADER_H + 4, DETAIL_W,
                          self.wh - HEADER_H - FOOTER_H - 8)

        # 视频是否存在也是后台查的。还没查完时**不能**直接说「无预览」——
        # 那是在替后台下结论。按住不动等一秒就会变成正确的字。
        # 另外：没装 mpv 的机器上「全屏预览」根本走不通，那就老实说没有，
        # 别让用户按下去等一个不会出现的黑屏。
        g = self.games[self.game_sel]
        if self.video_of(g):
            play = '全屏预览' if self.has_mpv else '无预览'
        elif self.covers.ready(g['id']):
            play = '无预览'
        else:
            play = '载入中'
        self.footer([('A', '安装'), ('X', play), ('Y', '卸载'),
                     ('右摇杆', '简介'), ('L1/R1', '翻页'), ('B', '返回')])

    def _cached_lines(self, key, text, maxw, maxlines, font=None, cap=400):
        """按 key 缓存 wrap 的结果，超上限时淘汰**最旧的一半**而不是整表清空

    整表清空看着简单，代价很大：浏览时标题缓存会稳步涨到上限，一旦清空，
    接下来每帧都要把当前 15 张卡片的标题重排一遍 —— 实测这一下就是
    100 多毫秒，整帧直接掉出 30fps 预算。淘汰一半则只损失一点命中率，
    而**刚看过的标题总是最新的**，不会立刻又被清。
    """
        lines = self.card_title_cache.get(key)
        if lines is not None:
            return lines
        f = font or self.f_tiny
        lines = wrap(text, f, maxw, maxlines)
        if len(self.card_title_cache) >= cap:
            for k in list(self.card_title_cache.keys())[:cap // 2]:
                self.card_title_cache.pop(k, None)
        self.card_title_cache[key] = lines
        return lines

    def _draw_card(self, g, x, y, cw, ch, selected):
        """一张卡片：**只有封面**（+ 已装 / 多碟 / 无封面时的文字兜底）

        0.6.0 按用户反馈改了两处：
          * 选中态 = **只画框**，不铺蓝底。早先用 0xFF2C3A55 整块铺底，
            等于拿一张蓝方片盖住封面 —— 恰好把「用封面认游戏」这件事废掉了，
            而封面正是这个商店里最有用的识别信息。
          * 卡片上**不再写标题**。标题原来占卡片底部两行，紧贴在封面正下方、
            底色又与封面边框同色，看着像一条压在封面底部的黑带。
            去掉后封面撑满整格（本 NAS 的封面是正方形，1:1 正好铺满），
            游戏名改由右栏详情栏显示。

        唯一例外：**确实没有封面**的游戏仍在格子里居中写名字（最多 3 行），
        否则一整格黑方块认不出是哪一款。
        """
        # 正方形封面区，四周各留 2px 边框 —— 卡片就是封面本身
        img_h = int(cw / float(CARD_ASPECT))
        # 卡片底板：选中与否**完全一样**，都是同一层浅底
        self.canvas.rect(x, y, cw, ch, 0xFF262626)
        # 封面区
        self.canvas.rect(x + 2, y + 2, cw - 4, img_h - 4, 0xFF1A1A1A)
        surf = self.covers.thumb(g['id'])
        if surf is not None:
            sw, sh = surf.contents.w, surf.contents.h
            self.canvas.blit(surf, x + 2 + ((cw - 4) - sw) // 2,
                             y + 2 + ((img_h - 4) - sh) // 2)
        elif self.covers.ready(g['id']):
            # 查完了确实没有封面 -> 用名字兜底
            self._draw_card_name(g, x, y, cw, img_h, selected)
        else:
            # 还在加载：给个「…」而不是留白，避免误以为没封面
            self.txt_r('…', x + cw - 8, y + img_h // 2 - 8, TEXT_MUTE,
                       self.f_small)

        # 已安装角标：右上角一个小绿点，比写「已装」两个字省地方
        if self.is_installed(g):
            self.canvas.rect(x + cw - 12, y + 6, 6, 6, argb(GREEN))

        if g['is_multi']:
            # 「多碟」角标放卡片右上角，和「已装」绿点分列两侧
            self.txt_r('碟', x + cw - 5, y + 4, WARN, self.f_small)

        if selected:
            # 2px 亮边框（画四条，避免 rect 只填一圈导致边缘发虚）
            e = SEL_EDGE
            self.canvas.rect(x - 2, y - 2, cw + 4, 2, e)          # 上
            self.canvas.rect(x - 2, y + ch, cw + 4, 2, e)         # 下
            self.canvas.rect(x - 2, y - 2, 2, ch + 4, e)          # 左
            self.canvas.rect(x + cw, y - 2, 2, ch + 4, e)         # 右

    def _draw_card_name(self, g, x, y, cw, img_h, selected):
        """没有封面时的兜底：把游戏名居中写在格子里的 13px 小字（最多 3 行）

        **必须缓存**：wrap 要逐字量像素宽度，而真机 cProfile 早就证明
        Fonts.width() 是仅次于 blit 的热点（10 帧 1720 次）。按
        (游戏id, 卡宽) 缓存一次就够 —— 标题是固定文本，卡宽也固定。
        """
        lines = self._cached_lines((g['id'], cw, 'cardnb'), g['title'],
                                   cw - 10, 3)
        lh = 15
        ty = y + 2 + max(0, ((img_h - 4) - len(lines) * lh) // 2)
        for ln in lines:
            tw = self.f_tiny.width(ln)
            self.txt(ln, x + max(2, (cw - tw) // 2), ty,
                     WHITE if selected else TEXT_DIM, self.f_tiny)
            ty += lh

    def _draw_detail(self, x, y, w, h):
        """右栏：状态标签 + 标题 + **视频预览区** + 简介（可滚动）

        0.5.0 按反馈迭代：
          - 视频区**不再塞封面图**，整块就是视频画面（16:9）。
            早先左封面右说明的排法把视频压成半张图，而封面在左栏已经有了。
          - 简介移到视频**正下方**，右摇杆上下滚动。
          - 画面上**不再压字幕条**（那行「X 全屏预览」看着像视频被截掉一块），
            按键提示交给底部状态栏。
          - 状态标签提到标题**前面**、与标题同高，省下一整行 28px ——
            简介区是按剩余高度算的，这 28px 自动变成多两行正文。
        """
        g = self.games[self.game_sel]
        self.canvas.rect(x, y, 1, h, 0xFF3A3A3A)      # 分隔线
        x += 10
        w -= 14
        cur = y + 2

        # ---- 标题：最多两行，占满整行宽度 ----
        tkey = (g['id'], w, 'detail')
        title_lines = self._cached_lines(tkey, g['title'], w, 2, self.f_row)
        for i, ln in enumerate(title_lines):
            self.txt(ln, x, cur + i * 24, ACCENT, self.f_row)
        cur += len(title_lines) * 24

        # ---- 状态标签行：标题**下方**，右侧显示文件大小 ----
        # 色块高度不能想当然。早先取 20px，可 f_small(16) 的**实际渲染高度
        # 是 24px**（TTF_FontHeight 返回 24，真机实测 surface 就是 48x24），
        # 于是「未安装」三个字上下都溢出底色 —— 用户反馈「字体超出底色块」。
        # 现在块高直接取 f_small.line_h：字面高度即块高，绝不会再溢出。
        # 宽度也按实测字宽 + 左右各 6px 内边距，不再硬编码。
        bh = self.f_small.line_h
        bx = x
        if self.is_installed(g):
            label, fg, bg = '已安装', GREEN, 0xFF2C4A2C
        else:
            label, fg, bg = '未安装', TEXT_DIM, 0xFF3A3A3A
        bw = self.f_small.width(label) + 12
        self.canvas.rect(bx, cur, bw, bh, bg)
        self.txt(label, bx + 6, cur, fg, self.f_small)
        bx += bw + 6
        if g['is_multi']:
            bw2 = self.f_small.width('多碟') + 12
            self.canvas.rect(bx, cur, bw2, bh, 0xFF4A3A2C)
            self.txt('多碟', bx + 6, cur, WARN, self.f_small)
            bx += bw2 + 6

        # 大小右对齐到右栏边缘。查不到就**什么都不画** —— 在后台统计表
        # 回来之前显示「?」会让人以为游戏真的没有大小。
        nbytes = self.sizes.lookup(g, self._size_cache)
        if nbytes is not None:
            self.txt_r(inst.human(nbytes), x + w, cur, TEXT_MUTE, self.f_small)
        cur += bh + 6
        
        # ---- 视频预览区：整块 16:9 真实画面（抽帧自预览视频）----
        cur = self._draw_preview(g, x, cur, w)
        cur += 8

        # ---- 简介：视频下方，右摇杆滚动 ----
        self.canvas.rect(x, cur, w, 2, 0xFF3A3A3A)
        cur += 8
        # 三态要分清：**还没加载完** / **加载完了但确实没有** / **有内容**。
        # 早先这里写成「desc 为空就显示没有简介」，结果加载中也会显示
        # 「（这个游戏没有简介）」，两行自相矛盾的话同时出现在屏幕上。
        desc = self.descs.get(g['id'])
        if desc is None:
            self.txt('简介载入中…', x, cur, TEXT_MUTE, self.f_small)
        elif not desc:
            self.txt('（这个游戏没有简介）', x, cur, TEXT_MUTE, self.f_small)
        else:
            body_h = y + h - cur - 18      # 留 18px 给底部的滚动指示条
            maxlines = max(1, body_h // 15)
            # **整段**排版（最多 200 行），而不是只排可视的那些 ——
            # 滚动范围取决于总行数，只排 maxlines 行就不知道后面还有多少。
            # 结果按 (游戏, 宽度) 缓存：换游戏才重排一次，之后每帧只是
            # 把算好的行贴上去。不缓存的话每帧都要重跑一遍 wrap，
            # 而 wrap 要逐字测量像素宽度（真机 cProfile 的第二大热点）。
            fk = (g['id'], w, 'full')
            full = self.desc_lines_cache.get(fk)
            if full is None:
                full = wrap(desc, self.f_tiny, w, 200)
                # 上限 8 是照着「只有当前这一款要排」定的，但浏览时
                # 每款都排一次、两次就撑爆并被整表清掉 —— 下一款又得重排。
                # 给到 32 项（约 32 款游戏的简介行，内存几百 KB），
                # 上限到才淘汰最旧的一半。左右移动回看时几乎全命中。
                if len(self.desc_lines_cache) >= 32:
                    for k in list(self.desc_lines_cache.keys())[:16]:
                        self.desc_lines_cache.pop(k, None)
                self.desc_lines_cache[fk] = full
            total = len(full)
            start = self._clamp_desc_scroll(self.desc_scroll, total, maxlines)
            self.desc_scroll = start
            for i in range(maxlines):
                if start + i >= total:
                    break
                self.txt(full[start + i], x, cur, TEXT_DIM, self.f_tiny)
                cur += 15
            self._draw_scroll_hint(x, y + h - 16, w, start, total, maxlines)

    def _draw_preview(self, g, x, y, w):
        """视频预览区（16:9），返回下一行的 y

        显示的是**正在播放的视频画面**（vplayer 用一个常驻 ffmpeg 管道
        持续供帧，这里贴的是最新一帧），不是封面也不是静止截图。
        换选中项时 vplayer 会自己重起管道，这里不用管。
        """
        ph = PREVIEW_H
        self.canvas.rect(x, y, w, ph, 0xFF101010)
        # **必须传 gid**：切换游戏后 worker 可能还在收上一款的尾帧，
        # vplayer.frame(gid) 会把不属于当前游戏的帧挡掉，
        # 否则「切了游戏却还在播上一个视频」。
        frame = self.vplayer.frame(g['id'])
        if frame is not None:
            sw, sh = frame.contents.w, frame.contents.h
            self.canvas.blit(frame, x + (w - sw) // 2, y + (ph - sh) // 2)
            # 这里**不要**再压半透明字幕条。早先为了「X 全屏预览」这行
            # 提示，在画面最底下盖了 20px 的黑条，结果用户看到的正是
            # 「视频被截掉下方一块」—— 实际是提示条压住了画面，
            # 而全屏预览的按键提示底部状态栏本来就有（footer 的 X 项）。
            return y + ph

        # 还没出第一帧。分清「正在起播」和「确实没有片源」——
        # 后者只字未提视频路径，前者也还没查出来（video_of 为 None）。
        has_video = bool(self.video_of(g))
        surf = self.covers.thumb(g['id'])
        if surf is not None:
            sw, sh = surf.contents.w, surf.contents.h
            self.canvas.blit(surf, x + (w - sw) // 2, y + (ph - sh) // 2)
        if has_video and not self.vplayer.available:
            # 本平台**没法在界面里播**（比如 ROCKNIX 上没有 ffmpeg 命令行）。
            # 这里绝不能压半透明条 + 写「正在播放预览…」—— 那是假话，
            # 用户会一直等一个永远不来的画面。只提示还有全屏预览这条路。
            self.txt('按 X 全屏预览', x + w // 2 - 46, y + ph // 2 - 8,
                     TEXT_DIM, self.f_small)
        elif has_video:
            self.canvas.rect(x, y, w, ph, 0x99000000)   # 半透明盖住封面
            self.txt('正在播放预览…', x + w // 2 - 52, y + ph // 2 - 8,
                     TEXT_DIM, self.f_small)
        else:
            if self.covers.ready(g['id']):
                self.txt('没有预览视频', x + w // 2 - 42, y + ph // 2 - 8,
                         TEXT_MUTE, self.f_small)
            else:
                self.txt('载入中…', x + w // 2 - 28, y + ph // 2 - 8,
                         TEXT_MUTE, self.f_small)
        return y + ph

    def _draw_scroll_hint(self, x, y, w, start, total, maxlines):
        """简介滚动指示条：只在真的需要滚时才画

        居中要量文字宽度，但文案只有「start+1/total」这一种，
        量过的结果按 (文案, 区域宽度) 缓存 —— 不然每帧都进 TTF_SizeUTF8。
        """
        if total <= maxlines:
            return
        # 上/下箭头：▲ 表示上面还有，▼ 表示下面还有
        if start > 0:
            self.txt('▲', x, y, ACCENT, self.f_tiny)
        if start + maxlines < total:
            self.txt_r('▼', x + w, y, ACCENT, self.f_tiny)
        msg = '%d/%d' % (start + 1, total)
        ck = (msg, w)
        tw = self.card_title_cache.get(ck)
        if tw is None:
            tw = self.f_tiny.width(msg)
            self.card_title_cache[ck] = tw
        self.txt(msg, x + w // 2 - tw // 2, y, TEXT_MUTE, self.f_tiny)

    def _clamp_desc_scroll(self, val, total, maxlines):
        """把滚动位置夹到合法范围（越界时贴边，不回弹）"""
        maxstart = max(0, total - maxlines)
        return max(0, min(val, maxstart))

    def is_installed(self, g):
        key = g['id']
        v = self.installed_cache.get(key)
        if v is None:
            name = g['rom_rel'] or ''
            if g['is_multi']:
                name = inst.sanitize(g['title']) + '.m3u'
            name = os.path.basename(name.replace('\\', '/'))
            v = bool(name) and os.path.exists(os.path.join(g['es_path'], name))
            self.installed_cache[key] = v
        return v

    # ------------------------------------------------------------ 视频
    def video_of(self, g):
        """该游戏的预览视频路径；没有 / 还没查出来都返回 None

        结果由 CoverLoader 在后台查好（它找封面时顺手一起 listdir 了），
        这里只查表 —— 绝不能在这里同步去 NAS 上找，那会卡住整个网格。
        """
        return self.covers.video(g['id'])

    def request_play(self):
        """按 X：播放当前选中游戏的 NAS 预览视频

        本进程占着 KMS 显示，没法在自己进程里放视频，所以走「退出 + 交给外层」：
        写好请求文件后以 EXIT_PLAY 退出，RomStore.sh 会接手播 mpv 再把界面拉回来。
        """
        g = self.games[self.game_sel] if self.games else None
        if not g:
            return
        v = self.video_of(g)
        if not v:
            self.msg = {'text': '这款游戏没有预览视频。\n（按 A 仍可正常安装）',
                        'color': WARN}
            return
        if not self.has_mpv:
            # 本机没装 mpv。RomStore.sh 那一段是用 mpv 播的，硬按下去只会
            # 让界面退出又立刻回来（还白闪一下），不如在这里就说清楚。
            self.msg = {'text': '本机没有 mpv，无法全屏预览。\n（按 A 仍可正常安装）',
                        'color': WARN}
            return
        try:
            with open(PLAY_REQ, 'w') as f:
                f.write(v + '\n' + str(g['id']) + '\n')
        except OSError as e:
            self.msg = {'text': '无法写入播放请求：%s' % e, 'color': RED}
            return
        self.exit_code = EXIT_PLAY
        self.running = False

    # ------------------------------------------------------------ 进度屏
    # ------------------------------------------------------------ 速度计
    #
    # 0.6.0 新增：进度页显示**复制速度**。
    #
    # 为什么要在界面上单独立一个速度计：ROM 都是几十 MB 到几 GB，从 NAS
    # 经 SMB（ArkOS）或 rclone FUSE（AURKNIX）读、再写到 SD 卡，快的时候
    # 十几 MB/s、慢的时候几百 KB/s。只有一个百分比进度条，用户根本分不清
    # 「还要等 10 秒」还是「已经卡死了」—— 速度一显示就明白了。
    #
    # 采样放在 UI 侧而不是 install.py：**不改 install() 的回调签名**
    # （cli.py 也用它），而且任何阶段的字节流都自动被覆盖。
    _SP_WINDOW = 3.0      # 滑动窗口长度（秒）：短了数字乱跳，长了反应迟钝
    _SP_STALL = 2.0       # 超过这么久没有新样本就不显示数字，见 copy_speed
    _SP_HIST = 60         # 柱状图保留多少格
    _SP_MAX = 400         # 样本列表的硬上限（见 speed_sample 里的说明）

    def speed_reset(self):
        """开新任务时清零（换一个游戏/重新扫描，不能留着上一款的速度）"""
        with self._sp_lock:
            self._sp = []
            self._sp_hist = []
            self._sp_at = 0.0
        self.t_start = time.time()

    def speed_sample(self, done):
        """记一个样本。**worker 线程调用**，所以要加锁（见 __init__ 的说明）"""
        now = time.time()
        with self._sp_lock:
            self._sp.append((now, done))
            self._sp_at = now
            # 只留窗口内的样本；至少留 2 个，否则刚从窗口滑出去就没了
            while len(self._sp) > 2 and now - self._sp[0][0] > self._SP_WINDOW:
                self._sp.pop(0)
            # 兜底硬上限。窗口裁剪是**靠"头部样本够老"**来推进的：万一
            # 时间戳几乎不前进（极快的本地复制、NTP 把表拨回去、或者调用方
            # 用假时间喂样本），头部永远不"够老"，列表就会一路涨下去。
            # 真机上速度是 2~35 MB/s、512KB 一个样本，3 秒窗口本来就只有
            # 几十个样本，这条上限正常永远碰不到 —— 它只是保证"有界"这个
            # 性质不被时间源搞掉。这个是 tests/test_speed.py 第 7 节逼出来的。
            if len(self._sp) > self._SP_MAX:
                del self._sp[:-self._SP_MAX]
            # 顺带记一格历史给柱状图 —— 跟着样本走而不是跟渲染走：
            # 渲染是 30fps，跟它走的话一秒钟会塞 30 格，图会糊成一片。
            if len(self._sp) >= 2:
                span = self._sp[-1][0] - self._sp[0][0]
                if span > 0.001:
                    inst_ = (self._sp[-1][1] - self._sp[0][1]) / span
                    self._sp_hist.append(inst_)
                    if len(self._sp_hist) > self._SP_HIST:
                        del self._sp_hist[:-self._SP_HIST]

    def copy_speed(self):
        """当前速度（字节/秒）。算不出、或已经停顿，返回 0.0

        ## 分母用「现在」而不是「最后一个样本的时刻」

        这样「卡住不动」会自然衰减到 0：没有新样本 → 分子不变，而分母
        `now - t0` 一直在涨，速度被摊薄。反过来若在采样时就把速度算好存
        起来，界面会一直挂着一个好看的数字骗人 —— 正是要避免的那种「假装
        在跑」的显示。

        但也不能一停就报 0：慢速传输时两个样本本来就可能隔一两秒
        （512KB 的粒度，200KB/s 就是 2.5 秒一个样本）。所以只有超过
        `_SP_STALL` 秒确实没有任何样本，才认定为停顿。
        """
        now = time.time()
        with self._sp_lock:
            first = self._sp[0] if self._sp else None
            last = self._sp[-1] if self._sp else None
            at = self._sp_at
        if first is None or last is None or last is first:
            return 0.0
        if now - at > self._SP_STALL:
            return 0.0
        span = now - first[0]
        got = last[1] - first[1]
        if span <= 0.05 or got <= 0:
            return 0.0
        return got / span

    def speed_history(self):
        with self._sp_lock:
            return list(self._sp_hist)

    def speed_elapsed(self):
        """本次传输已用了几秒

        **以第一个样本为准**，而不是 `t_start`：真机上踩过 —— `t_start`
        只在 `start_install` 里被 `speed_reset()` 赋值，任何绕过它构造
        进度状态的路径（无头脚本、以后新增的入口）都会拿到 0，而
        `time.time() - 0` 正好是个约 178 万的天文数字，界面会显示
        「已用 497728 小时 25 分」。第一个样本的时刻才是"传输开始"的
        可靠定义，它一定存在（有速度可显示就意味着有样本）。
        """
        with self._sp_lock:
            first = self._sp[0][0] if self._sp else 0.0
        t0 = first or self.t_start
        if not t0:
            return 0.0
        return max(0.0, time.time() - t0)

    @staticmethod
    def human_speed(n):
        """速度用「/s」后缀，并且 KB/s 不保留小数、MB/s 保留两位

        和 inst.human 的档位规则一致（B/KB 取整，MB 以上两位小数），
        免得出现「1023 KB/s」这种该进位却没进位的读法。
        """
        if n <= 0:
            return '0 KB/s'
        return inst.human(n) + '/s'

    @staticmethod
    def human_time(sec):
        """秒 -> 12 秒 / 3 分 20 秒 / 1 小时 5 分（给"已用/剩余"用）

        超过 100 小时就报 `--`：那不是"还剩很久"，那说明输入本身是坏的
        （速度几乎是 0、或者时间戳没被初始化）。宁可不显示，也不显示一个
        没人信的怪数字。
        """
        if sec is None or sec <= 0:
            return '--'
        sec = int(round(sec))
        if sec >= 100 * 3600:
            return '--'
        if sec < 60:
            return '%d 秒' % sec
        if sec < 3600:
            return '%d 分 %02d 秒' % (sec // 60, sec % 60)
        return '%d 小时 %02d 分' % (sec // 3600, (sec % 3600) // 60)

    def _draw_speed_hist(self, x, y, w, h):
        """速度柱状图：看得出「在动」和「忽快忽慢」

        数据是最近 60 个瞬时速度。**纵轴按窗口峰值归一化**，不按固定刻度
        —— 从 NAS 拉的绝对速度换个网况能差一个数量级，固定刻度要么全顶格
        要么全都看不见。归一化时留 15% 余量：实测 SMB 上速度很稳，不留余量
        的话每根柱子都顶到天花板，整条图变成一块实心砖，看不出"稳"还是
        "满"。

        横向**从左边开始长**（老→新，最新的贴着右边缘）：满 60 格前左边是
        实的、右边留白，一眼就知道"才刚开始"。反过来先钉死右对齐会让左半
        边先空一大块，看着像画坏了。
        """
        hist = self.speed_history()
        if not hist:
            return
        self.canvas.rect(x, y, w, h, 0xFF262626)
        peak = max(hist) * 1.15
        if peak <= 0:
            return
        bw = max(1, w // self._SP_HIST)          # 格宽固定，格子数才是变量
        gap = 2 if bw >= 4 else 0                # 留缝才看得出是一根根柱子
        n = len(hist)
        for i, v in enumerate(hist):
            bx = x + i * bw
            if bx >= x + w:
                break
            bh = int((h - 2) * min(1.0, v / peak))
            if bh < 1:
                bh = 1
            # 最后几格是"现在"，画亮一点；前面的是历史，压暗
            col = 0xFF5C8FD6 if i >= n - 6 else 0xFF3F6294
            self.canvas.rect(bx, y + h - 1 - bh, bw - gap, bh, col)

    def render_progress(self):
        self.header('处理中', '')
        p = self.progress or {}
        phase = p.get('phase', '')
        cname = PHASE_CN.get(phase, phase)
        done, total = p.get('done', 0), p.get('total', 1)
        pct = (done / float(total)) if total else 0.0
        self.txt(str(cname), 20, HEADER_H + 40, ACCENT, self.f_row)
        self.txt(str(p.get('msg', ''))[:46], 20, HEADER_H + 72, TEXT, self.f_small)
        bx, by, bw, bh = 20, HEADER_H + 120, self.ww - 40, 26
        self.canvas.rect(bx, by, bw, bh, 0xFF2E2E2E)
        self.canvas.rect(bx, by, int(bw * max(0.0, min(1.0, pct))), bh, SEL_EDGE)
        self.txt('%.1f%%' % (pct * 100), bx + 10, by + 3, WHITE, self.f_small)
        if total > 1:
            self.txt_r('%s / %s' % (inst.human(done), inst.human(total)),
                       bx + bw - 10, by + 3, WHITE, self.f_small)

        # ---- 传输速度 ---------------------------------------------------
        # 只在 'copy' 阶段画：其它阶段的 done/total 不是"字节流"，
        # 摆一个 0 KB/s 在那儿只会让人以为传输卡了。
        if phase == 'copy' and total > 1:
            sp = self.copy_speed()
            y = by + bh + 14
            self.txt('速度', bx, y + 5, TEXT_MUTE, self.f_small)
            if sp > 0:
                sp_text = self.human_speed(sp)
                self.txt(sp_text, bx + 44, y, ACCENT, self.f_row)
                # 剩余时间：拿"剩余字节 / 当前速度"。速度是滑窗均值，
                # 所以它会跟着网速一起变 —— 这正是用户想看到的。
                remain = total - done
                self.txt_r('剩余 %s' % self.human_time(remain / sp),
                           bx + bw, y + 5, TEXT_DIM, self.f_small)
                # 「已用」紧跟在速度后面 —— 按**实际文字宽度**定位而不是
                # 写死一个 x：速度字符串长度是会变的（"9.80 KB/s" 和
                # "102.40 MB/s" 差一倍宽），写死就会撞在一起。
                self.txt('已用 %s' % self.human_time(self.speed_elapsed()),
                         bx + 44 + self.f_row.width(sp_text) + 22, y + 5,
                         TEXT_MUTE, self.f_small)
                self._draw_speed_hist(bx, y + 30, bw, 26)
            else:
                # 停顿中（NAS 掉线 / 源文件在转 / 就快写完了）。
                # 不要报 0 KB/s —— 0 读起来像"坏了"，这里要的是"等着呢"。
                self.txt('—  等待数据…', bx + 44, y, TEXT_MUTE, self.f_row)
                self._draw_speed_hist(bx, y + 30, bw, 26)

        self.footer([('', '请稍候…')])

    # ------------------------------------------------------------ 消息
    def render_msg(self):
        self.header('提示', '')
        m = self.msg or {}
        y = HEADER_H + 24
        for ln in wrap(m.get('text', ''), self.f_row, self.ww - 48, 12):
            self.txt(ln, 24, y, m.get('color', TEXT), self.f_row)
            y += 26
        self.footer([('A/B', '知道了')])

    # ------------------------------------------------------------ 商店源设置页
    def render_source(self):
        self.header('商店源', src.status_text(self.source))
        n_f = len(SRC_FIELDS)
        n_a = len(SRC_ACTIONS)
        total = n_f + n_a
        if self.src_sel >= total:
            self.src_sel = 0

        y = HEADER_H + 10
        for i, (key, label) in enumerate(SRC_FIELDS):
            sel = (i == self.src_sel)
            if sel:
                self.draw_sel_bar(y)
            self.txt(label, 22, y, TEXT if sel else TEXT_DIM, self.f_row)
            val = self.source.get(key, '')
            if key == 'password' and val:
                val = '*' * len(val)
            if not val:
                val = '（匿名）' if key == 'user' else '（未填）'
                col = TEXT_MUTE
            else:
                col = TEXT
            self.txt(val[:36], 140, y, col, self.f_row)
            if key == 'user' and not self.source.get('user'):
                self.txt('留空 = guest 匿名', 420, y + 3, TEXT_MUTE, self.f_small)
            y += 34

        # 挂载点只读 —— es_systems.cfg 里所有路径都指向它，改了全盘对不上
        self.txt('挂载点', 22, y, TEXT_MUTE, self.f_row)
        self.txt(src.MOUNT_POINT + '（固定）', 140, y, TEXT_MUTE, self.f_row)
        y += 28
        self.canvas.rect(16, y, self.ww - 32, 2, 0xFF3A3A3A)
        y += 12

        for j, (act, label) in enumerate(SRC_ACTIONS):
            sel = (n_f + j == self.src_sel)
            if sel:
                self.draw_sel_bar(y)
            col = ACCENT if act == 'save' else (TEXT if sel else TEXT_DIM)
            self.txt(label, 22, y, col, self.f_row)
            if act == 'save':
                self.txt('%s · %s' % (src.auth_label(self.source), src.unc_path(self.source)),
                         200, y + 3, TEXT_MUTE, self.f_small)
            elif act == 'scan':
                self.txt('自动找开了 %d 的主机' % src.SMB_PORT, 200, y + 3,
                         TEXT_MUTE, self.f_small)
            y += 34

        self.footer([('A', '编辑/执行'), ('X', '扫描局域网'), ('B', '返回')])

    # ------------------------------------------------------------ 软键盘
    def render_kb(self):
        kb = self.kb or {}
        page = kb.get('page', 0) % len(KB_PAGES)
        rows = KB_PAGES[page]
        key = kb.get('key', '')
        self.header('输入 · %s' % kb.get('label', key), KB_NAMES[page])

        # 当前缓冲
        bx, by, bw, bh = 20, HEADER_H + 8, self.ww - 40, 40
        self.canvas.rect(bx, by, bw, bh, 0xFF2E2E2E)
        self.canvas.rect(bx, by, 3, bh, SEL_EDGE)
        buf = kb.get('buf', '')
        show = ('*' * len(buf)) if key == 'password' else buf
        self.txt((show[-38:] or '') + '_', bx + 12, by + 8, TEXT, self.f_row)

        cw, ch = 54, 36
        cols = max(len(r) for r in rows)
        x0 = (self.ww - cols * cw) // 2
        y0 = by + bh + 14
        r_sel = min(kb.get('r', 0), len(rows) - 1)
        for r, row in enumerate(rows):
            for c, glyph in enumerate(row):
                x = x0 + c * cw
                y = y0 + r * ch
                sel = (r == r_sel and c == min(kb.get('c', 0), len(row) - 1))
                if sel:
                    self.canvas.rect(x + 2, y + 2, cw - 4, ch - 4, SEL_BG)
                    self.canvas.rect(x + 2, y + 2, cw - 4, 3, SEL_EDGE)
                w = self.f_row.width(glyph)
                self.txt(glyph, x + (cw - w) // 2, y + 7,
                         WHITE if sel else TEXT_DIM, self.f_row)

        self.footer([('A', '输入'), ('Y', '退格'), ('X', '完成'),
                     ('L1/R1', '换页'), ('B', '取消')])

    # ------------------------------------------------------------ 主机扫描页
    def render_hostscan(self):
        ns = self.netscan or {}
        hosts = ns.get('hosts') or []
        running = ns.get('running')
        sub = ('扫描中 %d/%d' % (ns.get('done', 0), ns.get('total', 254))
               if running else '%d 台' % len(hosts))
        self.header('扫描局域网', sub)
        y = HEADER_H + 8
        if running and not hosts:
            self.txt('正在探测 %s.* 网段上的 %d 端口…'
                     % (src.local_subnet() or '?', src.SMB_PORT), 20, y + 4, WARN, self.f_row)
            self.txt('约 2 秒，请不要退出', 20, y + 32, TEXT_MUTE, self.f_small)
            self.footer([('B', '返回')])
            return
        if not hosts:
            self.txt('没有发现开放 %d 的主机。' % src.SMB_PORT, 20, y + 4, WARN, self.f_row)
            self.txt('返回上一页，选「主机」用手柄软键盘手动输入。',
                     20, y + 32, TEXT_MUTE, self.f_small)
            self.footer([('B', '返回')])
            return

        top = max(0, min(ns.get('top', 0), len(hosts) - VIS_ROWS))
        ns['top'] = top if len(hosts) > VIS_ROWS else 0
        sel = min(ns.get('sel', 0), len(hosts) - 1)
        for i in range(top, min(top + VIS_ROWS, len(hosts))):
            ip = hosts[i]
            if i == sel:
                self.draw_sel_bar(y)
            cur = (ip == self.source.get('host'))
            self.txt(ip, 22, y, TEXT if i == sel else TEXT_DIM, self.f_row)
            if cur:
                self.txt_r('当前', self.ww - 20, y + 2, GREEN, self.f_small)
            y += ROW_H
        if len(hosts) > VIS_ROWS:
            self.txt_r('%d/%d' % (sel + 1, len(hosts)),
                       self.ww - 20, self.wh - FOOTER_H - 22, TEXT_MUTE, self.f_small)
        self.footer([('A', '选为主机'), ('L1/R1', '翻页'), ('B', '返回')])

    def render(self):
        self.canvas.fill(BG)
        if self.msg:
            self.render_msg()
        elif self.screen == 'systems':
            self.render_systems()
        elif self.screen == 'games':
            self.render_games()
        elif self.screen == 'progress':
            self.render_progress()
        elif self.screen == 'source':
            self.render_source()
        elif self.screen == 'kb':
            self.render_kb()
        elif self.screen == 'hostscan':
            self.render_hostscan()
        else:
            self.render_systems()
        if self.menu_open and not self.msg:
            self.render_menu()
        # 视频帧 blit 完了，告诉播放器这一帧可以回收。
        # 必须在 render 末尾 —— 若在此之前就通知，worker 可能在渲染
        # 还没读完这块 surface 时把它 free 掉（use-after-free）。
        self.vplayer.done()

    # ------------------------------------------------------------ 功能菜单
    def render_menu(self):
        """居中浮层：掌机没有键盘，退出与重扫都收在 Start 菜单里"""
        n = len(self.menu_items)
        row_h, bw = 40, 300
        bh = 44 + n * row_h + 34
        bx = (self.ww - bw) // 2
        by = (self.wh - bh) // 2
        self.canvas.rect(bx - 3, by - 3, bw + 6, bh + 6, SEL_EDGE)
        self.canvas.rect(bx, by, bw, bh, 0xFF262626)
        self.canvas.rect(bx, by, bw, 36, HEADER_BG)
        self.canvas.rect(bx, by + 34, bw, 2, SEL_EDGE)
        self.txt('Start 菜单', bx + 16, by + 6, ACCENT, self.f_row)
        y = by + 50
        for i, (_a, label) in enumerate(self.menu_items):
            if i == self.menu_sel:
                self.canvas.rect(bx + 8, y - 3, bw - 16, row_h - 4, SEL_BG)
                self.canvas.rect(bx + 8, y - 3, 4, row_h - 4, SEL_EDGE)
            col = TEXT if i == self.menu_sel else TEXT_DIM
            # 「预览声音」是个开关，标签要跟着当前状态变（开/关）
            if _a == 'sound':
                label = '预览声音：' + ('开' if self.vplayer.sound else '关')
            self.txt(label, bx + 24, y, col, self.f_row)
            y += row_h
        self.txt('A 选定    B 关闭', bx + 16, by + bh - 24, TEXT_MUTE, self.f_small)

    # ------------------------------------------------------------ 输入
    def handle(self, act):
        if self.msg:
            if act in ('ok', 'back', 'quit'):
                self.msg = None
            return

        # 后台任务完成回执
        if self.worker_result:
            kind, r = self.worker_result
            self.worker_result = None
            if kind == 'scan':
                if r.get('ok'):
                    self.reload_systems()
                    self.status = '索引完成 %.1fs' % r.get('seconds', 0)
                    self.screen = 'systems'
                else:
                    self.msg = {'text': '扫描失败：%s' % r.get('error'), 'color': RED}
            elif kind == 'install':
                if r.get('ok'):
                    self.installed_cache.clear()
                    art = r.get('art') or {}
                    if art.get('ok'):
                        extra = '\n封面已导入：%s' % (r.get('image') or '')
                    elif art.get('reason'):
                        extra = '\n封面未导入（%s）' % art['reason']
                    else:
                        extra = ''
                    self.msg = {'text': '安装成功：%s\n%s%s'
                                        % (r.get('rom_name', ''),
                                           inst.human(r.get('bytes')), extra),
                                'color': GREEN}
                    self.screen = 'games'
                else:
                    self.msg = {'text': '安装失败：%s' % r.get('error'), 'color': RED}
                    self.screen = 'games'
            elif kind == 'remove':
                if not r.get('ok'):
                    self.msg = {'text': '卸载失败：%s' % r.get('error'), 'color': RED}
                elif r.get('nothing'):
                    # 本地根本没有这个游戏——不谎报"已卸载"
                    self.msg = {'text': '本地没有这个游戏（未安装）：%s'
                                        % (r.get('rom_name') or ''), 'color': WARN}
                else:
                    self.installed_cache.clear()
                    art_n = len(r.get('art_moved') or [])
                    extra = '（含封面 %d 个）' % art_n if art_n else ''
                    self.msg = {'text': '已卸载：%s%s'
                                        % (r.get('rom_name') or '', extra),
                                'color': GREEN}
                self.screen = 'games'
            elif kind == 'mount':
                if r.get('ok'):
                    self.msg = {'text': '已连接商店源\n%s\n\n正在重新扫描索引…'
                                        % r.get('unc', ''), 'color': GREEN}
                    self.start_scan(auto=False)
                else:
                    hint = r.get('hint') or '检查主机地址与共享名，确认 NAS 已开机'
                    self.msg = {'text': '连接失败\n\n%s\n\n原因：%s'
                                        % (hint, r.get('error', '')), 'color': RED}
                    self.screen = 'source'
            return

        if self.screen == 'progress':
            return

        # 商店源设置流程（设置页 / 软键盘 / 主机扫描）：Start 在这里等同返回，
        # 避免把 Start 菜单叠在设置页上
        if self.screen in ('source', 'kb', 'hostscan'):
            if act == 'menu':
                act = 'back'
            if self.screen == 'kb':
                self._handle_kb(act)
            elif self.screen == 'hostscan':
                self._handle_hostscan(act)
            else:
                self._handle_source(act)
            return

        # 功能菜单：Select / Start 开关；打开时吞掉其它动作
        if act == 'menu':
            if not self.scanning:
                self.menu_open = not self.menu_open
                self.menu_sel = 0
            return
        if self.menu_open:
            self._handle_menu(act)
            return

        if act == 'quit':
            self.running = False
            return
        if act == 'refresh':
            self.start_scan(auto=False)
            return

        if self.screen == 'systems':
            self._handle_systems(act)
        elif self.screen == 'games':
            self._handle_games(act)

    def _handle_menu(self, act):
        n = len(self.menu_items)
        if act == 'up':
            self.menu_sel = (self.menu_sel - 1) % n
        elif act == 'down':
            self.menu_sel = (self.menu_sel + 1) % n
        elif act == 'back':
            self.menu_open = False
        elif act == 'ok':
            what = self.menu_items[self.menu_sel][0]
            if what == 'sound':
                # 切声音不关菜单：调声音时想看着画面确认，
                # 菜单关掉就没法连着试了。vplayer 会重建 ffmpeg 生效。
                self.vplayer.set_sound(not self.vplayer.sound)
                return
            self.menu_open = False
            if what == 'quit':
                self.running = False
            elif what == 'refresh':
                self.start_scan(auto=False)
            elif what == 'source':
                back = self.screen if self.screen in ('systems', 'games') \
                    else 'systems'
                self.open_source(back)

    # ------------------------------------------------------------ 商店源页输入
    def _handle_source(self, act):
        n = len(SRC_FIELDS) + len(SRC_ACTIONS)
        if act == 'up':
            self.src_sel = (self.src_sel - 1) % n
            return
        if act == 'down':
            self.src_sel = (self.src_sel + 1) % n
            return
        if act == 'install':            # X = 扫描局域网（任何位置都能按）
            self.start_hostscan()
            return
        if act == 'back':
            self.screen = self.src_back_screen
            return
        if act != 'ok':
            return
        if self.src_sel < len(SRC_FIELDS):
            self.open_kb(SRC_FIELDS[self.src_sel][0])
            return
        what = SRC_ACTIONS[self.src_sel - len(SRC_FIELDS)][0]
        if what == 'scan':
            self.start_hostscan()
        elif what == 'save':
            self.start_mount()
        else:
            self.screen = self.src_back_screen

    def _handle_kb(self, act):
        kb = self.kb
        if not kb:
            self.screen = 'source'
            return
        if act == 'back':
            self.kb = None
            self.screen = 'source'
            return
        if act == 'install':            # X = 完成
            self.commit_kb()
            return
        if act == 'delete':             # Y = 退格
            kb['buf'] = kb['buf'][:-1]
            return
        if act == 'ok':
            rows = KB_PAGES[kb['page']]
            r = min(kb['r'], len(rows) - 1)
            c = min(kb['c'], len(rows[r]) - 1)
            if len(kb['buf']) < 48:
                kb['buf'] += rows[r][c]
            return

        rows = KB_PAGES[kb['page']]
        if act == 'pgup':
            kb['page'] = (kb['page'] - 1) % len(KB_PAGES)
        elif act == 'pgdn':
            kb['page'] = (kb['page'] + 1) % len(KB_PAGES)
        elif act == 'up':
            kb['r'] = (kb['r'] - 1) % len(rows)
        elif act == 'down':
            kb['r'] = (kb['r'] + 1) % len(rows)
        elif act == 'left':
            kb['c'] = (kb['c'] - 1) % len(rows[kb['r']])
        elif act == 'right':
            kb['c'] = (kb['c'] + 1) % len(rows[kb['r']])
        else:
            return
        # 换页/移动后夹紧，避免落到不存在的格子
        rows = KB_PAGES[kb['page']]
        kb['r'] = min(kb['r'], len(rows) - 1)
        kb['c'] = min(kb['c'], len(rows[kb['r']]) - 1)

    def _handle_hostscan(self, act):
        ns = self.netscan or {}
        if act == 'back':
            self.screen = 'source'
            return
        hosts = ns.get('hosts') or []
        if ns.get('running') or not hosts:
            return
        n = len(hosts)
        if act == 'up':
            ns['sel'] = (ns['sel'] - 1) % n
        elif act == 'down':
            ns['sel'] = (ns['sel'] + 1) % n
        elif act == 'pgup':
            ns['sel'] = max(0, ns['sel'] - VIS_ROWS)
        elif act == 'pgdn':
            ns['sel'] = min(n - 1, ns['sel'] + VIS_ROWS)
        elif act == 'ok':
            self.source['host'] = hosts[ns['sel']]
            self.screen = 'source'
            return
        else:
            return
        if ns['sel'] < ns.get('top', 0):
            ns['top'] = ns['sel']
        elif ns['sel'] >= ns.get('top', 0) + VIS_ROWS:
            ns['top'] = ns['sel'] - VIS_ROWS + 1

    def _handle_systems(self, act):
        n = len(self.sys_rows)
        if not n:
            return
        if act == 'up':
            self.sys_sel = (self.sys_sel - 1) % n
        elif act == 'down':
            self.sys_sel = (self.sys_sel + 1) % n
        elif act == 'pgup':
            self.sys_sel = max(0, self.sys_sel - VIS_ROWS)
        elif act == 'pgdn':
            self.sys_sel = min(n - 1, self.sys_sel + VIS_ROWS)
        elif act == 'ok':
            self.load_games()
            self.screen = 'games'
            return
        if self.sys_sel < self.sys_top:
            self.sys_top = self.sys_sel
        elif self.sys_sel >= self.sys_top + VIS_ROWS:
            self.sys_top = self.sys_sel - VIS_ROWS + 1

    def _handle_games(self, act):
        """卡片网格的输入：A 装 / X 播 / Y 卸，右摇杆滚简介

        0.5.0 起不再有独立的详情页 —— 详情就是右栏，选中即所见。
        所以 A 直接安装、X 直接播视频，不用先进详情页。

        ## 移动模型（0.6.0 按用户反馈改过，别再改回去）

        | 键 | 作用 |
        |---|---|
        | 左 / 右 | **只在本行内移动**，到行首/行尾就停住 |
        | 上 / 下 | 上下一行；**滚动只由这两个键触发** |
        | L1 / R1 | 翻一整页 |

        两条硬规则，都是用户明确提的：

        1. **左右不跨行。** 跨行会有两个后果：光标斜着跳到下一行第一列，
           而且（配合老的居中视口）整屏一起滚 —— 用户的原话是
           「第三行第一列向右只会向下换行」「网格会出现横向滚动，不够直观」。
           4 列全都在屏上，按不到"看不见的列"，所以不跨行不会漏游戏。
        2. **滚动只在光标要跑出可见区时才发生**（见 render_games 的视口段），
           一次一行。4 行可见时前 3 行完全不滚 —— 用户要的「第四行开始滚动」。
        """
        n = len(self.games)
        if not n:
            if act == 'back':
                self.screen = 'systems'
            return
        cols, rows = self._grid_metrics()[:2]
        per_page = cols * rows
        sel = self.game_sel

        # 右摇杆上下 = 滚动右栏简介。与网格导航互不干扰，各自独立。
        if act == 'rscroll_down':
            self.desc_scroll += 1
            return
        if act == 'rscroll_up':
            self.desc_scroll -= 1
            if self.desc_scroll < 0:
                self.desc_scroll = 0
            return

        # ---- 左右：**只在本行内移动**（0.6.0 改）----------------------------
        # 以前 `sel -= 1 / sel += 1` 是能跨行的：在第 N 行最右按右键会跳到
        # 第 N+1 行第 1 列。配合当时"居中跟随"的视口，整屏还会跟着一起滚，
        # 于是用户看到的就是「按右键，网格往下换了一行」。
        #
        # 现在改成到本行边缘就停住：右键永远只表示"往右"，
        # **不可能引起纵向滚动**（滚动只由上下键触发）。
        # 要在同一列继续往下，用下键 —— 4 列全都在屏上，不存在"看不到右边
        # 那一列"的问题，所以不会因此漏掉任何游戏。
        col = sel % cols
        if act == 'left':
            if col > 0:
                sel -= 1
        elif act == 'right':
            if col < cols - 1 and sel + 1 < n:
                sel += 1
        elif act == 'up':
            if sel - cols >= 0:
                sel -= cols
        elif act == 'down':
            if sel + cols < n:
                sel += cols
        elif act == 'pgup':
            sel = max(0, sel - per_page)
        elif act == 'pgdn':
            # 翻一整页（不是半页）：L1/R1 在底栏写的就是"翻页"
            sel = min(n - 1, sel + per_page)
        elif act == 'back':
            self.screen = 'systems'
            return
        elif act == 'ok':               # A = 安装
            self.start_install(self.games[self.game_sel])
            return
        elif act == 'install':          # X = 全屏播放预览视频
            self.request_play()
            return
        elif act == 'delete':           # Y = 卸载
            self.start_remove(self.games[self.game_sel])
            return
        # 网格**不做循环绕回**：绕回会让「左移到头再右移」又跳回上一行末尾，
        # 在二维布局里非常反直觉。到边就停住。
        sel = max(0, min(sel, n - 1))
        if sel != self.game_sel:
            # 换了游戏，简介回到开头（不跟过去）
            self.desc_scroll = 0
        self.game_sel = sel

    # ------------------------------------------------------------ 主循环
    def stop(self):
        """请求主循环退出（异步安全：只改一个 bool）

        给信号处理器用的。**不要把这里写成抛异常** —— 信号可能在任意两条
        字节码之间到达，抛异常会从 SDL 调用、封面解码、SMB 读中间穿出去，
        轻则漏掉清理，重则把已经释放的 surface 再用一次（段错误）。

        只置位就够了：run() 每帧（约 30fps）都看一次 self.running，
        下一帧就走正常收尾路径（close -> vplayer.shutdown -> 收掉 mpv）。
        """
        self.running = False

    def run(self):
        fps_delay = 1.0 / 30.0
        while self.running:
            t = time.time()
            for a in self.inp.poll(t):
                self.handle(a)
            self.render()
            if self.display:
                self.display.present(self.canvas)
            time.sleep(fps_delay)

    def shot(self, path):
        self.render()
        self.canvas.save_png(path)
        return path

    def close(self):
        # 后台线程是 daemon，但显式停掉更干净（不再往已释放的 SDL 上贴图）。
        # vplayer 要排在 display.close() **之前**停 —— 它的 worker 还在
        # 往 surface 上写东西，而那些 surface 属于已经关掉的 SDL 上下文。
        self.covers.shutdown()
        self.covers.clear()
        self.sizes.clear()
        self.vplayer.shutdown()
        if self.display:
            self.display.close()
        for f in (self.f_title, self.f_row, self.f_small, self.f_big, self.f_tiny):
            try:
                f.close()
            except Exception:
                pass
        self.canvas.free()
        if self.conn:
            self.conn.close()


def run_script(actions, dumpdir, size=(W, H)):
    """无头脚本模式：按顺序执行动作，每步导出一张 PNG（用于远程验证界面）

    支持的动作：任意按键名（up/down/ok/back/...）；
    另有 `goto:<游戏id>` —— 直接跳到该游戏的详情页，省去按键导航
    （远程验证封面时特别有用，不用靠上下翻找出目标游戏）。
    """
    if not os.path.isdir(dumpdir):
        os.makedirs(dumpdir, exist_ok=True)
    app = App(headless=True, size=size)
    out = []
    n = 0
    p = os.path.join(dumpdir, '%02d_%s.png' % (n, app.screen))
    app.shot(p)
    out.append(p)
    for a in actions:
        n += 1
        # 等上一轮后台任务结束
        guard = 0
        while app.worker and app.worker.is_alive() and guard < 600:
            app.worker.join(0.1)
            guard += 1
        if a.startswith('goto:'):
            _goto_game(app, a[5:])
        else:
            app.handle(a)
        # 等本轮后台任务，同时把进度屏也导出一帧
        guard = 0
        dumped_progress = False
        while app.worker and app.worker.is_alive() and guard < 6000:
            app.render()
            if not dumped_progress:
                p = os.path.join(dumpdir, '%02d_%s_progress.png' % (n, a))
                app.shot(p)
                out.append(p)
                dumped_progress = True
            app.worker.join(0.1)
            guard += 1
        name = getattr(app, 'screen', 'x')
        p = os.path.join(dumpdir, '%02d_%s_%s.png' % (n, a.replace(':', '-'), name))
        app.shot(p)
        out.append(p)
    app.close()
    return out


def goto_game(app, gid):
    """把 app 直接定位到某个游戏 id。成功返回 True

    两个用途：
      1. 无头脚本模式的 `goto:<id>`（远程验证界面，不用靠方向键翻找）
      2. 播完预览视频后，RomStore.sh 用 `run.py --resume <id>` 把界面恢复
         到刚才那一张卡片 —— 否则每看一次预览就被打回系统列表，体验很差

    0.5.0 里落点是**网格选中项**（详情已在右栏常驻，没有独立详情页了）。
    """
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return False
    try:
        g = idx.get_game(app.conn, gid)
    except Exception:
        g = None
    if not g:
        return False
    # 让当前系统指到它，返回时不会跳到别的系统
    for i, s in enumerate(app.sys_rows):
        if s['nas_dir'] == g['nas_dir']:
            app.sys_sel = i
            break
    app.load_games()
    # 网格光标也对齐到它，之后按 B 退回列表时正好停在这一张卡片上
    for i, gg in enumerate(app.games):
        if gg['id'] == g['id']:
            app.game_sel = i
            # 落点：把目标摆在**中间一行**（看预览回来时视线不用再找）。
            #
            # ⚠️ 这里必须显式给初值，不能让渲染去反推居中 ——
            # 0.6.0 改成"最小滚动"之后，视口是粘性状态，按键永远不会
            # 主动居中；想去反推居中就等于把用户抱怨过的那个毛病
            # （任何按键都带着整屏滚）装回来。跳转是"一次性定位"，
            # 和"按键导航"是两件事，所以在这里单独算。
            cols, rows = app._grid_metrics()[:2]
            total_rows = (len(app.games) + cols - 1) // cols
            top_row = i // cols - rows // 2
            top_row = max(0, min(top_row, max(0, total_rows - rows)))
            app.game_top = top_row * cols
            return True
    return False


# 旧名保留（无头脚本模式一路这么叫过来的）
_goto_game = goto_game
