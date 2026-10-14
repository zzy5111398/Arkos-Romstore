# -*- coding: utf-8 -*-
"""卡片网格导航 / 纵向滚动的离线自测（不碰真机、不用 SDL）

跑法：

    python3 tests/test_nav.py

这是 0.6.0 按用户反馈改动的守门测试。他的四句话：

  1. 「网格应该只进行纵向滚动」
  2. 「现在从第三行就开始滚动了，应该改成第四行开始滚动」
  3. 「第三行第一列向右只会向下换行」
  4. 「网格会出现横向滚动，不够直观」

四条其实是**同一个根因**：视口以前是「居中跟随」（`top = sel - 每屏//2`），
所以选中项一超过第 8 个（4 列布局下就是第 3 行）视口就一格一格跟着滚，
光标被钉在第 3 行不动。用户按右键看到的就是"网格往下换了一行"，
而且**任何按键都会带着整屏滚动**。

第 2 节把「右键绝不引起纵向滚动」写成了全量断言（对每一个游戏都试一遍），
这是这个 bug 最不容易再犯的写法。

⚠️ 不构造 ui.App（要 SDL 和字体）：继承它但不跑 __init__，
只摆导航要用的那几个状态，并把 _grid_metrics 固定成 4 列 x 4 行 ——
真机上实测就是这个布局（640x480）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

from romstore import ui   # noqa: E402

FAIL = []
COLS, ROWS = 4, 4


def check(name, cond, extra=''):
    print(('  [OK] ' if cond else '  [FAIL] ') + name
          + ('  ' + str(extra) if extra else ''))
    if not cond:
        FAIL.append(name)


class Shell(ui.App):
    """只带网格导航要用的状态的假 self"""

    def __init__(self, n=64):
        self.games = [{'id': i, 'title': 'g%d' % i} for i in range(n)]
        self.game_sel = 0
        self.game_top = 0
        self.desc_scroll = 0
        self.screen = 'games'

    def _grid_metrics(self):
        # 真机 640x480 实测就是 4 列 x 4 行
        return COLS, ROWS, 91, 91, 400

    def press(self, act):
        """模拟"按一下键 + 紧接着渲染一帧"

        视口更新发生在 render_games 里（见 _grid_viewport），不在按键里 ——
        所以按键之后必须再跑一次视口计算，否则测的是半个流程。
        """
        self._handle_games(act)
        self._grid_viewport(COLS, ROWS)

    def vis_row(self):
        """选中项在**屏幕上**是第几行（0 起）"""
        return (self.game_sel - self.game_top) // COLS


def fresh(n=64, sel=0, top=0):
    s = Shell(n)
    s.game_sel, s.game_top = sel, top
    s._grid_viewport(COLS, ROWS)     # 先让视口自洽
    return s


print('== 1. 视口：4 行可见时，前 3 行完全不滚 ==')
for row in range(ROWS - 1):                     # 第 1~3 行
    s = fresh(sel=row * COLS)
    tops = set()
    for c in range(COLS):
        s.game_sel = row * COLS + c
        tops.add(s._grid_viewport(COLS, ROWS))
    check('第 %d 行四个位置滚动量都是 0' % (row + 1), tops == {0}, tops)

print('== 2. 第 4 行再往下才滚（一次一行）==')
s = fresh(sel=3 * COLS)                         # 第 4 行第一列
check('光标在第 4 行时视口仍是 0', s._grid_viewport(COLS, ROWS) == 0, s.game_top)
s.press('down')
check('第 4 行按"下"后光标到第 5 行', s.game_sel == 16, s.game_sel)
check('视口只滚了**一行**（0 -> 4）', s.game_top == 4, s.game_top)
check('光标仍落在屏幕上第 4 行', s.vis_row() == ROWS - 1, s.vis_row())
s.press('down')
check('再按一次又滚一行（4 -> 8）', s.game_top == 8, s.game_top)
check('光标仍在屏幕上第 4 行', s.vis_row() == ROWS - 1, s.vis_row())

print('== 3. 往上：光标跑出上沿才回滚 ==')
s = fresh(sel=20, top=8)                        # 光标在屏幕第 4 行
check('起点 sel=20 top=8', (s.game_sel, s.game_top) == (20, 8),
      '%s' % ((s.game_sel, s.game_top),))
s.press('up')
check('往上第 1 步：视口不动（还在可见区）', (s.game_sel, s.game_top) == (16, 8),
      '%s' % ((s.game_sel, s.game_top),))
s.press('up')
check('往上第 2 步：视口仍不动', (s.game_sel, s.game_top) == (12, 8),
      '%s' % ((s.game_sel, s.game_top),))
s.press('up')
check('往上第 3 步：正好到可见区顶行，仍不滚', (s.game_sel, s.game_top) == (8, 8),
      '%s' % ((s.game_sel, s.game_top),))
s.press('up')
check('往上第 4 步：跑出上沿 -> 回滚一行（8 -> 4）',
      (s.game_sel, s.game_top) == (4, 4),
      '%s' % ((s.game_sel, s.game_top),))
s.press('up')
check('往上第 5 步：回到第一行，视口归零', (s.game_sel, s.game_top) == (0, 0),
      '%s' % ((s.game_sel, s.game_top),))

print('== 4. 左右**绝不**跨行、绝不引起纵向滚动 ==')
s = fresh(sel=8)                                # 用户原话里的"第三行第一列"
check('起点：第 3 行第 1 列，top=0', s.game_sel == 8 and s.game_top == 0,
      'sel=%d top=%d' % (s.game_sel, s.game_top))
s.press('right')
check('按右键 -> 往右一格（不是往下换行）', s.game_sel == 9, s.game_sel)
check('按右键 -> 视口纹丝不动', s.game_top == 0, s.game_top)
check('光标还在屏幕上第 3 行', s.vis_row() == 2, s.vis_row())
s.press('right')
s.press('right')
check('再往右到第 4 列', s.game_sel == 11, s.game_sel)
s.press('right')
check('第 4 列再按右键 -> 停住（不跨行）', s.game_sel == 11, s.game_sel)
s.press('left')
s.press('left')
s.press('left')
check('往左回到第 1 列', s.game_sel == 8, s.game_sel)
s.press('left')
check('第 1 列再按左键 -> 停住（不跨行）', s.game_sel == 8, s.game_sel)

# 全量断言：任意位置按右键，视口都不许动
worst = []
for row in range(4):
    for c in range(COLS):
        sel = row * COLS + c
        s = fresh(sel=sel)
        before = s.game_top
        s.press('right')
        if s.game_top != before:
            worst.append('sel=%d top %d->%d' % (sel, before, s.game_top))
check('**任意位置**按右键都不会滚动网格', not worst, '; '.join(worst[:3]))
worst = []
for row in range(4):
    for c in range(COLS):
        sel = row * COLS + c
        s = fresh(sel=sel)
        before = s.game_top
        s.press('left')
        if s.game_top != before:
            worst.append('sel=%d top %d->%d' % (sel, before, s.game_top))
check('**任意位置**按左键都不会滚动网格', not worst, '; '.join(worst[:3]))

print('== 5. 上下键到边就停（不绕回）==')
s = fresh(sel=0)
s.press('up')
check('第一行按上 -> 停在 0', s.game_sel == 0, s.game_sel)
s.press('left')
check('第一列按左 -> 停在 0', s.game_sel == 0, s.game_sel)
s = fresh(n=14, sel=12)                         # 14 款：最后一行只有 2 个
s.press('down')
check('最后一行按下 -> 按不下去（不是跳到不存在的格子）',
      s.game_sel == 12, s.game_sel)
s.press('right')
check('最后一行往右到第 2 个', s.game_sel == 13, s.game_sel)
s.press('right')
check('最后一行第 2 个再往右 -> 停住', s.game_sel == 13, s.game_sel)

print('== 6. L1/R1 翻整页 ==')
s = fresh(sel=0)
s.press('pgdn')
check('pgdn 翻 16 个（一整页）', s.game_sel == 16, s.game_sel)
check('pgdn 后视口跟着滚到第 5 行起', s.game_top == 16 - (ROWS - 1) * COLS,
      s.game_top)
check('光标仍在屏幕上第 4 行', s.vis_row() == ROWS - 1, s.vis_row())
s.press('pgup')
check('pgup 翻回来', s.game_sel == 0, s.game_sel)
check('pgup 后视口回到 0', s.game_top == 0, s.game_top)
s = fresh(n=20, sel=19)
s.press('pgdn')
check('末尾翻页不越界', s.game_sel == 19, s.game_sel)

print('== 7. 视口不会露出空白行 ==')
bad = []
for n in (1, 2, 4, 13, 14, 16, 17, 20, 33, 64, 65):
    for sel in range(n):
        s = Shell(n)
        s.game_sel = sel
        s.game_top = 0
        top = s._grid_viewport(COLS, ROWS)
        if top >= n:                       # 首行都是空的
            bad.append('n=%d sel=%d top=%d' % (n, sel, top))
        elif top % COLS != 0:              # 必须整行对齐，否则会滚出半行
            bad.append('n=%d sel=%d top=%d 没对齐' % (n, sel, top))
        elif top and top + ROWS * COLS > n + COLS:
            # 尾部允许留不满一页，但不能整行空着
            if top >= ((n + COLS - 1) // COLS) * COLS:
                bad.append('n=%d sel=%d top=%d 整行空白' % (n, sel, top))
check('各种总数下视口都合法', not bad, '; '.join(bad[:3]))

print('== 8. 粘性：view/down 反复按不会漂移 ==')
s = fresh(sel=0)
for _ in range(3):          # 光标下到第 4 行，视口该开始滚
    s.press('down')
t1 = (s.game_sel, s.game_top)
s._grid_viewport(COLS, ROWS)
s._grid_viewport(COLS, ROWS)
check('重复渲染不改变视口（粘性，不是每帧反推）',
      (s.game_sel, s.game_top) == t1, '%s vs %s' % ((s.game_sel, s.game_top), t1))

print('== 9. ' + '源码里不存在"居中跟随"的老写法 ==')
src = open(os.path.join(ROOT, 'src', 'romstore', 'ui.py'), encoding='utf-8').read()
check('没有 sel - per_page // 2 这种反推',
      'per_page // 2' not in src)
check('左右键都不再直接 sel -= 1 / sel += 1',
      "elif act == 'right':\n            if col < cols - 1" in src)

print()
if FAIL:
    print('有 %d 项未通过：' % len(FAIL))
    for n in FAIL:
        print('  - ' + n)
    sys.exit(1)
print('全部通过')
