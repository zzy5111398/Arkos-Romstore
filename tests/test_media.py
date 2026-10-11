# -*- coding: utf-8 -*-
"""media.py + install 封面路径 的离线自测（不碰真机）"""
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from romstore import media as M

FAIL = []


def check(name, cond, extra=''):
    print(('  [OK] ' if cond else '  [FAIL] ') + name + ('  ' + str(extra) if extra else ''))
    if not cond:
        FAIL.append(name)


root = tempfile.mkdtemp(prefix='romstore-t-')
nas = os.path.join(root, 'nas')
hh = os.path.join(root, 'handheld')
os.makedirs(nas)
os.makedirs(hh)

print('== 1. rom_stem / target_image_name ==')
check('rom_stem(1943.zip)', M.rom_stem('1943.zip') == '1943', M.rom_stem('1943.zip'))
check('rom_stem(Sub/Game (U).zip)', M.rom_stem('Sub/Game (U).zip') == 'Game (U)',
      M.rom_stem('Sub/Game (U).zip'))
check('target 1943.zip+.jpg', M.target_image_name('1943.zip', '.jpg') == '1943.jpg',
      M.target_image_name('1943.zip', '.jpg'))
check('target 1943.zip+None', M.target_image_name('1943.zip', None) == '1943.png',
      M.target_image_name('1943.zip', None))

print('== 2. find_asset 精确匹配 ==')
# NAS: FC/1943.zip + FC/media/1943/boxFront.jpg
fc = os.path.join(nas, 'FC')
os.makedirs(os.path.join(fc, 'media', '1943'))
open(os.path.join(fc, '1943.zip'), 'wb').write(b'ROM' * 100)
open(os.path.join(fc, 'media', '1943', 'boxFront.jpg'), 'wb').write(b'\xff\xd8JPEG')
open(os.path.join(fc, 'media', '1943', 'logo.png'), 'wb').write(b'\x89PNG')
open(os.path.join(fc, 'media', '1943', 'video.mp4'), 'wb').write(b'\x00' * 2048)

s, rel = M.find_asset(fc, '1943.zip', 'box_front')
check('找到 boxFront.jpg', s is not None and s.endswith('boxFront.jpg'), s)
check('相对路径含 media/1943', rel is not None and 'media' in rel and '1943' in rel, rel)

print('== 3. 大小写不敏感（真实 GBA 情况）==')
gba = os.path.join(nas, 'GBA')
os.makedirs(os.path.join(gba, 'media', 'Metroid'))
open(os.path.join(gba, 'Metroid.zip'), 'wb').write(b'x')
open(os.path.join(gba, 'media', 'Metroid', 'boxfront.jpg'), 'wb').write(b'\xff\xd8')
s, _ = M.find_asset(gba, 'Metroid.zip', 'box_front')
check('小写 boxfront.jpg 命中', s is not None and s.endswith('boxfront.jpg'), s)

print('== 3b. find_video（详情页按 X 播放的预览片源）==')
v = M.find_video(fc, '1943.zip')
check('找到 media/1943/video.mp4', v is not None and v.endswith('video.mp4'), v)
check('返回绝对路径', v is not None and os.path.isabs(v), v)
# 没视频的游戏返回 None（详情页据此提示"无预览"），不能抛异常
open(os.path.join(fc, 'NoVideo.zip'), 'wb').write(b'x')
check('没有视频返回 None', M.find_video(fc, 'NoVideo.zip') is None)
# 扩展名/文件名大小写不敏感（真实 NAS 上 boxFront/video 混用大小写）
os.makedirs(os.path.join(fc, 'media', 'CapsGame'))
open(os.path.join(fc, 'CapsGame.zip'), 'wb').write(b'x')
open(os.path.join(fc, 'media', 'CapsGame', 'Video.MP4'), 'wb').write(b'\x00' * 512)
v2 = M.find_video(fc, 'CapsGame.zip')
check('Video.MP4 大小写不敏感命中', v2 is not None and v2.endswith('Video.MP4'), v2)
# 任意扩展名的视频也能兜住（不是每个包都叫 video.mp4）
os.makedirs(os.path.join(fc, 'media', 'OddGame'))
open(os.path.join(fc, 'media', 'OddGame', 'preview.webm'), 'wb').write(b'\x1aE\xdf\xa3')
check('preview.webm 命中', (M.find_video(fc, 'OddGame.zip') or '').endswith('preview.webm'))
# 健壮性
check('系统目录不存在返回 None', M.find_video('/本机不存在的路径', 'x.zip') is None)
check('空系统目录返回 None', M.find_video('', 'x.zip') is None)

