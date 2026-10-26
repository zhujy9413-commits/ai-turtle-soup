"""Build the local regular-weight web font from Noto Serif SC (SIL OFL).

Usage: python3 build_archive_font.py SOURCE.ttf OUTPUT.woff2
Build dependencies: fonttools and brotli. No runtime dependency.
The character set is public GB2312 plus public UI text; never private story data.
"""
import sys
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools import subset

source, output = map(Path, sys.argv[1:3])
font = TTFont(source, recalcTimestamp=False)
if "fvar" in font:
    instantiateVariableFont(font, {"wght": 400}, inplace=True)
names = {1: "Yetang Archive Song", 2: "Regular", 3: "YetangArchiveSong-Regular-20261026",
         4: "Yetang Archive Song Regular", 6: "YetangArchiveSong-Regular",
         16: "Yetang Archive Song", 17: "Regular", 25: "YetangArchiveSong"}
for record in font["name"].names:
    if record.nameID in names:
        record.string = names[record.nameID].encode(record.getEncoding())
chars = set(range(0x20, 0x100)) | set(range(0x2000, 0x2070)) | set(range(0x2190, 0x2200))
chars |= set(range(0x3000, 0x3040)) | set(range(0xFF00, 0xFFF0))
for a in range(0xA1, 0xF8):
    for b in range(0xA1, 0xFF):
        try:
            chars.update(map(ord, bytes([a, b]).decode("gb2312")))
        except UnicodeDecodeError:
            pass
public = Path(__file__).resolve().parents[1] / "public"
for name in ["app.js", "intro.js", "index.html"]:
    chars.update(map(ord, (public / name).read_text()))
options = subset.Options()
options.name_IDs = ["*"]
options.name_languages = ["*"]
options.notdef_outline = True
options.recalc_timestamp = False
subsetter = subset.Subsetter(options=options)
subsetter.populate(unicodes=chars)
subsetter.subset(font)
font.flavor = "woff2"
output.parent.mkdir(parents=True, exist_ok=True)
font.save(output)
print(f"Built {output.name}: {output.stat().st_size} bytes, {len(font.getBestCmap())} characters")
