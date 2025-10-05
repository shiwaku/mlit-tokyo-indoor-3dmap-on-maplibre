#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
同じフロアの 3D GeoJSON（*.3d.geojson）をマージする簡易スクリプト。

- INPUT_DIR 配下の *.3d.geojson を走査（再帰）
- ファイル名からフロアを推定（B3/B2/B1/0/1/2/3/4, 2out/3out/4out）
- モード:
    'per_floor'            → フロアごとに1ファイル
    'per_floor_and_kind'   → フロア×種類（Space/Floor/…）ごとに1ファイル
    'per_floor_by_geom'    → フロア×ジオメトリタイプ（points/lines/polygons）ごとに1ファイル  ←★追加

出力例:
  geojson_merged/Shinjuku_B2.merged.3d.geojson                  （per_floor）
  geojson_merged/Shinjuku_B2_Space.merged.3d.geojson           （per_floor_and_kind）
  geojson_merged/Shinjuku_B2.points.geojson                    （per_floor_by_geom）
"""

import json, re, sys, gzip
from pathlib import Path
from collections import defaultdict

# ====== ここだけ触ればOK ===========================================
INPUT_DIR  = Path("./geojson")        # 元の *.3d.geojson 置き場
OUTPUT_DIR = Path("./geojson_merged") # 出力先

# どの種類のファイルを対象にするか（名前に含まれるサフィックスで判定）
INCLUDE_KINDS = [
    "Space", "Floor", "Drawing", "Fixture", "Facility",
    "Opening", "TWSILine", "TWSIPoint"
]

# マージ単位:
#   'per_floor'           → フロアごとに 1 ファイル
#   'per_floor_and_kind'  → フロア×種類 ごとに 1 ファイル
#   'per_floor_by_geom'   → フロア×ジオメトリタイプ（points/lines/polygons）ごとに 1 ファイル
MERGE_MODE = 'per_floor_by_geom'  # ← 推奨

# 出力を gzip したい場合は True（拡張子は .geojson.gz）
GZIP_OUTPUT = False
# ====================================================================

# フロア推定用パターン（ファイル名に含まれるタグ）
PATTERNS = [
    (re.compile(r"_b3[_\.]", re.I), "B3"),
    (re.compile(r"_b2[_\.]", re.I), "B2"),
    (re.compile(r"_b1[_\.]", re.I), "B1"),
    (re.compile(r"_0[_\.]",  re.I), "0F"),
    (re.compile(r"_1[_\.]",  re.I), "1F"),
    (re.compile(r"_2out[_\.]", re.I), "2out"),
    (re.compile(r"_3out[_\.]", re.I), "3out"),
    (re.compile(r"_4out[_\.]", re.I), "4out"),
    (re.compile(r"_2[_\.]",  re.I), "2F"),
    (re.compile(r"_3[_\.]",  re.I), "3F"),
    (re.compile(r"_4[_\.]",  re.I), "4F"),
]

def infer_floor_label(filename: str) -> str:
    for pat, label in PATTERNS:
        if pat.search(filename):
            return label
    return "0F"  # 不明なら 0F に寄せる

def infer_kind(filename: str) -> str:
    # 例: ..._Space.3d.geojson → Space を拾う
    for k in INCLUDE_KINDS:
        if re.search(rf"_{re.escape(k)}\.3d\.geojson$", filename, re.I):
            return k
    # 見つからない場合は末尾の直前パートを拾っておく（保険）
    m = re.search(r"_([A-Za-z0-9]+)\.3d\.geojson$", filename)
    return m.group(1) if m else "Unknown"

def categorize_geom(ft) -> str:
    """
    Feature を points / lines / polygons / others に分類
    """
    geom = ft.get("geometry") or {}
    t = (geom.get("type") or "").lower()
    if t in ("point", "multipoint"):
        return "points"
    if t in ("linestring", "multilinestring"):
        return "lines"
    if t in ("polygon", "multipolygon"):
        return "polygons"
    if t == "geometrycollection":
        # 単一カテゴリならそれに寄せる、混在は polygons に寄せる
        cats = set()
        for g in geom.get("geometries") or []:
            tt = (g.get("type") or "").lower()
            if tt in ("point", "multipoint"): cats.add("points")
            elif tt in ("linestring", "multilinestring"): cats.add("lines")
            elif tt in ("polygon", "multipolygon"): cats.add("polygons")
        if len(cats) == 1:
            return cats.pop()
        return "polygons"
    return "others"

def load_features(path: Path):
    with path.open("r", encoding="utf-8") as f:
        gj = json.load(f)
    feats = gj.get("features", [])
    # 出自ファイル名を properties に残す（デバッグやトレース用）
    for ft in feats:
        props = ft.get("properties") or {}
        props["__source_file"] = path.name
        ft["properties"] = props
    return feats

def write_fc(path: Path, features):
    path.parent.mkdir(parents=True, exist_ok=True)
    fc = {"type": "FeatureCollection", "features": features}
    if GZIP_OUTPUT:
        gz = path.with_suffix(path.suffix + ".gz")
        with gzip.open(gz, "wt", encoding="utf-8") as f:
            json.dump(fc, f, ensure_ascii=False)
        print(f"[OK] {gz.name}  ({len(features)} features)")
    else:
        with path.open("w", encoding="utf-8") as f:
            json.dump(fc, f, ensure_ascii=False)
        print(f"[OK] {path.name}  ({len(features)} features)")

def main():
    files = sorted(INPUT_DIR.rglob("*.3d.geojson"))  # 再帰探索
    if not files:
        print(f"[ERR] {INPUT_DIR} に *.3d.geojson が見つかりません")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if MERGE_MODE not in ('per_floor', 'per_floor_and_kind', 'per_floor_by_geom'):
        print(f"[ERR] MERGE_MODE '{MERGE_MODE}' は不正です")
        sys.exit(2)

    if MERGE_MODE == 'per_floor_by_geom':
        # floor → {points:[], lines:[], polygons:[], others:[]}
        buckets = defaultdict(lambda: defaultdict(list))
        for p in files:
            floor = infer_floor_label(p.name)
            kind  = infer_kind(p.name)
            if kind not in INCLUDE_KINDS:
                continue
            feats = load_features(p)
            for ft in feats:
                cat = categorize_geom(ft)
                buckets[floor][cat].append(ft)

        # 書き出し（空バケツはスキップ）
        for floor, bycat in buckets.items():
            for cat in ("polygons", "lines", "points", "others"):
                feats = bycat.get(cat) or []
                if not feats:
                    continue
                outname = f"Tokyo_{floor}.{cat}.geojson"
                write_fc(OUTPUT_DIR / outname, feats)
        return

    # 既存モード（従来の動作）
    buckets = defaultdict(list)  # key → list[file]
    for p in files:
        floor = infer_floor_label(p.name)
        kind  = infer_kind(p.name)
        if kind not in INCLUDE_KINDS:
            continue
        if MERGE_MODE == 'per_floor':
            key = (floor,)
        else:  # per_floor_and_kind
            key = (floor, kind)
        buckets[key].append(p)

    for key, paths in buckets.items():
        if MERGE_MODE == 'per_floor':
            floor = key[0]
            outname = f"Tokyo_{floor}.merged.3d.geojson"
        else:
            floor, kind = key
            outname = f"Tokyo_{floor}_{kind}.merged.3d.geojson"

        features = []
        for p in paths:
            features.extend(load_features(p))
        write_fc(OUTPUT_DIR / outname, features)

if __name__ == "__main__":
    main()
