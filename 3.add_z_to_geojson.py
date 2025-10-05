#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GeoJSON の全座標に Z=BASE_Z を付与（または欠損時のみ付与）
- MultiPolygon/Polygon/MultiLineString/… すべて対応
- FeatureCollection / Feature / GeometryCollection もOK
"""

import json
from pathlib import Path

# ===== 設定（ここだけ編集）=========================================
IN_FILE  = Path("r3_tokyo_tochiriyo.geojson")      # 入力ファイル
OUT_FILE = Path("r3_tokyo_tochiriyo.add.z.geojson") # 出力ファイル

BASE_Z   = 3.2     # 付与する標高[m]
FILL_ONLY = False   # False: 既存Zも上書き / True: Zが無い点だけ付与
# ================================================================

def with_z(coord):
    """coord が [x,y] or [x,y,z,...] のいずれでも Z を調整して返す"""
    if not isinstance(coord, list) or len(coord) < 2:
        return coord
    x, y = coord[0], coord[1]
    if len(coord) >= 3:
        if FILL_ONLY:
            return coord  # 既存Zを尊重
        else:
            out = coord[:]  # 上書き
            out[2] = BASE_Z
            return out
    else:
        # Zが無い → 追加
        return [x, y, BASE_Z]

def walk_coords(obj):
    """coordinates 配列の任意次元リストを再帰で処理"""
    if isinstance(obj, list):
        # coord (数値配列) か、入れ子配列かを判定
        if len(obj) >= 2 and all(isinstance(c, (int, float)) for c in obj[:2]):
            return with_z(obj)
        else:
            return [walk_coords(e) for e in obj]
    return obj

def process_geom(geom):
    if not isinstance(geom, dict) or "type" not in geom:
        return geom
    gtype = geom["type"]
    if gtype == "GeometryCollection":
        geoms = geom.get("geometries", [])
        geom["geometries"] = [process_geom(g) for g in geoms]
        return geom
    if "coordinates" in geom:
        geom["coordinates"] = walk_coords(geom["coordinates"])
    return geom

def process(obj):
    if not isinstance(obj, dict) or "type" not in obj:
        return obj
    otype = obj["type"]
    if otype == "FeatureCollection":
        feats = obj.get("features", [])
        for ft in feats:
            if isinstance(ft, dict) and "geometry" in ft:
                ft["geometry"] = process_geom(ft["geometry"])
        return obj
    elif otype == "Feature":
        if "geometry" in obj:
            obj["geometry"] = process_geom(obj["geometry"])
        return obj
    else:
        # Geometry 単体
        return process_geom(obj)

def main():
    data = json.loads(IN_FILE.read_text(encoding="utf-8"))
    data = process(data)
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Wrote {OUT_FILE} with Z={BASE_Z} (FILL_ONLY={FILL_ONLY})")

if __name__ == "__main__":
    main()
