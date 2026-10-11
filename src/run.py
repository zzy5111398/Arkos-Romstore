#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROM 商店客户端入口

真机运行（从 ES 的 Options -> Tools 启动）：
    python3 /home/ark/.romstore/run.py

无头脚本模式（开发/远程验证，逐步导出界面截图）：
    python3 run.py --script "down,down,ok,down,ok" --dump /tmp/ui
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from romstore import ui   # noqa: E402
from romstore import sdl  # noqa: E402


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
    fp = config.FONT_CJK if os.path.exists(config.FONT_CJK) else config.FONT_CJK_ALT
    print('中文字体      : %s  %s' % (fp, '存在' if os.path.exists(fp) else '缺失'))
    print('NAS 路径      : %s  %s'
          % (config.NAS_ROOT, '可访问' if os.path.isdir(config.NAS_ROOT) else '不可访问'))
    print('索引数据库    : %s  %s'
          % (config.DB_PATH,
             '存在' if os.path.exists(config.DB_PATH) else '不存在'))
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
    size = (ui.W, ui.H)
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == '--probe':
            return probe()
        if a == '--display-test':
            return display_test()
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
    try:
        app.run()
    finally:
        app.close()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
