# -*- coding: utf-8 -*-
"""SDL2 / SDL2_ttf / SDL2_image 的 ctypes 绑定（Python 3.7 无 pip 环境）

为什么用 ctypes：
  掌机没有 SDL2 头文件（不能编 C），但有 libSDL2 / libSDL2_ttf / libSDL2_image，
  Python 的 ctypes 不需要头文件即可调用。

为什么不用 dialog：
  实测控制台字体最大只有 14.6 KB（无 CJK 字形），且 fbcon 不支持双宽字形，
  本库标题全为中文，dialog/curses 根本显示不出来。SDL2_ttf + NotoSansCJK 是唯一可行路径。

已踩过的坑：
  - SDL_BlitSurface 是宏，真实导出符号是 SDL_UpperBlit
  - TTF_GetError 也是宏，映射到 SDL_GetError
  - 视频驱动只有 KMSDRM 与 offscreen（没有 dummy/fbdev）
  - 事件用 struct.unpack_from 按偏移解析，避免 union 定义出错
"""
import ctypes
import os
import struct

# ------------------------------------------------------------------ 常量
SDL_INIT_VIDEO = 0x00000020
SDL_INIT_JOYSTICK = 0x00000200
SDL_INIT_GAMECONTROLLER = 0x00002000
SDL_INIT_EVENTS = 0x00004000

SDL_WINDOWPOS_CENTERED = 0x2FFF0000
SDL_WINDOW_FULLSCREEN = 0x00000001
SDL_WINDOW_FULLSCREEN_DESKTOP = 0x00001001
SDL_WINDOW_SHOWN = 0x00000004
SDL_WINDOW_BORDERLESS = 0x00000010

SDL_RENDERER_SOFTWARE = 0x00000001
SDL_RENDERER_ACCELERATED = 0x00000002
SDL_RENDERER_PRESENTVSYNC = 0x00000004

SDL_PIXELFORMAT_ARGB8888 = 0x16362004
SDL_TEXTUREACCESS_STREAMING = 1

EV_QUIT = 0x100
EV_KEYDOWN = 0x300
EV_KEYUP = 0x301
EV_CONTROLLERAXISMOTION = 0x650
EV_CONTROLLERBUTTONDOWN = 0x651
EV_CONTROLLERBUTTONUP = 0x652
EV_CONTROLLERDEVICEADDED = 0x653
EV_CONTROLLERDEVICEREMOVED = 0x654

# SDL_Keycode
K_UP, K_DOWN, K_LEFT, K_RIGHT = 0x40000052, 0x40000051, 0x40000050, 0x4000004F
K_RETURN, K_KP_ENTER, K_ESCAPE, K_BACKSPACE = 13, 0x40000058, 27, 8
K_PAGEUP, K_PAGEDOWN = 0x4000004B, 0x4000004E
K_SPACE = 32
K_q, K_Q = 113, 81
K_r, K_R = 114, 82
K_i, K_I = 105, 73
K_d, K_D = 100, 68
K_m, K_M = 109, 77

# SDL_GameControllerButton
CB_A, CB_B, CB_X, CB_Y = 0, 1, 2, 3
CB_BACK, CB_GUIDE, CB_START = 4, 5, 6
CB_LEFTSTICK, CB_RIGHTSTICK = 7, 8
CB_LSHOULDER, CB_RSHOULDER = 9, 10
CB_DPAD_UP, CB_DPAD_DOWN, CB_DPAD_LEFT, CB_DPAD_RIGHT = 11, 12, 13, 14

AX_LEFTX, AX_LEFTY = 0, 1
AX_RIGHTX, AX_RIGHTY = 2, 3
AX_TRIGGERLEFT, AX_TRIGGERRIGHT = 4, 5

EVENT_BUF_SIZE = 64


class Rect(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int), ('y', ctypes.c_int),
                ('w', ctypes.c_int), ('h', ctypes.c_int)]


class Color(ctypes.Structure):
    _fields_ = [('r', ctypes.c_uint8), ('g', ctypes.c_uint8),
                ('b', ctypes.c_uint8), ('a', ctypes.c_uint8)]