print('== 4. 没有 media/ 子目录时退化到裸目录 ==')
fc2 = os.path.join(nas, 'SFC')
os.makedirs(os.path.join(fc2, 'Mario'))
open(os.path.join(fc2, 'Mario.smc'), 'wb').write(b'x')
open(os.path.join(fc2, 'Mario', 'cover.png'), 'wb').write(b'\x89PNG')
s, rel = M.find_asset(fc2, 'Mario.smc', 'box_front')
check('裸目录 cover.png 命中', s is not None and s.endswith('cover.png'), s)

print('== 5. 只有一张图时兜底（非 logo）==')
sfc3 = os.path.join(nas, 'SFC3')
os.makedirs(os.path.join(sfc3, 'media', 'Zelda'))
open(os.path.join(sfc3, 'Zelda.smc'), 'wb').write(b'x')
open(os.path.join(sfc3, 'media', 'Zelda', 'whatever.png'), 'wb').write(b'\x89PNG')
s, _ = M.find_asset(sfc3, 'Zelda.smc', 'box_front')
check('唯一图片兜底命中', s is not None and s.endswith('whatever.png'), s)
s2, _ = M.find_asset(sfc3, 'Zelda.smc', 'logo')
check('logo 请求不兜底', s2 is None, s2)

print('== 6. 完全没图 ==')
nopic = os.path.join(nas, 'NES')
os.makedirs(os.path.join(nopic, 'media', 'Duck'))
open(os.path.join(nopic, 'Duck.nes'), 'wb').write(b'x')
open(os.path.join(nopic, 'media', 'Duck', 'video.mp4'), 'wb').write(b'\x00')
s, _ = M.find_asset(nopic, 'Duck.nes', 'box_front')
check('只有视频 -> 无封面', s is None, s)

print('== 7. pick_media_dir 沿用既有写法（目录名 + 布局）==')
os.makedirs(os.path.join(hh, 'img'))
open(os.path.join(hh, 'img', 'a.png'), 'wb').write(b'x')
check('gamelist 无 -> 默认 (img, flat)',
      M.pick_media_dir(hh) == ('img', 'flat'), M.pick_media_dir(hh))
# gamelist 里的 <image> 是散文件式 -> flat
gltext = '<?xml version="1.0"?>\r\n<gameList>\r\n  <game>\r\n    <image>./media/1943.png</image>\r\n  </game>\r\n</gameList>\r\n'
check('gamelist ./media/1943.png -> (media, flat)',
      M.pick_media_dir(hh, gltext) == ('media', 'flat'), M.pick_media_dir(hh, gltext))
# gamelist 里的 <image> 是子目录式 -> subdir（真实 GBA 情况）
glsub = ('<gameList><game><image>./media/黄金太阳/boxfront.png</image>'
         '</game></gameList>')
check('gamelist ./media/X/boxfront.png -> (media, subdir)',
      M.pick_media_dir(hh, glsub) == ('media', 'subdir'), M.pick_media_dir(hh, glsub))

# 磁盘探测：只有散文件
hh2 = os.path.join(root, 'hh2')
os.makedirs(os.path.join(hh2, 'media'))
open(os.path.join(hh2, 'media', 'x.png'), 'wb').write(b'x')
check('磁盘 media/ 全是散文件 -> (media, flat)',
      M.pick_media_dir(hh2) == ('media', 'flat'), M.pick_media_dir(hh2))

