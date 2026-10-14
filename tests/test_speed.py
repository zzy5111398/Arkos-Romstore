# -*- coding: utf-8 -*-
"""进度页「复制速度计」的离线自测（不碰真机、不联网、不建 SDL 显示）

跑法：

    python3 tests/test_speed.py

盯住四件事（每一条都对应真机上真出过或差点出的问题）：

  1. **停顿要衰减到 0** —— 复制卡住时不能一直挂着最后一个好看的数字。
     也不能一停就报 0：512KB 的分块粒度下，慢速传输本来就一两个样本
     才来一次（200 KB/s 时 2.5 秒一个）。
  2. **时间戳没被初始化时不能显示天文数字** —— 真机上出过
     「已用 497728 小时 25 分」（`time.time() - 0`）。
  3. **除零 / 单样本 / 乱序样本不能抛异常** —— 它跑在渲染主循环里，
     抛一个就是整屏崩掉。
  4. **文件头声称的行为与实现一致**（`_SP_WINDOW` / `_SP_STALL`）。

⚠️ 这里**不构造 ui.App**：那要 SDL 和字体。速度计的几个方法只碰
`_sp / _sp_lock / _sp_at / t_start`，所以直接拿一个"壳"当 self 调即可 ——
既不损失覆盖（测的就是这几个方法本身），也不依赖运行环境。
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

from romstore import ui   # noqa: E402

FAIL = []
MB = 1024 * 1024


def check(name, cond, extra=''):
    print(('  [OK] ' if cond else '  [FAIL] ') + name
          + ('  ' + str(extra) if extra else ''))
    if not cond:
        FAIL.append(name)


class Shell(ui.App):
    """只带速度计要用的那几个属性的假 self

    **继承 ui.App 但不走 App.__init__**（那要 SDL 显示和字体）。
    继承是为了拿到 `_SP_WINDOW` / `_SP_STALL` / `_SP_HIST` 这几个类常量
    和方法的真实绑定 —— 自己抄一份的话，哪天产品侧调了窗口长度，
    测试还在按老值验，等于没测。
    """

    def __init__(self):
        self._sp_lock = threading.Lock()
        self._sp = []
        self._sp_hist = []
        self._sp_at = 0.0
        self.t_start = 0.0


def feed(sh, pairs):
    """按 (时刻偏移秒, 已传字节) 回放样本 —— 直接写 _sp，绕开 time.time()"""
    now = time.time()
    for dt, done in pairs:
        sh._sp.append((now - 3.0 + dt, done))
    sh._sp_at = sh._sp[-1][0] if sh._sp else 0.0
    sh._sp.sort()


# --------------------------------------------------------------- 1. 基本
print('== 1. 基本读数 ==')
sh = Shell()
sh.speed_reset()
# 直接用时间戳回放，不靠 sleep —— 睡出来的间隔不确定（实测这么写会得到
# 82 MB/s 而不是想要的 4 MB/s，测试自己先假了）
feed(sh, [(0.0, 0), (1.0, 4 * MB), (2.0, 8 * MB), (3.0, 12 * MB)])
sp = sh.copy_speed()
check('4 个样本能算出速度', sp > 0, sh.human_speed(sp))
# 分母是 now - 第一个样本（第一个样本在 3 秒前），分子 12MB
check('速度等于 12MB/3s = 4 MB/s（误差 <15%）',
      3.4 * MB <= sp <= 4.6 * MB, '%.2f MB/s' % (sp / MB))
check('human_speed 读数是 4.00 MB/s', sh.human_speed(sp) == '4.00 MB/s',
      sh.human_speed(sp))

print('== 2. 停顿必须衰减到 0（而不是挂着最后一个数字）==')
# 把最后一个样本的时刻往回推 5 秒 = 5 秒没有任何新数据
sh._sp = [(t - 8.0, 10 * MB) for t in [time.time()]]
sh._sp.append((time.time() - 5.0, 40 * MB))
sh._sp_at = time.time() - 5.0
check('停 5 秒后速度为 0', sh.copy_speed() == 0.0, sh.human_speed(sh.copy_speed()))
check('停顿时 human_speed 给 0 KB/s', sh.human_speed(sh.copy_speed()) == '0 KB/s')

print('== 3. 慢速不能因为样本稀疏被误判成停顿 ==')
sh2 = Shell()
sh2.speed_reset()
now = time.time()
# 200 KB/s、512KB 一个样本 -> 2.4 秒一个样本，最后一个样本就在刚才
sh2._sp = [(now - 2.4, 0), (now - 0.2, 512 * 1024)]
sh2._sp_at = now - 0.2
slow = sh2.copy_speed()
check('慢速仍有读数（>0）', slow > 0, sh2.human_speed(slow))
check('慢速读数没被摊成 0.1 倍以下', slow > 0.25 * 200 * 1024,
      '%.0f KB/s' % (slow / 1024))

print('== 4. t_start 没初始化时「已用」不能是天文字（真机踩过）==')
sh3 = Shell()
sh3.t_start = 0.0
sh3._sp = [(time.time() - 7.0, 0), (time.time(), 10 * MB)]
sh3._sp_at = time.time()
el = sh3.speed_elapsed()
check('无 t_start 时用第一个样本当起点', 6.0 <= el <= 8.5, '%.1f 秒' % el)
check('human_time 不会输出几万小时', '497728' not in ui.App.human_time(el),
      ui.App.human_time(el))

print('== 5. human_time / human_speed 档位 ==')
check('0 秒 -> --', ui.App.human_time(0) == '--')
check('负数 -> --', ui.App.human_time(-5) == '--')
check('None -> --', ui.App.human_time(None) == '--')
check('45 秒', ui.App.human_time(45) == '45 秒', ui.App.human_time(45))
check('200 秒 -> 3 分 20 秒', ui.App.human_time(200) == '3 分 20 秒',
      ui.App.human_time(200))
check('3900 秒 -> 1 小时 05 分', ui.App.human_time(3900) == '1 小时 05 分',
      ui.App.human_time(3900))
check('100 小时以上 -> --（坏数据不显示）',
      ui.App.human_time(200 * 3600) == '--')
check('1 MB/s -> 1.00 MB/s', ui.App.human_speed(1 * MB) == '1.00 MB/s',
      ui.App.human_speed(1 * MB))
check('512 KB/s -> 512 KB/s（KB 不取小数）',
      ui.App.human_speed(512 * 1024) == '512 KB/s',
      ui.App.human_speed(512 * 1024))
check('0 -> 0 KB/s', ui.App.human_speed(0) == '0 KB/s')

print('== 6. 退化输入不能抛异常（它在渲染主循环里）==')
bad = []
for name, fn in (
        ('空样本 copy_speed', lambda: Shell().copy_speed()),
        ('空样本 speed_elapsed', lambda: Shell().speed_elapsed()),
        ('单样本 copy_speed', lambda: (lambda s: (s.speed_sample(1),
                                                 s.copy_speed())[1])(Shell())),
        ('零字节增量', lambda: (lambda s: (s._sp.extend(
            [(time.time() - 1, 5), (time.time(), 5)]), s.copy_speed())[1])(Shell())),
        ('时间不动（span=0）', lambda: (lambda s: (s._sp.extend(
            [(time.time(), 1), (time.time(), 9)]), s.copy_speed())[1])(Shell())),
        ('历史为空时取历史', lambda: Shell().speed_history()),
):
    try:
        fn()
    except Exception as e:
        bad.append('%s -> %s: %s' % (name, type(e).__name__, e))
check('所有退化输入都安静', not bad, '; '.join(bad))

print('== 7. 样本窗口有界（长时间运行不能无限涨）==')
# 7a 真实节奏：一小时、每 0.2 秒一个样本（2.5 MB/s 下 512KB 就是这么密），
#    窗口应该把它裁到十几个
sh6 = Shell()
sh6.speed_reset()
now = time.time()
for i in range(18000):
    sh6._sp_lock.acquire()
    sh6._sp.append((now - 3600 + i * 0.2, i * 512 * 1024))
    sh6._sp_lock.release()
sh6.speed_sample(18000 * 512 * 1024)        # 触发一次裁剪
check('真实节奏下窗口只有几十个样本', len(sh6._sp) < 40, '%d 个' % len(sh6._sp))

# 7b 病态：时间戳几乎不前进（极快本地复制 / 假时间）。窗口裁剪是按
#    "头部够不够老"推进的，这种输入下它永远推不动 —— 硬上限必须兜住
sh4 = Shell()
sh4.speed_reset()
for i in range(5000):
    sh4._sp_lock.acquire()
    sh4._sp.append((time.time() - 999 + i * 0.0001, i * 512))
    sh4._sp_lock.release()
    sh4.speed_sample(i * 512)
check('病态输入被硬上限兜住（<= %d）' % ui.App._SP_MAX,
      len(sh4._sp) <= ui.App._SP_MAX, '%d 个' % len(sh4._sp))
check('柱状图历史有上限（<= %d）' % ui.App._SP_HIST,
      len(sh4.speed_history()) <= ui.App._SP_HIST,
      '%d 格' % len(sh4.speed_history()))

print('== 8. speed_reset 真的清空 ==')
sh5 = Shell()
sh5.speed_sample(1)
sh5.speed_sample(2)
sh5.speed_reset()
check('reset 后样本为空', sh5._sp == [])
check('reset 后历史为空', sh5._sp_hist == [])
check('reset 后速度为 0', sh5.copy_speed() == 0.0)
check('reset 会重设 t_start', sh5.t_start > 0)

print('== 9. 相位门：只有 copy 阶段该画速度 ==')
src = open(os.path.join(ROOT, 'src', 'romstore', 'ui.py'), encoding='utf-8').read()
check("render_progress 里有 phase == 'copy' 的判断",
      "phase == 'copy'" in src)
check("prog 回调只在 copy 阶段采样",
      "if phase == 'copy' and total > 1" in src)
check('速度区在总字节 <=1 时不画（没意义的场景）',
      "phase == 'copy' and total > 1" in src)

print()
if FAIL:
    print('有 %d 项未通过：' % len(FAIL))
    for n in FAIL:
        print('  - ' + n)
    sys.exit(1)
print('全部通过')