class SDL_PixelFormat(ctypes.Structure):
    """SDL2 的 SDL_PixelFormat。

    **字段顺序必须与 SDL2 头文件完全一致** —— 这是 ABI 布局，错一个字段
    后面全部读到垃圾。纯 Python 缩放 fallback 只用末尾的 BytesPerPixel
    （用它判断"每像素几个字节"，只处理 3/4 字节的图）。
    指针统一按 c_void_p 收（我们不解引用 palette 这些）。
    """
    _fields_ = [('format', ctypes.c_uint32), ('palette', ctypes.c_void_p),
                ('BitsPerPixel', ctypes.c_uint8), ('BytesPerPixel', ctypes.c_uint8),
                ('padding', ctypes.c_uint8 * 2),
                ('Rmask', ctypes.c_uint32), ('Gmask', ctypes.c_uint32),
                ('Bmask', ctypes.c_uint32), ('Amask', ctypes.c_uint32),
                ('Rloss', ctypes.c_uint8), ('Gloss', ctypes.c_uint8),
                ('Bloss', ctypes.c_uint8), ('Aloss', ctypes.c_uint8),
                ('Rshift', ctypes.c_uint8), ('Gshift', ctypes.c_uint8),
                ('Bshift', ctypes.c_uint8), ('Ashift', ctypes.c_uint8),
                ('refcount', ctypes.c_int), ('next', ctypes.c_void_p)]


class SDL_Surface(ctypes.Structure):
    """SDL2 的 SDL_Surface。

    `format` 直接声明为 SDL_PixelFormat* —— **必须在 SDL_PixelFormat 之后定义**
    （早先写成 c_void_p 再事后改 _fields_ 是错的：ctypes 在第一次访问字段后
    就锁定了布局，改 _fields_ 不生效，读出来是个 int）。
    """
    _fields_ = [('flags', ctypes.c_uint32),
                ('format', ctypes.POINTER(SDL_PixelFormat)),
                ('w', ctypes.c_int), ('h', ctypes.c_int),
                ('pitch', ctypes.c_int), ('pixels', ctypes.c_void_p),
                ('userdata', ctypes.c_void_p), ('locked', ctypes.c_int),
                ('lock_data', ctypes.c_void_p), ('clip_rect', Rect),
                ('map', ctypes.c_void_p), ('refcount', ctypes.c_int)]