# 磁盘探测：每游戏一子目录（真实 GBA 形态：462 子目录 / 0 散文件）
hh2b = os.path.join(root, 'hh2b')
os.makedirs(os.path.join(hh2b, 'media', 'GameA'))
open(os.path.join(hh2b, 'media', 'GameA', 'boxfront.png'), 'wb').write(b'x')
os.makedirs(os.path.join(hh2b, 'media', 'GameB'))
open(os.path.join(hh2b, 'media', 'GameB', 'boxfront.jpg'), 'wb').write(b'x')
check('磁盘 media/ 全是子目录 -> (media, subdir)',
      M.pick_media_dir(hh2b) == ('media', 'subdir'), M.pick_media_dir(hh2b))

hh3 = os.path.join(root, 'hh3')
os.makedirs(hh3)
check('空的 -> 默认 (img, flat)',
      M.pick_media_dir(hh3) == ('img', 'flat'), M.pick_media_dir(hh3))

print('== 7b. target_image_rel 两套布局 ==')
rel_flat, parts_flat = M.target_image_rel('img', 'flat', '1943.zip', '.jpg')
check('flat -> img/1943.jpg', rel_flat == 'img/1943.jpg', rel_flat)
check('flat parts 两级', parts_flat == ['img', '1943.jpg'], parts_flat)
rel_sub, parts_sub = M.target_image_rel('media', 'subdir', '黄金太阳.zip', '.png')
check('subdir -> media/黄金太阳/boxfront.png',
      rel_sub == 'media/黄金太阳/boxfront.png', rel_sub)
check('subdir parts 三级', parts_sub == ['media', '黄金太阳', 'boxfront.png'], parts_sub)

print('== 8. copy_atomic ==')
src = os.path.join(fc, 'media', '1943', 'boxFront.jpg')
dst = os.path.join(hh, 'img', '1943.jpg')
n = M.copy_atomic(src, dst)
check('字节数正确', n == os.path.getsize(src), n)
check('目标存在且无 .part 残留',
      os.path.exists(dst) and not os.path.exists(dst + '.part'))

print('== 9. install.upsert_gamelist 写 <image> ==')
from romstore import install as I

gl = os.path.join(hh, 'gamelist.xml')
glhead = ('<?xml version="1.0" encoding="utf-8" standalone="yes"?>\r\n'
          '<gameList>\r\n'
          '  <game>\r\n    <path>./old.nes</path>\r\n    <name>Old</name>\r\n  </game>\r\n'
          '</gameList>\r\n')
with open(gl, 'wb') as f:
    f.write(b'\xef\xbb\xbf' + glhead.encode('utf-8'))
added, updated, path = I.upsert_gamelist(hh, [
    {'rom_name': '1943.zip', 'title': '1943', 'desc': '射击',
     'image': './img/1943.jpg', 'developer': 'Capcom'}])
raw = open(gl, 'rb').read()
txt = raw.decode('utf-8-sig')
check('新增 1 条', added == 1 and updated == 0, (added, updated))
check('BOM 保留', raw.startswith(b'\xef\xbb\xbf'))
check('<image> 写入', './img/1943.jpg' in txt)
check('原有 old.nes 未动', './old.nes' in txt and '<name>Old</name>' in txt)
check('CRLF 风格保持', raw.count(b'\r\n') >= 5, raw.count(b'\r\n'))
check('无裸 LF 混入', raw.replace(b'\r\n', b'').count(b'\n') == 0,
      raw.replace(b'\r\n', b'').count(b'\n'))

print('== 10. upsert 更新既有条目（不重复）==')
added2, updated2, _ = I.upsert_gamelist(hh, [
    {'rom_name': '1943.zip', 'title': '1943 改', 'image': './media/1943.png'}])
txt = open(gl, 'rb').read().decode('utf-8-sig')
check('更新而非新增', added2 == 0 and updated2 == 1, (added2, updated2))
check('新图路径生效', './media/1943.png' in txt)
check('只有一个 1943 块', txt.count('./1943.zip') == 1)
check('总 game 块数 == 2', txt.count('<game>') == 2, txt.count('<game>'))

print('== 11. _images_with_stem（卸载时别误删同名 ROM）==')
d = os.path.join(hh, 'img')
open(os.path.join(d, '1943.zip'), 'wb').write(b'x')   # 同名 ROM，不该被当封面
open(os.path.join(d, '1943.png'), 'wb').write(b'x')
open(os.path.join(d, '1943X.png'), 'wb').write(b'x')
hits = sorted(I._images_with_stem(d, '1943'))
# 1943.jpg 是第 8 步复制的，同属该 stem —— 应一起命中；1943.zip / 1943X.png 不能命中
check('命中全部同 stem 图片', hits == ['1943.jpg', '1943.png'], hits)
check('不误伤同名 ROM / 长名', '1943.zip' not in hits and '1943X.png' not in hits)

