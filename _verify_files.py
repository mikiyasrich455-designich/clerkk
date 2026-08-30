import os
import json

base = r"C:\Users\mikiyas\Documents\TimeDetective\clerk"

# 1. index.html size + content sanity
idx = os.path.join(base, "index.html")
size = os.path.getsize(idx)
html = open(idx, encoding="utf-8").read()
print(f"[1] index.html = {size} bytes ({size/1024:.1f} KB)")
print(f"    has <html> : {'<html' in html}")
print(f"    has <body> : {'<body' in html}")
print(f"    has Clerk  : {'Clerk' in html}")
print(f"    has tailwind: {'cdn.tailwindcss.com' in html}")
print(f"    has JS boot : {'window.onload = boot' in html}")
print(f"    ends </html>: {html.rstrip().endswith('</html>')}")

# 2. backend.py import check
bp = os.path.join(base, "backend.py")
print(f"\n[2] backend.py exists = {os.path.exists(bp)}")

# 3. catalog count
cat = json.load(open(os.path.join(base, "catalog.json"), encoding="utf-8"))
print(f"[3] catalog products = {len(cat['products'])}")

# 4. insights seeded
ins = json.load(open(os.path.join(base, "insights.json"), encoding="utf-8"))
print(f"[4] seeded insights = {len(ins['log'])}")

# 5. vercel.json + requirements
vc = json.load(open(os.path.join(base, "vercel.json"), encoding="utf-8"))
print(f"[5] vercel.json routes = {len(vc['routes'])}")
print(f"    requirements.txt exists = {os.path.exists(os.path.join(base, 'requirements.txt'))}")

print("\nALL CHECKS COMPLETE")