class Bind(object):
    """加载并声明所有用到的函数签名"""

    def __init__(self):
        self.sdl = ctypes.CDLL('libSDL2-2.0.so.0')
        self.ttf = ctypes.CDLL('libSDL2_ttf-2.0.so.0')
        self.img = ctypes.CDLL('libSDL2_image-2.0.so.0')
        S, T, I = self.sdl, self.ttf, self.img

        S.SDL_Init.argtypes = [ctypes.c_uint32]
        S.SDL_Init.restype = ctypes.c_int
        S.SDL_InitSubSystem.argtypes = [ctypes.c_uint32]
        S.SDL_InitSubSystem.restype = ctypes.c_int
        S.SDL_Quit.restype = None
        S.SDL_GetError.restype = ctypes.c_char_p
        S.SDL_GetVersion.argtypes = [ctypes.c_void_p]

        S.SDL_GetNumVideoDrivers.restype = ctypes.c_int
        S.SDL_GetVideoDriver.argtypes = [ctypes.c_int]
        S.SDL_GetVideoDriver.restype = ctypes.c_char_p
        S.SDL_GetCurrentVideoDriver.restype = ctypes.c_char_p

        S.SDL_GetNumVideoDisplays.restype = ctypes.c_int
        S.SDL_GetDesktopDisplayMode.argtypes = [ctypes.c_int, ctypes.c_void_p]
        S.SDL_GetDesktopDisplayMode.restype = ctypes.c_int

        S.SDL_CreateWindow.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
        S.SDL_CreateWindow.restype = ctypes.c_void_p
        S.SDL_DestroyWindow.argtypes = [ctypes.c_void_p]

        S.SDL_CreateRenderer.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
        S.SDL_CreateRenderer.restype = ctypes.c_void_p
        S.SDL_DestroyRenderer.argtypes = [ctypes.c_void_p]
        S.SDL_RenderClear.argtypes = [ctypes.c_void_p]
        S.SDL_RenderCopy.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_void_p]
        S.SDL_RenderPresent.argtypes = [ctypes.c_void_p]
        S.SDL_CreateTexture.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int]
        S.SDL_CreateTexture.restype = ctypes.c_void_p
        S.SDL_DestroyTexture.argtypes = [ctypes.c_void_p]
        S.SDL_UpdateTexture.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_int]
        S.SDL_ShowCursor.argtypes = [ctypes.c_int]

        S.SDL_CreateRGBSurface.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32,
                                           ctypes.c_uint32, ctypes.c_uint32]
        S.SDL_CreateRGBSurface.restype = ctypes.POINTER(SDL_Surface)
        S.SDL_FreeSurface.argtypes = [ctypes.POINTER(SDL_Surface)]
        S.SDL_FillRect.argtypes = [ctypes.POINTER(SDL_Surface), ctypes.c_void_p,
                                   ctypes.c_uint32]
        S.SDL_FillRect.restype = ctypes.c_int
        S.SDL_UpperBlit.argtypes = [ctypes.POINTER(SDL_Surface), ctypes.c_void_p,
                                    ctypes.POINTER(SDL_Surface), ctypes.c_void_p]
        S.SDL_UpperBlit.restype = ctypes.c_int
        S.SDL_SetSurfaceBlendMode.argtypes = [ctypes.POINTER(SDL_Surface), ctypes.c_int]
        S.SDL_LockSurface.argtypes = [ctypes.POINTER(SDL_Surface)]
        S.SDL_UnlockSurface.argtypes = [ctypes.POINTER(SDL_Surface)]

        S.SDL_PollEvent.argtypes = [ctypes.c_void_p]
        S.SDL_PollEvent.restype = ctypes.c_int
        S.SDL_NumJoysticks.restype = ctypes.c_int
        S.SDL_IsGameController.argtypes = [ctypes.c_int]
        S.SDL_IsGameController.restype = ctypes.c_int
        S.SDL_GameControllerOpen.argtypes = [ctypes.c_int]
        S.SDL_GameControllerOpen.restype = ctypes.c_void_p
        S.SDL_GameControllerNameForIndex.argtypes = [ctypes.c_int]
        S.SDL_GameControllerNameForIndex.restype = ctypes.c_char_p
        S.SDL_GameControllerClose.argtypes = [ctypes.c_void_p]
        S.SDL_GameControllerEventState.argtypes = [ctypes.c_int]
        S.SDL_GameControllerEventState.restype = ctypes.c_int

        T.TTF_Init.restype = ctypes.c_int
        T.TTF_Quit.restype = None
        T.TTF_OpenFont.argtypes = [ctypes.c_char_p, ctypes.c_int]
        T.TTF_OpenFont.restype = ctypes.c_void_p
        T.TTF_CloseFont.argtypes = [ctypes.c_void_p]
        T.TTF_RenderUTF8_Blended.argtypes = [ctypes.c_void_p, ctypes.c_char_p, Color]
        T.TTF_RenderUTF8_Blended.restype = ctypes.POINTER(SDL_Surface)
        T.TTF_FontHeight.argtypes = [ctypes.c_void_p]
        T.TTF_FontHeight.restype = ctypes.c_int
        T.TTF_SizeUTF8.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                   ctypes.POINTER(ctypes.c_int),
                                   ctypes.POINTER(ctypes.c_int)]
        T.TTF_SizeUTF8.restype = ctypes.c_int

        I.IMG_SavePNG.argtypes = [ctypes.POINTER(SDL_Surface), ctypes.c_char_p]
        I.IMG_SavePNG.restype = ctypes.c_int
        I.IMG_Load.argtypes = [ctypes.c_char_p]
        I.IMG_Load.restype = ctypes.POINTER(SDL_Surface)

        # 封面缩放用到的那几个符号**不是所有 libSDL2 都有**。
        # 实测 r36s（ArkOS4Clone）的 libSDL2-2.0.so.0 里**没有 SDL_BlitScaled**
        # —— 声明 argtypes 时 ctypes 立刻抛 AttributeError，会把整个 Bind() 拖崩，
        # 结果连主界面都起不来（不只是没封面）。所以**先探测、再声明**，
        # 缺了就置 None，由 ImageCache 走纯 Python 缩放兜底。
        self.has_blitscaled = self._probe(S, 'SDL_BlitScaled')
        self.has_convertsurf = self._probe(S, 'SDL_ConvertSurfaceFormat')

        if self.has_convertsurf:
            S.SDL_ConvertSurfaceFormat.argtypes = [ctypes.POINTER(SDL_Surface),
                                                   ctypes.c_uint32, ctypes.c_uint32]
            S.SDL_ConvertSurfaceFormat.restype = ctypes.POINTER(SDL_Surface)
        else:
            S.SDL_ConvertSurfaceFormat = None
        if self.has_blitscaled:
            S.SDL_BlitScaled.argtypes = [ctypes.POINTER(SDL_Surface), ctypes.c_void_p,
                                         ctypes.POINTER(SDL_Surface), ctypes.c_void_p]
            S.SDL_BlitScaled.restype = ctypes.c_int
        else:
            S.SDL_BlitScaled = None
        S.SDL_SetColorKey.argtypes = [ctypes.POINTER(SDL_Surface), ctypes.c_int,
                                      ctypes.c_uint32]
        S.SDL_SetColorKey.restype = ctypes.c_int

        self._version = None

    def version(self):
        if self._version is None:
            buf = ctypes.create_string_buffer(4)
            self.sdl.SDL_GetVersion(buf)
            self._version = tuple(ord(buf.raw[i:i + 1]) for i in range(3))
        return self._version

    @staticmethod
    def _probe(lib, name):
        """某个符号在这个 libSDL2 里到底有没有（没有就返回 False，不抛异常）"""
        try:
            getattr(lib, name)
            return True
        except AttributeError:
            return False

    def err(self):
        e = self.sdl.SDL_GetError()
        return e.decode('utf-8', 'replace') if e else ''

    def video_drivers(self):
        n = self.sdl.SDL_GetNumVideoDrivers()
        return [self.sdl.SDL_GetVideoDriver(i).decode() for i in range(n)]