print('== 12. 子目录式布局的卸载清理（真实 GBA 形态）==')
# 造一个 GBA 风格的家：media/<游戏>/boxfront.png + logo.png + video.mp4
gbase = os.path.join(hh, 'media')
gd = os.path.join(gbase, '黄金太阳')
os.makedirs(gd)
open(os.path.join(gd, 'boxfront.png'), 'wb').write(b'\x89PNG')
open(os.path.join(gd, 'logo.png'), 'wb').write(b'\x89PNG')
open(os.path.join(gd, 'video.mp4'), 'wb').write(b'\x00' * 100)
# 邻居不能被误伤
gd2 = os.path.join(gbase, '黄金太阳2')
os.makedirs(gd2)
open(os.path.join(gd2, 'boxfront.png'), 'wb').write(b'\x89PNG')

check('_find_dir_ci 命中子目录', I._find_dir_ci(gbase, '黄金太阳') == gd,
      I._find_dir_ci(gbase, '黄金太阳'))
check('_find_dir_ci 不匹配前缀相似名（黄金太阳2）',
      I._find_dir_ci(gbase, '黄金太阳') != gd2)
imgs = sorted(I._images_in_dir(gd))
check('_images_in_dir 只列图片', imgs == ['boxfront.png', 'logo.png'], imgs)

# 模拟 remove 的子目录清理逻辑：只删图片，目录里有 video.mp4 则保留目录
leftover = [n for n in os.listdir(gd) if n not in imgs]
check('有 video.mp4 时目录不该被删', leftover == ['video.mp4'], leftover)

print('== 13. batchmedia.audit 的三层体检（封面看不到的排查神器）==')
from romstore import batchmedia as B
# 造一个 ES 家：gamelist 引 3 张图，其中 1 张缺失、1 张是坏图（exFAT 截断样）
aud = os.path.join(root, 'audit_home')
os.makedirs(os.path.join(aud, 'media', '好游戏'))
open(os.path.join(aud, 'media', '好游戏', 'boxfront.png'), 'wb').write(
    b'\x89PNG\r\n\x1a\n' + b'\x00' * 40)          # 好图
os.makedirs(os.path.join(aud, 'media', '坏游戏'))
open(os.path.join(aud, 'media', '坏游戏', 'boxfront.png'), 'wb').write(
    b'\x00' * 64)                                  # 坏图：全零，非任何图片头
gl = ('<?xml version="1.0"?>\n<gameList>\n'
      '  <game><path>./好游戏.zip</path>'
      '<image>./media/好游戏/boxfront.png</image></game>\n'
      '  <game><path>./坏游戏.zip</path>'
      '<image>./media/坏游戏/boxfront.png</image></game>\n'
      '  <game><path>./没了.zip</path>'
      '<image>./media/没了/boxfront.png</image></game>\n'
      '</gameList>\n')
open(os.path.join(aud, 'gamelist.xml'), 'w').write(gl)

games = list(B._iter_games(gl))
check('_iter_games 切出 3 个 game', len(games) == 3, len(games))
check('_iter_games 抽出 image', games[0]['image'] == './media/好游戏/boxfront.png',
      games[0]['image'])

check('_looks_like_image 认 PNG', B._looks_like_image(
    os.path.join(aud, 'media', '好游戏', 'boxfront.png')))
check('_looks_like_image 拒全零坏图', not B._looks_like_image(
    os.path.join(aud, 'media', '坏游戏', 'boxfront.png')))

imgs = B._collect_images(aud, gl, 0)
check('_collect_images 只收「存在」的（缺失的不进抽查）', len(imgs) == 2, len(imgs))
bad = sum(1 for p in imgs if not B._looks_like_image(p))
check('抽查能数出 1 张坏图', bad == 1, bad)

