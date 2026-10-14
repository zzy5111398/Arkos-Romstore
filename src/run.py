#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROM 商店客户端入口

真机运行（从 ES 菜单启动「ROM 商店」；不要手敲 —— 这个程序会独占显示，
SSH 里直接跑会把会话挂住）：
    ArkOS    ES -> Options -> ROM 商店     实际调用 /home/ark/.romstore/run.py
    ROCKNIX  ES -> Ports  -> ROM 商店      实际调用 /storage/.romstore/run.py

无头脚本模式（开发/远程验证，逐步导出界面截图）：
    python3 run.py --script "down,down,ok,down,ok" --dump /tmp/ui

播放预览视频后的回跳（由 RomStore.sh 调用，一般不用手敲）：
    python3 run.py --resume 1234      # 直接停在游戏 id=1234 的详情页

清孤儿（由 RomStore.sh 在收尾时调用，一般不用手敲）：
    python3 run.py --sweep            # 杀掉上次残留的 mpv 帧服务进程
"""
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from romstore import ui   # noqa: E402
from romstore import sdl  # noqa: E402
from romstore import vplayer  # noqa: E402


def sweep():
    """杀掉上一次运行遗留的 mpv 帧服务进程（详见 vplayer.sweep_stale）"""
    n = vplayer.sweep_stale(quiet=False)
    print('清理遗留的预览进程: %d 个' % n)
    return 0


def _install_signal_handlers(app):
    """把 SIGTERM/SIGINT/SIGHUP 变成「请主循环正常收尾」

    ## 为什么必须有（真机踩过，不是理论问题）

    Python 对 SIGTERM 的**默认动作是立即终止进程** —— `finally` 不执行、
    `app.close()` 不执行，而 `close()` 里那句 `vplayer.shutdown()` 正是
    收掉 mpv 的唯一机会。于是 mpv 被 init 收养（`ppid=1`）继续跑：
    一直往 `/tmp` 灌 jpg（实测 10 分钟 2741 个 / 22MB），还占着声卡不放。
    实测一次残留了 **3 个**。

    处理方式：只调 `app.stop()` 置位，让主循环下一帧自己退出
    （不抛异常，理由见 ui.App.stop 的注释）。

    Windows 上除了 SIGINT 之外的信号本来就设不上，所以逐个 try ——
    开发机跑 `--script` 无头模式时不能因为这个炸掉。
    """
    if app is None:
        return

    def _bye(signum, frame):
        app.stop()

    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        s = getattr(signal, name, None)
        if s is None:
            continue
        try:
            signal.signal(s, _bye)
        except (ValueError, OSError, RuntimeError, AttributeError):
            pass


def probe():
    """环境自检：视频驱动 / 屏幕尺寸 / 手柄识别 / 字体"""
    b = sdl.Bind()
    v = b.version()
    print('SDL 版本      : %d.%d.%d' % v)
    print('视频驱动(编译): %s' % ', '.join(b.video_drivers()))
    print('SDL_VIDEODRIVER: %s' % os.environ.get('SDL_VIDEODRIVER', '(未设)'))
    print('手柄映射文件  : %s  %s'
          % (os.environ.get('SDL_GAMECONTROLLERCONFIG_FILE', '(未设)'),
             '存在' if os.path.exists(
                 os.environ.get('SDL_GAMECONTROLLERCONFIG_FILE', '/nonexistent')) else '不存在'))

    import ctypes
    import struct
    if b.sdl.SDL_Init(sdl.SDL_INIT_VIDEO) == 0:
        print('SDL_Init(VIDEO): OK, 当前驱动 = %s'
              % b.sdl.SDL_GetCurrentVideoDriver().decode())
        buf = ctypes.create_string_buffer(24)
        if b.sdl.SDL_GetDesktopDisplayMode(0, buf) == 0:
            w = struct.unpack_from('<i', buf.raw, 4)[0]
            h = struct.unpack_from('<i', buf.raw, 8)[0]
            print('桌面分辨率    : %dx%d' % (w, h))
        b.sdl.SDL_Quit()
    else:
        print('SDL_Init(VIDEO): 失败 -> %s' % b.err())
        print('   （正常情况下从 EmulationStation 的 Options 里启动才拿得到显示）')

    inp = sdl.Input(b)
    inp.open_controllers()
    names = inp.controller_names()
    print('手柄数量      : %d  %s' % (len(names), names or ''))
    print('已打开控制器  : %d' % len(inp.controllers))

    from romstore import config
    # font_path() 会按平台档案给的候选顺序挑一个真实存在的
    # （ROCKNIX 上系统里一个 CJK 字体都没有，靠安装器拷进 store 的那份）
    fp = config.font_path()
    print('中文字体      : %s  %s' % (fp, '存在' if os.path.exists(fp) else '缺失'))
    print('平台          : %s  程序目录=%s  ROM 根=%s  后端=%s'
          % (config.PLATFORM, config.STORE_DIR, config.ES_ROMS,
             config.MOUNT_BACKEND))
    # 右栏内嵌预览用哪条解码路 —— 两个平台的答案不一样，值得单独报一行：
    # ArkOS 是 ffmpeg 管道，AURKNIX/ROCKNIX 上没有 ffmpeg 命令行，改走
    # mpv 的 --vo=image（带 rkvdec 硬解）。见 romstore/vplayer.py
    print('内嵌预览      : %s' % vplayer.describe())
    print('全屏预览      : mpv %s'
          % (vplayer.tool('mpv') or '（缺失，按 X 无法播放）'))
    print('NAS 路径      : %s  %s'
          % (config.NAS_ROOT, '可访问' if os.path.isdir(config.NAS_ROOT) else '不可访问'))
    print('索引数据库    : %s  %s'
          % (config.DB_PATH,
             '存在' if os.path.exists(config.DB_PATH) else '不存在'))

    # 封面预览依赖 SDL_image 解码 jpg/png + libSDL2 的 SDL_BlitScaled 缩放。
    # 实测 r36s 的 libSDL2 里**没有 SDL_BlitScaled** —— 此时自动走纯 Python
    # 最近邻缩放（慢一点但可用），不会拖崩程序。
    try:
        b.img.IMG_Load
        print('SDL_image     : OK（封面可解码）')
    except (AttributeError, OSError) as e:
        print('SDL_image     : 不可用 -> %s（详情页不显示封面缩略图）' % e)
    if getattr(b, 'has_blitscaled', False):
        print('封面缩放      : SDL_BlitScaled（原生，快）')
    else:
        print('封面缩放      : 纯 Python 最近邻（本机 libSDL2 缺 SDL_BlitScaled）')
    return 0


def display_test(seconds=2.0):
    """真机上屏自检：走完整的 KMSDRM 窗口/渲染器/纹理链路，然后立刻退出

    无头模式不经过 Display，这个测试专门补上那段。
    """
    import time
    t0 = time.time()
    app = ui.App(headless=False)
    print('Display 建立成功: 窗口 %dx%d, 渲染器 = %s'
          % (app.display.w, app.display.h,
             '硬件加速' if app.display.ren else '?'))
    app.shot('/tmp/display_test.png')
    app.display.present(app.canvas)
    print('present() 成功')
    time.sleep(seconds)
    app.close()
    print('已退出，耗时 %.2f 秒' % (time.time() - t0))
    return 0


def main(argv):
    dump = '/tmp/romstore-ui'
    script = None
    resume = None
    size = (ui.W, ui.H)
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == '--probe':
            return probe()
        if a == '--sweep':
            return sweep()
        if a == '--display-test':
            return display_test()
        if a == '--resume' and i + 1 < len(argv):
            resume = argv[i + 1]
            i += 2
            continue
        if a == '--script' and i + 1 < len(argv):
            script = argv[i + 1]
            i += 2
            continue
        if a == '--dump' and i + 1 < len(argv):
            dump = argv[i + 1]
            i += 2
            continue
        if a == '--size' and i + 1 < len(argv):
            try:
                w, h = argv[i + 1].lower().split('x')
                size = (int(w), int(h))
            except Exception:
                pass
            i += 2
            continue
        if a in ('-h', '--help'):
            print(__doc__)
            return 0
        i += 1

    if script is not None:
        acts = [x.strip() for x in script.split(',') if x.strip()]
        paths = ui.run_script(acts, dump, size=size)
        for p in paths:
            print(p)
        return 0

    app = ui.App(size=size)
    _install_signal_handlers(app)
    try:
        if resume is not None:
            # 播完预览视频回来：直接落在原来那一款游戏的详情页上
            ui.goto_game(app, resume)
        app.run()
    finally:
        # 这里必须能跑到 —— 收 mpv 就靠它。所以上面装了信号处理器，
        # 让 SIGTERM 走「正常收尾」而不是「立即终止」（见 _install_signal_handlers）
        app.close()
    # App 可能带着"请求播放视频"的退出码退出（见 ui.EXIT_PLAY），
    # 透传给外层 RomStore.sh，由它接手播放。
    return app.exit_code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