class Canvas(object):
    """对 SDL_Surface 的薄封装（32 位 ARGB）"""

    def __init__(self, b, w, h):
        self.b = b
        self.w, self.h = w, h
        self.surf = b.sdl.SDL_CreateRGBSurface(
            0, w, h, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
        if not self.surf:
            raise RuntimeError('SDL_CreateRGBSurface 失败: %s' % b.err())
        self.pitch = self.surf.contents.pitch

    def fill(self, argb):
        self.b.sdl.SDL_FillRect(self.surf, None, ctypes.c_uint32(argb))

    def rect(self, x, y, w, h, argb):
        r = Rect(x, y, w, h)
        self.b.sdl.SDL_FillRect(self.surf, ctypes.byref(r), ctypes.c_uint32(argb))

    def blit(self, src_surf, x, y):
        self.b.sdl.SDL_UpperBlit(src_surf, None, self.surf,
                                 ctypes.byref(Rect(x, y, 0, 0)))

    def blit_scaled(self, src_surf, x, y, w, h):
        """把源 surface 缩放贴到 (x, y, w, h)"""
        self.b.sdl.SDL_BlitScaled(src_surf, None, self.surf,
                                  ctypes.byref(Rect(x, y, w, h)))

    def save_png(self, path):
        return self.b.img.IMG_SavePNG(self.surf, path.encode('utf-8'))

    def free(self):
        if self.surf:
            self.b.sdl.SDL_FreeSurface(self.surf)
            self.surf = None


class ImageCache(object):
    """封面缩略图缓存

    详情页每帧都重绘，所以按 (路径, 目标宽高) 缓存**已缩放好的** surface，
    免得每帧重算。掌机内存只有 1 GB，缓存上限设小（8 张）并 LRU 淘汰。

    缩放优先走 SDL_BlitScaled；**这个符号在 r36s 的 libSDL2 里没有**
    （见 Bind 里的探测），所以备了一条纯 Python 最近邻缩放路径。
    纯 Python 慢（400x300 约 0.1 秒），但只在**换游戏时算一次**，
    之后走缓存，不影响列表滚动与翻页。

    加载失败（文件损坏 / SDL_image 缺对应解码器）返回 None，
    调用方按「无封面」处理 —— 绝不让一张坏图把整个 UI 拖崩。
    """

    _LIMIT = 8

    def __init__(self, b):
        self.b = b
        self._cache = {}   # (path, w, h) -> surface
        self._order = []

    def thumb(self, path, w, h):
        if not path or w <= 0 or h <= 0:
            return None
        key = (path, w, h)
        hit = self._cache.get(key)
        if hit is not None:
            return hit if hit is not False else None

        surf = self._make(path, w, h)
        self._cache[key] = surf if surf else False
        self._order.append(key)
        self._trim()
        return surf

    def _make(self, path, w, h):
        src = self.b.img.IMG_Load(path.encode('utf-8'))
        if not src:
            return None
        # 原图由本函数的 finally 统一释放；转出来的 conv 由 _scale_* 释放
        try:
            sw = src.contents.w
            sh = src.contents.h
            if sw <= 0 or sh <= 0:
                return None
            # 等比缩放到 w x h 内（letterbox），避免封面被拉变形
            scale = min(w / float(sw), h / float(sh))
            tw = max(1, int(sw * scale))
            th = max(1, int(sh * scale))

            if self.b.has_blitscaled:
                return self._scale_sdl(src, tw, th)
            return self._scale_py(src, tw, th)
        except Exception:
            # 封面是锦上添花，任何异常都退化成「无封面」，
            # 绝不能因为一张读不出来的图把整个界面拖崩。
            return None
        finally:
            self.b.sdl.SDL_FreeSurface(src)

    def _to_argb(self, src):
        """把任意格式的源图转成 32 位 ARGB8888。

        **这一步是必须的**：SDL_image 解 JPEG 得到的是 24 位 BGR
        （实测 BytesPerPixel=3、Amask=0），直接按 4 字节搬到目标画布上
        会得到 alpha=0 的像素 —— 画出来完全透明，看着就是"没封面"。
        用 SDL_ConvertSurfaceFormat 转一次，格式统一、字节序也对，
        下面两条缩放路径就都不用操心像素格式了。

        本函数只负责转，**不释放 src**；返回的 conv 由调用方释放。
        没有 SDL_ConvertSurfaceFormat 时退回原图（纯 Python 路径自己补 alpha）。
        """
        if not self.b.has_convertsurf:
            return src, False
        conv = self.b.sdl.SDL_ConvertSurfaceFormat(
            src, SDL_PIXELFORMAT_ARGB8888, 0)
        if conv:
            return conv, True
        return src, False

    def _scale_sdl(self, src, tw, th):
        """SDL 原生缩放（快）。用了 libSDL2 自带的 SDL_BlitScaled

        这里**只管缩放**，不释放 src —— 释放统一由 _make 的 finally 做，
        免得两处都 free 造成 double free（SDL 会崩）。
        转换出来的临时 surface 由本函数自己释放。
        """
        S = self.b.sdl
        use, conv_owned = self._to_argb(src)
        try:
            dst = Canvas(self.b, tw, th)
            dst.fill(0x00000000)
            S.SDL_SetSurfaceBlendMode(use, 1)  # SDL_BLENDMODE_BLEND
            S.SDL_BlitScaled(use, None, dst.surf, None)
            return dst.surf
        finally:
            if conv_owned:
                S.SDL_FreeSurface(use)

    def _scale_py(self, src, tw, th):
        """纯 Python 最近邻缩放（fallback）

        为什么不用 SDL_BlitScaled：r36s 上没这个符号（见 Bind 探测）。
        为什么不用 SDL_LowerBlit 手动分块：那要自己算拉伸分块，更绕。
        直接读源 surface 的像素、写目标 surface 的像素最简单，也最可移植
        —— 只用了 pitch/pixels/format 这几个字段，一切 SDL2 都有。

        进来先经 _to_argb 统一成 32 位 ARGB，所以下面只需处理 4 字节像素。
        源格式实在认不出来（非 3/4 字节、且本机又没 SDL_ConvertSurfaceFormat）
        就放弃，返回 None 当无封面。
        """
        S = self.b.sdl
        use, conv_owned = self._to_argb(src)
        try:
            fmt = use.contents.format
            if not fmt:
                return None
            try:
                bpp = fmt.contents.BytesPerPixel
            except (AttributeError, ValueError):
                return None
            if bpp not in (3, 4):
                return None

            sw, sh = use.contents.w, use.contents.h
            if sw <= 0 or sh <= 0:
                return None

            # 读源像素：SDL_LockSurface -> 裸内存 -> 解锁。
            # 整块读进来，再按行切片（比逐行 string_at 快）。
            if S.SDL_LockSurface(use) != 0:
                return None
            try:
                pitch = use.contents.pitch
                raw = ctypes.string_at(use.contents.pixels, pitch * sh)
                rows = [raw[i * pitch:i * pitch + sw * bpp] for i in range(sh)]
            finally:
                S.SDL_UnlockSurface(use)

            # 预计算每个目标列对应的源列（最近邻）
            xs = [min(sw - 1, (x * sw) // tw) for x in range(tw)]

            dst = Canvas(self.b, tw, th)
            if S.SDL_LockSurface(dst.surf) != 0:
                dst.free()
                return None
            try:
                dpitch = dst.surf.contents.pitch
                dbase = dst.surf.contents.pixels
                if not dbase:
                    return None
                opaque = b'\xff'          # 3 字节源补的不透明 alpha
                for y in range(th):
                    sy = min(sh - 1, (y * sh) // th)
                    srow = rows[sy]
                    out = bytearray()
                    if bpp == 4:
                        for sx in xs:
                            o = sx * 4
                            out += srow[o:o + 4]
                    else:  # 3 字节：按 BGR 原样搬，补 0xFF 的不透明 alpha
                        for sx in xs:
                            o = sx * 3
                            out += srow[o:o + 3] + opaque
                    ctypes.memmove(dbase + y * dpitch, bytes(out), len(out))
            finally:
                S.SDL_UnlockSurface(dst.surf)
            return dst.surf
        finally:
            if conv_owned:
                S.SDL_FreeSurface(use)

    def _trim(self):
        while len(self._order) > self._LIMIT:
            k = self._order.pop(0)
            if k in self._cache:
                s = self._cache.pop(k)
                if s is not False and s:
                    self.b.sdl.SDL_FreeSurface(s)

    def clear(self):
        for s in self._cache.values():
            if s is not False and s:
                self.b.sdl.SDL_FreeSurface(s)
        self._cache = {}
        self._order = []


class Fonts(object):
    """字体与文本 surface 缓存"""

    def __init__(self, b, path, size):
        self.b = b
        self.path = path
        self.size = size
        if b.ttf.TTF_Init() != 0:
            raise RuntimeError('TTF_Init 失败')
        self.font = b.ttf.TTF_OpenFont(path.encode('utf-8'), size)
        if not self.font:
            raise RuntimeError('无法打开字体 %s: %s' % (path, b.err()))
        self._cache = {}
        self.line_h = b.ttf.TTF_FontHeight(self.font)

    def render(self, text, rgb):
        key = (text, rgb)
        s = self._cache.get(key)
        if s is not None:
            return s
        col = Color(rgb[0], rgb[1], rgb[2], 255)
        surf = self.b.ttf.TTF_RenderUTF8_Blended(self.font, text.encode('utf-8'), col)
        if len(self._cache) > 600:
            for k in list(self._cache.keys())[:200]:
                old = self._cache.pop(k)
                self.b.sdl.SDL_FreeSurface(old)
        self._cache[key] = surf
        return surf

    def width(self, text):
        w = ctypes.c_int()
        h = ctypes.c_int()
        if self.b.ttf.TTF_SizeUTF8(self.font, text.encode('utf-8'),
                                   ctypes.byref(w), ctypes.byref(h)) != 0:
            return 0
        return w.value

    def clear(self):
        for s in self._cache.values():
            if s:
                self.b.sdl.SDL_FreeSurface(s)
        self._cache = {}

    def close(self):
        self.clear()
        if self.font:
            self.b.ttf.TTF_CloseFont(self.font)
            self.font = None
        self.b.ttf.TTF_Quit()


class Display(object):
    """KMSDRM 上屏：把 Canvas 的内容拷到纹理再 present"""

    def __init__(self, b, w, h, title='ROM Store', fullscreen=True, vsync=True):
        self.b = b
        self.inited = False
        flags = SDL_INIT_VIDEO
        if b.sdl.SDL_Init(flags) != 0:
            raise RuntimeError('SDL_Init(VIDEO) 失败: %s' % b.err())
        self.inited = True
        b.sdl.SDL_ShowCursor(0)

        self.dw, self.dh = w, h
        try:
            buf = ctypes.create_string_buffer(24)
            if b.sdl.SDL_GetDesktopDisplayMode(0, buf) == 0:
                # SDL_DisplayMode { Uint32 format; int w; int h; ... }
                self.dw = struct.unpack_from('<i', buf.raw, 4)[0]
                self.dh = struct.unpack_from('<i', buf.raw, 8)[0]
        except Exception:
            pass

        self.w, self.h = (self.dw, self.dh) if fullscreen else (w, h)
        winflags = SDL_WINDOW_SHOWN
        if fullscreen:
            winflags |= SDL_WINDOW_FULLSCREEN_DESKTOP
        self.win = b.sdl.SDL_CreateWindow(title.encode('utf-8'),
                                          SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                          self.w, self.h, winflags)
        if not self.win:
            raise RuntimeError('SDL_CreateWindow 失败: %s' % b.err())

        rflags = SDL_RENDERER_ACCELERATED
        if vsync:
            rflags |= SDL_RENDERER_PRESENTVSYNC
        self.ren = b.sdl.SDL_CreateRenderer(self.win, -1, rflags)
        if not self.ren:
            self.ren = b.sdl.SDL_CreateRenderer(self.win, -1, SDL_RENDERER_SOFTWARE)
        if not self.ren:
            raise RuntimeError('SDL_CreateRenderer 失败: %s' % b.err())

        self.tex = b.sdl.SDL_CreateTexture(self.ren, SDL_PIXELFORMAT_ARGB8888,
                                           SDL_TEXTUREACCESS_STREAMING, w, h)
        if not self.tex:
            raise RuntimeError('SDL_CreateTexture 失败: %s' % b.err())

    def present(self, canvas):
        b = self.b
        b.sdl.SDL_UpdateTexture(self.tex, None, canvas.surf.contents.pixels,
                                canvas.pitch)
        b.sdl.SDL_RenderClear(self.ren)
        b.sdl.SDL_RenderCopy(self.ren, self.tex, None, None)
        b.sdl.SDL_RenderPresent(self.ren)

    def close(self):
        b = self.b
        if getattr(self, 'tex', None):
            b.sdl.SDL_DestroyTexture(self.tex)
        if getattr(self, 'ren', None):
            b.sdl.SDL_DestroyRenderer(self.ren)
        if getattr(self, 'win', None):
            b.sdl.SDL_DestroyWindow(self.win)
        if self.inited:
            b.sdl.SDL_Quit()
        self.inited = False


class Input(object):
    """事件 → 抽象动作。

    动作: up / down / left / right / ok / back / pgup / pgdn / quit
          home / end / refresh / install / delete / menu

    掌机没有键盘，所以每个动作都必须有手柄绑定（键盘绑定只留给桌面调试）：
      A=ok  B=back  X=install  Y=delete  L1/R1=翻页
      Start = menu（Start 菜单，内含「重扫 NAS」「退出商店」）
    """

    def __init__(self, b, repeat_delay=0.28, repeat_rate=0.075):
        self.b = b
        self.buf = ctypes.create_string_buffer(EVENT_BUF_SIZE)
        self.controllers = {}
        self.stick_held = None
        self.stick_since = 0.0
        self.repeat_delay = repeat_delay
        self.repeat_rate = repeat_rate
        self._last = 0.0
        self.b.sdl.SDL_InitSubSystem(SDL_INIT_GAMECONTROLLER)

    def open_controllers(self):
        S = self.b.sdl
        n = S.SDL_NumJoysticks()
        for i in range(n):
            if S.SDL_IsGameController(i):
                c = S.SDL_GameControllerOpen(i)
                if c and i not in self.controllers:
                    self.controllers[i] = c

    def controller_names(self):
        S = self.b.sdl
        out = []
        for i in range(S.SDL_NumJoysticks()):
            nm = S.SDL_GameControllerNameForIndex(i)
            out.append(nm.decode('utf-8', 'replace') if nm else ('#%d' % i))
        return out

    def poll(self, now=0.0):
        """返回动作列表（单帧内可能多个）"""
        S = self.b.sdl
        acts = []
        while S.SDL_PollEvent(self.buf):
            raw = self.buf.raw
            et = struct.unpack_from('<I', raw, 0)[0]
            if et == EV_QUIT:
                acts.append('quit')
            elif et == EV_KEYDOWN:
                sym = struct.unpack_from('<i', raw, 20)[0]
                a = self._key_to_action(sym)
                if a:
                    acts.append(a)
            elif et == EV_CONTROLLERDEVICEADDED:
                self.open_controllers()
            elif et == EV_CONTROLLERBUTTONDOWN:
                btn = struct.unpack_from('<B', raw, 12)[0]
                a = self._btn_to_action(btn)
                if a:
                    acts.append(a)
            elif et == EV_CONTROLLERAXISMOTION:
                ax = struct.unpack_from('<B', raw, 12)[0]
                val = struct.unpack_from('<h', raw, 16)[0]
                a = self._axis_to_action(ax, val, now)
                if a:
                    acts.append(a)
        return acts

    def _key_to_action(self, sym):
        if sym in (K_UP,):
            return 'up'
        if sym in (K_DOWN,):
            return 'down'
        if sym in (K_LEFT,):
            return 'left'
        if sym in (K_RIGHT,):
            return 'right'
        if sym in (K_RETURN, K_KP_ENTER, K_SPACE):
            return 'ok'
        if sym in (K_ESCAPE, K_BACKSPACE):
            return 'back'
        if sym == K_PAGEUP:
            return 'pgup'
        if sym == K_PAGEDOWN:
            return 'pgdn'
        if sym in (K_q, K_Q):
            return 'quit'
        if sym in (K_r, K_R):
            return 'refresh'
        if sym in (K_i, K_I):
            return 'install'
        if sym in (K_d, K_D):
            return 'delete'
        if sym in (K_m, K_M):
            return 'menu'
        return None

    def _btn_to_action(self, btn):
        return {
            CB_DPAD_UP: 'up', CB_DPAD_DOWN: 'down',
            CB_DPAD_LEFT: 'left', CB_DPAD_RIGHT: 'right',
            CB_A: 'ok',
            CB_B: 'back',
            CB_LSHOULDER: 'pgup', CB_RSHOULDER: 'pgdn',
            CB_X: 'install', CB_Y: 'delete',
            # Start = 菜单（重扫 / 退出都在里面）；Select 一并作为等价入口，
            # 因为它在 ArkOS/ES 里本就是"菜单"键。退出不直接触发，必须走菜单。
            CB_START: 'menu', CB_BACK: 'menu', CB_GUIDE: 'menu',
        }.get(btn)

    def _axis_to_action(self, ax, val, now):
        DEAD = 16000
        if ax == AX_LEFTY:
            if abs(val) < DEAD:
                self.stick_held = None
                return None
            want = 'up' if val < 0 else 'down'
        elif ax == AX_LEFTX:
            if abs(val) < DEAD:
                self.stick_held = None
                return None
            want = 'left' if val < 0 else 'right'
        else:
            return None
        if self.stick_held != want:
            self.stick_held = want
            self.stick_since = now or 0.0
            self._last = now or 0.0
            return want
        t = now or 0.0
        if t - self.stick_since >= self.repeat_delay and t - self._last >= self.repeat_rate:
            self._last = t
            return want
        return None
