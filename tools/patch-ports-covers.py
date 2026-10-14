#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""掌机 ports/gamelist.xml 封面补齐 v2（文本级，不重序列化）。

v2 改动：
  - 用 .sh 的 basename（去扩展名）作为主键匹配，兼容 Grand Theft Auto 3.sh → GTA3
  - 别名表把英文 sh 名映射到中文封面
  - 已有 <image> 指向的文件若不存在，也一并改掉（修正 wiliwili.jpg / gta3.jpg 这类死链）
"""
import os, re, shutil, sys

PORTS = "/storage/roms/ports"
GL = os.path.join(PORTS, "gamelist.xml")
IMGDIR = os.path.join(PORTS, "images")
SKIP = {"RomStore.sh"}

# .sh 文件名 -> 封面文件名（缺省同名）
ALIAS = {
    "Grand Theft Auto 3.sh": "GTA3.png",
    "[英]黄金矿工.sh": "黄金矿工.png",
    "wiliwili.sh": "哔哩哔哩.png",
    "Counter-Strike 1.6.sh": "反恐精英1.6.png",     # 双胞胎条目共用同一封面
    "反恐精英1.6.sh": "反恐精英1.6.png",
}

raw = open(GL, "rb").read()
bom = raw.startswith(b"\xef\xbb\xbf")
text = raw.decode("utf-8-sig")
imgs = set(os.listdir(IMGDIR))
shset = set(f for f in os.listdir(PORTS) if f.endswith(".sh"))

def resolve(shbase):
    """返回该 .sh 应使用的封面相对路径，无则 None。"""
    stem = ALIAS.get(shbase, os.path.splitext(shbase)[0] + ".png")
    return "./images/" + stem if stem in imgs else None

game_re = re.compile(r"\t<game>.*?\t</game>\n?", re.S)
stat = {"added": 0, "fixed": 0, "dropped": 0, "noimg": []}

def patch(m):
    blk = m.group(0)
    pm = re.search(r"<path>(.*?)</path>", blk, re.S)
    if not pm:
        return blk
    p = pm.group(1).strip()
    if not p.endswith(".sh"):
        return blk
    base = os.path.basename(p)
    if base not in shset or base in SKIP:
        return blk
    want = resolve(base)
    if not want:
        stat["noimg"].append(base)
        return blk
    im = re.search(r"<image>(.*?)</image>", blk, re.S)
    if im:
        cur = im.group(1).strip().lstrip("./").split("/")[-1]
        if im.group(1).strip() == want or cur in imgs:
            return blk
        stat["fixed"] += 1
        return blk[:im.start()] + "<image>%s</image>" % want + blk[im.end():]
    nm = re.search(r"(<name>.*?</name>\n)", blk, re.S)
    if not nm:
        return blk
    stat["added"] += 1
    return blk[:nm.end()] + "\t\t<image>%s</image>\n" % want + blk[nm.end():]

# 1) 删除悬空条目
drop = []
for m in game_re.finditer(text):
    pm = re.search(r"<path>(.*?)</path>", m.group(0), re.S)
    if pm and pm.group(1).strip().endswith(".sh"):
        if os.path.basename(pm.group(1).strip()) not in shset:
            drop.append((m.start(), m.end()))
newtext = text
for s, e in reversed(drop):
    newtext = newtext[:s] + newtext[e:]
stat["dropped"] = len(drop)

# 2) 补/修 image
newtext = game_re.sub(patch, newtext)

if newtext == text:
    print("NO_CHANGE")
    sys.exit(0)
bak = GL + ".covers.bak"
if not os.path.exists(bak):
    shutil.copy2(GL, bak)
out = newtext.encode("utf-8")
if bom:
    out = b"\xef\xbb\xbf" + out
open(GL, "wb").write(out)
print("OK dropped=%d added=%d fixed=%d noimg=%s bytes %d->%d" % (
    stat["dropped"], stat["added"], stat["fixed"], stat["noimg"], len(raw), len(out)))
