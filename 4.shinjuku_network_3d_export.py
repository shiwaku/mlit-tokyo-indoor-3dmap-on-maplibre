#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
歩行者ネットワーク（Shinjuku_node / Shinjuku_link）を3D GeoJSON化。
- ノードの ordinal（階層数）をもとに階ラベル（例：-2→2B）を自動生成
- BASE_Z + FLOOR_OFFSETS でノードZを計算
- リンクの start_id / end_id でノードZを対応付け、始点・終点のZを設定
- リンク形状は始点Z〜終点Zを線形補間して3D化
- 出力: ./Shinjuku_link_3d.geojson
"""

from pathlib import Path
import math
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString
from pyproj import CRS

# ====== 設定（必要に応じて変更） =========================================
DATA_DIR = Path("./shape/nw")
NODE_SHP = DATA_DIR / "Tokyo_node.shp"
LINK_SHP = DATA_DIR / "Tokyo_Link.shp"
OUT_GEOJSON = Path("./tokyo_link_3d.geojson")

# 列名（データ定義書に合わせて変更可能）
NODE_ID_COL = "node_id"
NODE_LVL_COL = "ordinal"      # 階層数（数値）
LINK_S_COL   = "start_id"
LINK_E_COL   = "end_id"

# Z = BASE_Z + FLOOR_OFFSETS[floor_label]
BASE_Z = 3.2  # 新宿駅の基準標高 (m, AMSL)

FLOOR_OFFSETS = {
    "3B": -35.0,
    "2B": -25.0,
    "1B": -15.0,
    "0F":   0.0,
    "1F": +15.0,
    "2F": +25.0,  "2out": +25.0,
    "3F": +35.0,  "3out": +35.0,
    "4F": +45.0,  "4out": +45.0,
}

# ====== 関数定義 ===========================================================

def ord_to_floor(o):
    """ordinal(float/int) → 階ラベル（例：-2→2B, 0→0F, 2→2F）"""
    if o is None:
        return None
    try:
        v = int(round(float(o)))
    except Exception:
        return None
    if v < 0:
        return f"{abs(v)}B"
    elif v == 0:
        return "0F"
    else:
        return f"{v}F"

def floor_to_z(floor_label: str) -> float | None:
    """階ラベルから絶対高さを計算"""
    if floor_label is None:
        return None
    off = FLOOR_OFFSETS.get(floor_label)
    return None if off is None else BASE_Z + off

# ---- 2D距離に沿ってZを線形内挿（端点は指定Zに一致） ----------------------
def _interp_line(line: LineString, z0: float, z1: float) -> LineString:
    xy = list(line.coords)
    if len(xy) == 1:
        x, y = xy[0]
        return LineString([(x, y, z0)])
    # 各セグメント長
    seg = [math.hypot(xy[i+1][0]-xy[i][0], xy[i+1][1]-xy[i][1]) for i in range(len(xy)-1)]
    total = sum(seg) or 1e-9
    acc = 0.0
    coords = [(xy[0][0], xy[0][1], z0)]
    for i in range(1, len(xy)):
        acc += seg[i-1]
        t = acc / total
        z = z0 + (z1 - z0) * t
        coords.append((xy[i][0], xy[i][1], z))
    return LineString(coords)

def add_z_geometry(geom, z0: float, z1: float):
    if isinstance(geom, LineString):
        return _interp_line(geom, z0, z1)
    elif isinstance(geom, MultiLineString):
        return MultiLineString([_interp_line(g, z0, z1) for g in geom.geoms])
    else:
        return geom  # 想定外はそのまま

# ====== データ読込 =========================================================
print("[INFO] ノード・リンクデータを読込中...")
nodes = gpd.read_file(NODE_SHP)
links = gpd.read_file(LINK_SHP)

# CRSをWGS84(EPSG:4326)に統一
if nodes.crs and CRS.from_user_input(nodes.crs) != CRS.from_epsg(4326):
    nodes = nodes.to_crs(4326)
if links.crs and CRS.from_user_input(links.crs) != CRS.from_epsg(4326):
    links = links.to_crs(4326)

# ====== ノードに階層ラベルとZを付与 ========================================
nodes["floor_label"] = nodes[NODE_LVL_COL].apply(ord_to_floor)
nodes["z"] = nodes["floor_label"].apply(floor_to_z)

# ====== リンクに始点・終点Zを結合 ==========================================
L = links.merge(
        nodes[[NODE_ID_COL, "floor_label", "z"]]
            .rename(columns={NODE_ID_COL: LINK_S_COL, "floor_label": "floor_s", "z": "z_start"}),
        on=LINK_S_COL, how="left"
    ).merge(
        nodes[[NODE_ID_COL, "floor_label", "z"]]
            .rename(columns={NODE_ID_COL: LINK_E_COL, "floor_label": "floor_e", "z": "z_end"}),
        on=LINK_E_COL, how="left"
    )

# 欠損チェック
missing = L[L[["z_start", "z_end"]].isna().any(axis=1)]
if len(missing):
    print(f"[WARN] Z欠損のリンク: {len(missing)} 本（対応ノード不明）")
L = L.dropna(subset=["z_start", "z_end"]).copy()

# ====== 3D形状を生成 =======================================================
print("[INFO] リンク形状にZ値を付与中...")
L["geometry"] = L.apply(
    lambda r: add_z_geometry(r.geometry, float(r["z_start"]), float(r["z_end"])),
    axis=1
)
L = L.set_geometry("geometry", crs="EPSG:4326")

# ====== GeoJSON出力 ========================================================
print("[INFO] GeoJSONを書き出し中...")
L.to_file(OUT_GEOJSON, driver="GeoJSON", encoding="utf-8")

print(f"[DONE] 出力完了: {OUT_GEOJSON.resolve()}")
