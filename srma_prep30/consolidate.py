#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path("downloaded_prep")
OUT = Path("SRMA_30_Preparation_Artifacts")
OUT.mkdir(exist_ok=True)

inventory = []
for p in sorted(ROOT.rglob("*")):
    if not p.is_file():
        continue
    if p.name == "task_log.json":
        try:
            log = json.loads(p.read_text(encoding="utf-8"))
            inventory.append({
                "Task": log.get("task"),
                "Title": log.get("title"),
                "Artifact": log.get("file"),
                "Created_UTC": log.get("created_utc"),
                "Source_path": str(p.parent),
                "Completed_review_claimed": False,
            })
        except Exception:
            pass
    else:
        target = OUT / p.name
        if target.exists():
            target = OUT / f"{p.parent.name}_{p.name}"
        target.write_bytes(p.read_bytes())

inventory = sorted(inventory, key=lambda x: int(x.get("Task") or 999))
with (OUT / "inventory.csv").open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["Task","Title","Artifact","Created_UTC","Source_path","Completed_review_claimed"])
    w.writeheader(); w.writerows(inventory)

readme = """# SRMA Bangladesh: 30 parallel preparation artifacts

These files establish screening, extraction, risk-of-bias, synthesis, PRISMA, GRADE, and submission infrastructure for PROSPERO CRD420261461557.

They are templates and prospective work products. They do **not** document completed title/abstract screening, full-text screening, data extraction, risk-of-bias assessment, or independent duplicate review.

Review team:
- Md. Mizanoor Rahman — lead reviewer
- Kapashia Binte Giash — second reviewer
- Department of Statistics, Mawlana Bhashani Science and Technology University
"""
(OUT / "README.md").write_text(readme, encoding="utf-8")

manifest = []
for p in sorted(OUT.iterdir()):
    if p.is_file():
        manifest.append({"file": p.name, "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
with (OUT / "sha256_manifest.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["file","bytes","sha256"]); w.writeheader(); w.writerows(manifest)

with zipfile.ZipFile("SRMA_30_Preparation_Artifacts.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(OUT.iterdir()):
        if p.is_file(): z.write(p, arcname=p.name)

print(json.dumps({"tasks_received": len(inventory), "files_packaged": len(list(OUT.iterdir()))}, indent=2))
