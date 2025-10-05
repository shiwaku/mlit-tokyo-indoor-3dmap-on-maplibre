#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
新宿駅 屋内地図R2（Shapefile群）→ フロアごとにZを付与した3D GeoJSONを一括生成。
- ./shape 配下の .shp を全て処理
- WGS84(EPSG:4326) へ再投影（入力にCRSがある場合）
- ファイル名からフロアを推定し、BASE_Z + FLOOR_OFFSETS[階] で絶対Zを付与
- 出力は ./geojson に *.3d.geojson
- 空/NULL/無効ジオメトリは安全にスキップ（必要なら make_valid で救済）
"""

import json, re, sys
from pathlib import Path
import geopandas as gpd
from shapely.geometry import (
    Point, LineString, Polygon, MultiPoint, MultiLineString,
    MultiPolygon, GeometryCollection, mapping
)

# shapely>=2 があれば無効ジオメトリ救済を有効化
try:
    from shapely.validation import make_valid  # type: ignore
except Exception:
    make_valid = None

# ====== 設定（必要に応じて変更） =========================================
BASE_Z = 3.2  # 新宿駅の基準標高 (m, AMSL)

# フロアごとの相対オフセット (m)。BASE_Z に加算して絶対高さにします。
# 地下はマイナス、屋外通路(out)は該当階と同じに設定。ここでは見やすさ優先で○倍強調。
FLOOR_OFFSETS = {
    "B3": -35.0,
    "B2": -25.0,
    "B1": -15.0,
    "0F":  0.0,
    "1F": +15.0,
    "2F": +25.0,  "2out": +25.0,
    "3F": +35.0,  "3out": +35.0,
    "4F": +45.0,  "4out": +45.0,
}

# 入出力ディレクトリ
INPUT_DIR  = Path("./shape")
OUTPUT_DIR = Path("./geojson")
# ======================================================================

# 代表的なファイル名パターン（ShinjukuTerminal_X_*）
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
    """ファイル名からフロアラベルを推定（デフォルト0F）"""
    for pat, label in PATTERNS:
        if pat.search(filename):
            return label
    return "0F"

def add_z_to_geom(geom, z: float):
    """2D/3D Geometry → 3D（全頂点に同一Zで上書き）"""
    if geom is None or getattr(geom, "is_empty", False):
        return geom
    gt = geom.geom_type

    if gt == "Point":
        return Point(geom.x, geom.y, z)

    if gt == "MultiPoint":
        return MultiPoint([Point(p.x, p.y, z) for p in geom.geoms])

    if gt == "LineString":
        # 既存にZがあっても z で上書き
        return LineString([(xy[0], xy[1], z) for xy in list(geom.coords)])

    if gt == "MultiLineString":
        return MultiLineString([
            LineString([(xy[0], xy[1], z) for xy in list(ls.coords)]) for ls in geom.geoms
        ])

    if gt == "Polygon":
        ext = [(xy[0], xy[1], z) for xy in list(geom.exterior.coords)]
        ints = [[(xy[0], xy[1], z) for xy in list(r.coords)] for r in geom.interiors]
        return Polygon(ext, ints)

    if gt == "MultiPolygon":
        polys = []
        for p in geom.geoms:
            ext = [(xy[0], xy[1], z) for xy in list(p.exterior.coords)]
            ints = [[(xy[0], xy[1], z) for xy in list(r.coords)] for r in p.interiors]
            polys.append(Polygon(ext, ints))
        return MultiPolygon(polys)

    if gt == "GeometryCollection":
        return GeometryCollection([add_z_to_geom(g, z) for g in geom.geoms])

    # 想定外タイプは素通し
    return geom

def clean_geoms(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """NULL/空/無効ジオメトリを除去し、可能なら make_valid で救済"""
    if gdf is None or gdf.empty:
        return gdf

    # NULL を除去
    gdf = gdf[~gdf.geometry.isna()].copy()

    # 空を除去
    if hasattr(gdf.geometry, "is_empty"):
        gdf = gdf[~gdf.geometry.is_empty].copy()

    # 可能なら救済 → 再度 空を除去
    if make_valid is not None and not gdf.empty:
        gdf["geometry"] = gdf["geometry"].map(make_valid)
        if hasattr(gdf.geometry, "is_empty"):
            gdf = gdf[~gdf.geometry.is_empty].copy()

    return gdf

def process_shp(shp: Path):
    label = infer_floor_label(shp.name)
    if label not in FLOOR_OFFSETS:
        print(f"[WARN] {shp.name}: 未定義のフロア '{label}' → 0F扱い")
        label = "0F"
    z_abs = BASE_Z + FLOOR_OFFSETS[label]

    # 1) 読み込み
    gdf = gpd.read_file(shp)

    # 2) ジオメトリクレンジング
    gdf = clean_geoms(gdf)
    if gdf is None or gdf.empty:
        print(f"[SKIP] {shp.name}: 有効なジオメトリがありません")
        return

    # 3) CRS がある場合のみ再投影（なければそのまま）
    if gdf.crs:
        try:
            gdf = gdf.to_crs(epsg=4326)
        except Exception as e:
            print(f"[WARN] {shp.name}: CRS変換に失敗しました（素通し）: {e}")

    # 4) Z 付与
    gdf3d = gdf.copy()
    gdf3d["geometry"] = gdf3d["geometry"].apply(lambda g: add_z_to_geom(g, z_abs))
    gdf3d["__floor"] = label
    gdf3d["__z_abs"] = float(z_abs)

    # 念のため最終チェック（Z付与後に空/Noneがあれば落とす）
    gdf3d = gdf3d[~gdf3d.geometry.isna()].copy()
    if hasattr(gdf3d.geometry, "is_empty"):
        gdf3d = gdf3d[~gdf3d.geometry.is_empty].copy()
    if gdf3d.empty:
        print(f"[SKIP] {shp.name}: Z付与後に有効なジオメトリなし")
        return

    # 5) GeoJSON 出力
    outname = shp.with_suffix("").name + ".3d.geojson"
    outpath = OUTPUT_DIR / outname
    outpath.parent.mkdir(parents=True, exist_ok=True)

    fc = {"type": "FeatureCollection", "features": []}
    for idx, row in gdf3d.iterrows():
        geom = row.geometry
        if geom is None or getattr(geom, "is_empty", False):
            print(f"[SKIP] {shp.name} idx={idx}: 空ジオメトリ")
            continue
        try:
            fc["features"].append({
                "type": "Feature",
                "geometry": mapping(geom),  # ここで __geo_interface__ を持たないと失敗する
                "properties": {k: v for k, v in row.items() if k != "geometry"}
            })
        except Exception as e:
            print(f"[SKIP] {shp.name} idx={idx}: mapping失敗: {e}")

    if not fc["features"]:
        print(f"[SKIP] {shp.name}: 出力対象なし")
        return

    with outpath.open("w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)

    print(f"[OK] {shp.name} -> {outpath.name}  floor={label} z={z_abs:.2f}")

def main():
    shps = list(INPUT_DIR.rglob("*.shp"))
    if not shps:
        print(f"[ERR] {INPUT_DIR} に .shp が見つかりません")
        sys.exit(1)

    # floors 表示は順序を固定して見やすくする
    floors_str = ", ".join([f"{k}:{v}" for k, v in FLOOR_OFFSETS.items()])
    print(f"[INFO] BASE_Z={BASE_Z}, floors={{ {floors_str} }}")
    print(f"[INFO] 対象SHP数: {len(shps)}")

    for shp in sorted(shps):
        try:
            process_shp(shp)
        except Exception as e:
            print(f"[ERR] {shp}: {e}")

if __name__ == "__main__":
    main()
