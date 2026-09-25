import json, sys
from pathlib import Path
from PIL import Image

src, out, slug = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
checks = json.loads((src / f"{slug}-checks.json").read_text())
summary = {}
for key, info in checks.items():
    w, theme = key.split("-")
    dpr, s, total = info["dpr"], info["s"], info["total"]
    width_px = round(int(w) * s * dpr)
    canvas = Image.new("RGB", (width_px, round(total * dpr)), "white")
    for c in info["chunks"]:
        img = Image.open(src / c["name"]).convert("RGB")
        # rows of this chunk that start at the wanted offset (the last scroll is clamped)
        skip = round((c["want"] - c["y"]) * dpr)
        part = img.crop((0, skip, min(width_px, img.width), img.height))
        canvas.paste(part, (0, round(c["want"] * dpr)))
    canvas.save(out / f"{slug}-{w}-{theme}.png")
    summary[key] = {"sideways": info["sw"] > info["cw"], "sw": info["sw"], "cw": info["cw"], "h": info["h"], "url": info["url"]}
(out / f"{slug}-checks.json").write_text(json.dumps(summary))
print(slug, json.dumps(summary))