# 直接用 audit 的层1逻辑（跳过 mapp/idx，手工核对引用层）
n_ok = n_miss = 0
for g in B._iter_games(gl):
    ip = g['image']
    rel = ip[2:] if ip.startswith('./') else ip.lstrip('/')
    if os.path.isfile(os.path.join(aud, rel)):
        n_ok += 1
    else:
        n_miss += 1
check('引用层：存在 2 / 缺失 1', (n_ok, n_miss) == (2, 1), (n_ok, n_miss))

print('== 14. mapp.resolve 宽容解析（ES名 与 NAS目录名 都认）==')
from romstore import mapp as MP
# 用户看到的是 ES 名（gba），内部要的是 NAS 目录名（GBA）
r = MP.resolve('gba')
check('resolve(gba) 归一到 NAS 目录 GBA', r and r[0] == 'GBA', r and r[0])
check('resolve(GBA) 大写也认', MP.resolve('GBA') and MP.resolve('GBA')[0] == 'GBA')
check('resolve(Gba) 混合大小写也认',
      MP.resolve('Gba') and MP.resolve('Gba')[0] == 'GBA')
r2 = MP.resolve('nes')
check('resolve(nes) -> NAS 目录 FC（两者不同名）', r2 and r2[0] == 'FC', r2 and r2[0])
r3 = MP.resolve('ppsspp')
check('resolve(ppsspp) -> NAS 目录 PSP', r3 and r3[0] == 'PSP', r3 and r3[0])
check('resolve 未知名字返回 None', MP.resolve('这个系统不存在') is None)
# 返回顺序必须是 (nas_dir, es_name, es_path, conf, note)
r4 = MP.resolve('gba')
check('resolve 返回顺序 (nas,es,path,conf,note)',
      r4 == ('GBA', 'gba', '/roms/gba', 'ok', 'GBA'), r4)

print('== 15. _existing_cover 大小写不敏感（修重复导入 bug）==')
# 场景：NAS 源叫 boxFront.png，我们拼的目标名是小写 boxfront.png，
# 而掌机上已有的是大写 F 的那个 —— 大小写敏感的 exFAT 上必须能找到它
cv = os.path.join(root, 'case_home', 'media', '某游戏')
os.makedirs(cv)
open(os.path.join(cv, 'boxFront.png'), 'wb').write(b'\x89PNG\x00\x01\x02')
# 小写目标名，应能命中大写 F 的真实文件
hit = B._existing_cover(os.path.join(cv, 'boxfront.png'))
check('_existing_cover 小写目标命中大写真实名',
      hit == os.path.join(cv, 'boxFront.png'), hit)
# jpg 同理
open(os.path.join(cv, 'boxFront.jpg'), 'wb').write(b'\xff\xd8\xff\x00')
hit2 = B._existing_cover(os.path.join(cv, 'boxfront.jpg'))
check('_existing_cover jpg 也命中', hit2 == os.path.join(cv, 'boxFront.jpg'),
      hit2)
# 目录里没有就是 None
check('_existing_cover 没有则 None',
      B._existing_cover(os.path.join(cv, 'nothing.png')) is None)
# 大小写完全一致的也应命中（不回归）
open(os.path.join(cv, 'boxfront.webp'), 'wb').write(b'RIFF')
check('_existing_cover 同名精确命中',
      B._existing_cover(os.path.join(cv, 'boxfront.webp'))
      == os.path.join(cv, 'boxfront.webp'))
# 目录不存在不能抛异常
check('_existing_cover 目录不存在返回 None 不抛',
      B._existing_cover('/本机不存在的路径/x/boxfront.png') is None)

print('== 16. _es_proc_start 健壮性（进程名截断 / 非 UTF-8）==')
# 在 Windows 上 /proc 不存在，应安静返回 0，而不是抛异常
t = B._es_proc_start()
check('_es_proc_start 不抛异常且返回数字', isinstance(t, (int, float)), t)
check('_es_proc_start 无 ES 进程时为 0（本机非掌机）', t == 0, t)

print()
if FAIL:
    print('!! %d 项失败: %s' % (len(FAIL), FAIL))
    shutil.rmtree(root, ignore_errors=True)
    sys.exit(1)
print('全部通过')
shutil.rmtree(root, ignore_errors=True)
