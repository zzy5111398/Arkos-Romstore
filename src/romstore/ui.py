# -*- coding: utf-8 -*-
"""SDL2 中文界面

屏幕 640x480（R36S），布局：
  ┌ 顶栏 52px  标题 + 索引概况
  ├ 列表区     滚动，每行 30px，约 12 行
  └ 底栏 44px  按键提示

按键（掌机没有键盘，全部动作都有手柄绑定）：
  十字键/左摇杆  上下移动      A            确认 / 进入
  B              返回          L1 / R1      上/下翻页
  X              安装          Y            卸载
  Start          菜单（重扫 NAS / 修改商店源 / 退出商店）

软键盘（screen='kb'，掌机没键盘，所有文本都靠它输入）：
  十字键/左摇杆  选字符       A            输入该字符
  Y              退格          X            完成
  L1 / R1        切换字符页    B            取消

  —— 下面这些键盘键只用于桌面调试，掌机上按不到 ——
  Enter 确认 / Esc 返回 / PageUp·PageDown 翻页 / I 安装 / D 卸载
  M 菜单 / R 重扫 / Q 退出
"""
import os
import threading
import time

from . import config
from . import index as idx
from . import install as inst
from . import media as media_mod
from . import meta
from . import sdl
from . import source as src

W, H = 640, 480
HEADER_H = 52
FOOTER_H = 44
ROW_H = 30
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
    """按像素宽度对中文做换行（中文按字符切，英文按词尽量切）"""
    lines = []
    for para in text.split('\n'):
        para = para.strip()
        if not para:
            continue
        cur = ''
        for ch in para:
            if fonts.width(cur + ch) <= maxw:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
                if len(lines) >= maxlines:
                    break
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
        self.detail_game = None
        self.detail_desc = ''
        self.detail_scroll = 0
        self.msg = None
        # Start 菜单（Start/Select 打开）：掌机没有键盘，退出/重扫/换源都收在这里
        self.menu_open = False
        self.menu_sel = 0
        self.menu_items = [('refresh', '重扫 NAS'),
                           ('source', '修改商店源'),
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
        self.worker = None
        self.worker_result = None
        self.running = True
        self.conn = None
        self.desc_cache = {}
        self.installed_cache = {}

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

        def prog(phase, done, total, msg):
            self.progress = {'phase': phase, 'done': done, 'total': total, 'msg': msg}

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
        y0 = self.wh - FOOTER_H
        self.canvas.rect(0, y0, self.ww, FOOTER_H, HEADER_BG)
        self.canvas.rect(0, y0, self.ww, 2, 0xFF3A3A3A)
        x = 14
        for key, label in hints:
            self.txt(key, x, y0 + 10, ACCENT, self.f_small)
            x += self.f_small.width(key) + 6
            self.txt(label, x, y0 + 10, TEXT_DIM, self.f_small)
            x += self.f_small.width(label) + 18

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

    # ------------------------------------------------------------ 游戏屏
    def render_games(self):
        s = self.current_system()
        if not s:
            self.screen = 'systems'
            return self.render_systems()
        self.header(s['nas_dir'] + ('  ' + s['note'] if s['note'] else ''),
                    '%d 款' % len(self.games))
        if not self.games:
            self.txt('该系统没有可安装的游戏。', 20, 120, WARN, self.f_row)
            self.footer([('B', '返回')])
            return
        top = max(0, min(self.game_top, len(self.games) - VIS_ROWS))
        self.game_top = top if len(self.games) > VIS_ROWS else 0
        y = HEADER_H + 8
        for i in range(top, min(top + VIS_ROWS, len(self.games))):
            g = self.games[i]
            if i == self.game_sel:
                self.draw_sel_bar(y)
            col = TEXT if i == self.game_sel else TEXT_DIM
            self.txt(g['title'], 22, y, col, self.f_row)
            tags = []
            if g['is_multi']:
                tags.append('多碟')
            if g['has_boxart']:
                tags.append('封面')
            if self.is_installed(g):
                tags.append('已装')
            if tags:
                self.txt_r('/'.join(tags), self.ww - 20, y + 2,
                           GREEN if '已装' in tags else TEXT_MUTE, self.f_small)
            y += ROW_H
        if len(self.games) > VIS_ROWS:
            self.txt_r('%d/%d' % (self.game_sel + 1, len(self.games)),
                       self.ww - 20, self.wh - FOOTER_H - 22, TEXT_MUTE, self.f_small)
        self.footer([('A', '详情'), ('X', '安装'), ('Y', '卸载'),
                     ('L1/R1', '翻页'), ('B', '返回'), ('Start', '菜单')])

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

    # ------------------------------------------------------------ 详情屏
    def load_desc(self, g):
        key = g['id']
        if key in self.desc_cache:
            return self.desc_cache[key]
        d = ''
        try:
            mp = os.path.join(config.NAS_ROOT, g['nas_dir'], config.METADATA_NAME)
            ds = meta.parse_descriptions(mp, {g['title']})
            d = ds.get(g['title'], '')
        except Exception:
            d = ''
        self.desc_cache[key] = d
        return d

    def render_detail(self):
        g = self.detail_game
        if not g:
            self.screen = 'games'
            return self.render_games()
        self.header(g['title'], g['nas_dir'])
        srcs, total = inst.rom_sources(self.conn, g)

        # 右栏：封面预览（NAS 上有图就先给用户看，装之前就能确认刮削对不对）
        pad = 16
        gap = 14
        prev_w, prev_h = 150, 200
        prev_x = self.ww - prev_w - pad
        prev_y = HEADER_H + 10
        surf = None
        cov = media_mod.find_asset(
            os.path.join(config.NAS_ROOT, g['nas_dir']), g['rom_rel'] or '',
            'box_front', g['media_dir'] if 'media_dir' in g.keys() else None)
        if cov[0]:
            surf = self.imgs.thumb(cov[0], prev_w, prev_h)
        self.canvas.rect(prev_x, prev_y, prev_w, prev_h, 0xFF232323)
        if surf is not None:
            sw = surf.contents.w
            sh = surf.contents.h
            ox = prev_x + (prev_w - sw) // 2
            self.canvas.blit(surf, ox, prev_y + (prev_h - sh) // 2)
        else:
            self.txt_r('无封面', prev_x + prev_w - 6, prev_y + prev_h // 2 - 8,
                       TEXT_MUTE, self.f_small)
        self.canvas.rect(prev_x, prev_y, 1, prev_h, 0xFF3A3A3A)
        self.canvas.rect(prev_x, prev_y + prev_h - 1, prev_w, 1, 0xFF3A3A3A)
        cover_bottom = prev_y + prev_h

        # 左栏：信息（宽度让出封面那一栏，长路径按像素截断）
        info_x = 20
        info_vx = 90
        info_right = prev_x - gap
        y = HEADER_H + 10
        lines = [
            ('系统', '%s  ->  %s' % (g['es_name'], g['es_path'])),
            ('ROM', '%d 个文件, %s' % (len(srcs), inst.human(total))),
            ('core', g['core'] or '-'),
            ('状态', '已安装' if self.is_installed(g) else '未安装'),
        ]
        if srcs:
            lines.insert(2, ('文件', os.path.basename(srcs[0][1])))
        for k, v in lines:
            self.txt(k, info_x, y, TEXT_MUTE, self.f_small)
            self.txt(self.clip(v, info_right - info_vx), info_vx, y,
                     TEXT if k == '状态' and '未' not in v else TEXT_DIM, self.f_small)
            y += 22

        # 分隔线以下才是简介：**必须从封面底边之后开始**，否则文字会从图后面穿过去
        y = max(y + 6, cover_bottom + 6)
        self.canvas.rect(pad, y, self.ww - pad * 2, 2, 0xFF3A3A3A)
        y += 8
        body_h = self.wh - FOOTER_H - y - 6
        maxlines = max(1, body_h // 22)
        wl = wrap(self.detail_desc, self.f_row, self.ww - pad * 2, maxlines + self.detail_scroll)
        wl = wl[self.detail_scroll:self.detail_scroll + maxlines]
        for ln in wl:
            self.txt(ln, pad, y, TEXT_DIM, self.f_row)
            y += 22
        free = inst.human(inst.free_bytes(g['es_path']))
        self.footer([('A/X', '安装'), ('Y', '卸载'), ('L1/R1', '滚动'), ('B', '返回'),
                     ('Start', '菜单'), ('', '可用 ' + free)])

    # ------------------------------------------------------------ 进度屏
    def render_progress(self):
        self.header('处理中', '')
        p = self.progress or {}
        phase = PHASE_CN.get(p.get('phase', ''), p.get('phase', ''))
        done, total = p.get('done', 0), p.get('total', 1)
        pct = (done / float(total)) if total else 0.0
        self.txt(str(phase), 20, HEADER_H + 40, ACCENT, self.f_row)
        self.txt(str(p.get('msg', ''))[:46], 20, HEADER_H + 72, TEXT, self.f_small)
        bx, by, bw, bh = 20, HEADER_H + 120, self.ww - 40, 26
        self.canvas.rect(bx, by, bw, bh, 0xFF2E2E2E)
        self.canvas.rect(bx, by, int(bw * max(0.0, min(1.0, pct))), bh, SEL_EDGE)
        self.txt('%.1f%%' % (pct * 100), bx + 10, by + 3, WHITE, self.f_small)
        if total > 1:
            self.txt_r('%s / %s' % (inst.human(done), inst.human(total)),
                       bx + bw - 10, by + 3, WHITE, self.f_small)
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
        elif self.screen == 'detail':
            self.render_detail()
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
                    self.screen = 'detail' if self.detail_game else 'games'
                else:
                    self.msg = {'text': '安装失败：%s' % r.get('error'), 'color': RED}
                    self.screen = 'detail'
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
                self.screen = 'detail' if self.detail_game else 'games'
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
        elif self.screen == 'detail':
            self._handle_detail(act)

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
            self.menu_open = False
            if what == 'quit':
                self.running = False
            elif what == 'refresh':
                self.start_scan(auto=False)
            elif what == 'source':
                back = self.screen if self.screen in ('systems', 'games', 'detail') \
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
        n = len(self.games)
        if not n:
            if act in ('back',):
                self.screen = 'systems'
            return
        if act == 'up':
            self.game_sel = (self.game_sel - 1) % n
        elif act == 'down':
            self.game_sel = (self.game_sel + 1) % n
        elif act == 'pgup':
            self.game_sel = max(0, self.game_sel - VIS_ROWS)
        elif act == 'pgdn':
            self.game_sel = min(n - 1, self.game_sel + VIS_ROWS)
        elif act == 'back':
            self.screen = 'systems'
            return
        elif act == 'ok':
            self.detail_game = self.games[self.game_sel]
            self.detail_desc = self.load_desc(self.detail_game)
            self.detail_scroll = 0
            self.screen = 'detail'
            return
        elif act == 'install':
            self.start_install(self.games[self.game_sel])
            return
        elif act == 'delete':
            self.start_remove(self.games[self.game_sel])
            return
        if self.game_sel < self.game_top:
            self.game_top = self.game_sel
        elif self.game_sel >= self.game_top + VIS_ROWS:
            self.game_top = self.game_sel - VIS_ROWS + 1

    def _handle_detail(self, act):
        if act == 'back':
            self.screen = 'games'
            return
        if act in ('ok', 'install'):
            if self.detail_game:
                self.start_install(self.detail_game)
            return
        if act == 'delete':
            if self.detail_game:
                self.start_remove(self.detail_game)
            return
        if act == 'up':
            self.detail_scroll = max(0, self.detail_scroll - 1)
        elif act == 'down':
            self.detail_scroll += 1
        elif act == 'pgup':
            self.detail_scroll = max(0, self.detail_scroll - 8)
        elif act == 'pgdn':
            self.detail_scroll += 8

    # ------------------------------------------------------------ 主循环
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
        if self.display:
            self.display.close()
        for f in (self.f_title, self.f_row, self.f_small, self.f_big):
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


def _goto_game(app, gid):
    """把 app 直接切到某个游戏 id 的详情页（无头验证用）"""
    try:
        gid = int(gid)
    except ValueError:
        return
    try:
        g = idx.get_game(app.conn, gid)
    except Exception:
        g = None
    if not g:
        return
    # 让当前系统/列表也指到它，返回时不会跳到别的系统
    for i, s in enumerate(app.sys_rows):
        if s['nas_dir'] == g['nas_dir']:
            app.sys_sel = i
            break
    app.load_games()
    app.detail_game = g
    app.detail_desc = app.load_desc(g)
    app.detail_scroll = 0
    app.screen = 'detail'
