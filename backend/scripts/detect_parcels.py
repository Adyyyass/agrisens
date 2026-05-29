"""
Deteksi Petak Sawah menggunakan YOLOv11 dengan Sliding Window
Input: Orthomosaic (GeoTIFF)
Output: GeoJSON polygon petak sawah + insert ke PostgreSQL
"""

import cv2
import numpy as np
import rasterio
from rasterio.plot import reshape_as_image
from ultralytics import YOLO
from shapely.geometry import Polygon
from shapely.ops import transform as shapely_transform
import geopandas as gpd
import psycopg2
import pyproj
import torch
import os
import warnings
import argparse
warnings.filterwarnings('ignore')

MODEL_PATH = "/home/labtefa/webgis_agriculture/backend/models/best.pt"
OUTPUT_FOLDER = os.path.join(os.path.dirname(__file__), "..", "data", "output")
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'agriculture_db',
    'user': 'postgres',
    'password': '456'
}

CONFIDENCE_THRESHOLD = 0.40
IOU_THRESHOLD = 0.4
TILE_SIZE = 2048
OVERLAP = 0.3  # 30% overlap antar tile
IOU_MERGE = 0.15  # Greedy NMS threshold for polygon deduplication
MIN_AREA_HA = 0.02  # Minimum parcel area (200 m²)
MAX_AREA_HA = 6.0   # Maximum parcel area (6 ha)


def load_orthomosaic(ortho_path):
    print(f"📖 Membaca orthomosaic: {ortho_path}")
    src = rasterio.open(ortho_path)
    img = src.read()

    if img.shape[0] >= 3:
        img_rgb = img[:3]
        img_display = reshape_as_image(img_rgb)
    else:
        img_gray = img[0]
        img_display = cv2.cvtColor(img_gray.astype('uint8'), cv2.COLOR_GRAY2RGB)

    # Normalize ke uint8 jika belum
    if img_display.dtype != np.uint8:
        for i in range(img_display.shape[2]):
            ch = img_display[:, :, i].astype(float)
            p2, p98 = np.percentile(ch[ch > 0], (2, 98)) if np.any(ch > 0) else (0, 255)
            img_display[:, :, i] = np.clip((ch - p2) / (p98 - p2 + 1e-5) * 255, 0, 255).astype(np.uint8)

    print(f"   Dimensi: {img_display.shape}, CRS: {src.crs}")
    return src, img_display


def pixel_to_geo(x, y, geo_transform):
    lon, lat = geo_transform * (x, y)
    return lon, lat


def calculate_area_hectare(polygon):
    try:
        centroid_lon = polygon.centroid.x
        zone = int((centroid_lon + 180) / 6) + 1
        utm_crs = f"EPSG:326{zone}" if centroid_lon >= 0 else f"EPSG:327{zone}"
        project = pyproj.Transformer.from_crs('EPSG:4326', utm_crs, always_xy=True).transform
        poly_meter = shapely_transform(project, polygon)
        return round(poly_meter.area / 10000, 4)
    except:
        return 0


def run_sliding_window(img, model, tile_size=640, overlap=0.3, conf=0.20, iou=0.4):
    """Jalankan YOLO dengan sliding window pada gambar besar"""
    h, w = img.shape[:2]
    stride = int(tile_size * (1 - overlap))

    all_boxes = []
    all_scores = []
    all_masks_info = []  # (mask_array, offset_x, offset_y)

    total_tiles = len(range(0, h, stride)) * len(range(0, w, stride))
    processed = 0

    print(f"   Image: {w}x{h}, Tile: {tile_size}, Stride: {stride}")
    print(f"   Total tiles: ~{total_tiles}")

    for y in range(0, h, stride):
        for x in range(0, w, stride):
            x_end = min(x + tile_size, w)
            y_end = min(y + tile_size, h)
            tile = img[y:y_end, x:x_end]

            # Skip tile terlalu kecil atau mostly hitam
            if tile.shape[0] < 64 or tile.shape[1] < 64:
                continue
            if np.mean(tile) < 5:
                continue

            results = model(tile, conf=conf, iou=iou, verbose=False)

            for result in results:
                if result.boxes is not None and len(result.boxes) > 0:
                    boxes = result.boxes.xyxy.cpu().numpy().copy()
                    scores = result.boxes.conf.cpu().numpy()

                    # Translate ke koordinat gambar penuh
                    boxes[:, [0, 2]] += x
                    boxes[:, [1, 3]] += y

                    all_boxes.extend(boxes.tolist())
                    all_scores.extend(scores.tolist())

                    # Simpan mask dengan offset
                    if result.masks is not None:
                        masks_xy = result.masks.xy  # list of numpy arrays
                        for mi, mask_pts in enumerate(masks_xy):
                            # Translate titik mask ke koordinat penuh
                            translated = mask_pts.copy()
                            translated[:, 0] += x
                            translated[:, 1] += y
                            all_masks_info.append({
                                'points': translated,
                                'score': float(scores[mi]),
                                'box': boxes[mi].tolist()
                            })

            processed += 1
            if processed % 50 == 0:
                print(f"   Progress: {processed}/{total_tiles} tiles")

    print(f"   Selesai: {len(all_boxes)} deteksi sebelum NMS global")

    # Global NMS untuk hapus duplikat antar tile
    if len(all_boxes) == 0:
        return []

    try:
        import torchvision
        boxes_t = torch.tensor(all_boxes, dtype=torch.float32)
        scores_t = torch.tensor(all_scores, dtype=torch.float32)
        keep = torchvision.ops.nms(boxes_t, scores_t, iou_threshold=0.15)
        keep_indices = keep.numpy().tolist()
        print(f"   Setelah NMS: {len(keep_indices)} deteksi")
        return [all_masks_info[i] for i in keep_indices if i < len(all_masks_info)]
    except Exception as e:
        print(f"   NMS error: {e}, pakai semua deteksi")
        return all_masks_info


def detect_parcels(ortho_path, ortho_id=None):
    print("=" * 60)
    print("🌾 DETEKSI PETAK SAWAH - SLIDING WINDOW")
    print("=" * 60)

    if not os.path.exists(MODEL_PATH):
        print(f"❌ Model tidak ditemukan: {MODEL_PATH}")
        return None

    # 1. Load
    src, img_display = load_orthomosaic(ortho_path)
    geo_transform = src.transform

    # 2. Load model
    print("🤖 Memuat model YOLO...")
    model = YOLO(MODEL_PATH)

    # 3. Sliding window
    print("🔍 Melakukan deteksi dengan sliding window...")
    detections = run_sliding_window(
        img_display, model,
        tile_size=TILE_SIZE,
        overlap=OVERLAP,
        conf=CONFIDENCE_THRESHOLD,
        iou=IOU_THRESHOLD
    )

    # 4. Konversi mask ke polygon geo
    print("📐 Mengkonversi ke polygon geografis...")
    all_parcels = []

    for det in detections:
        points = det['points']
        if len(points) < 3:
            continue

        # Konversi pixel → geo
        geo_points = []
        for px, py in points:
            lon, lat = pixel_to_geo(float(px), float(py), geo_transform)
            geo_points.append((lon, lat))

        try:
            poly = Polygon(geo_points)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.area <= 0:
                continue

            area_ha = calculate_area_hectare(poly)
            if area_ha < 0.001:  # Skip < 10m2
                continue

            all_parcels.append({
                'geometry': poly,
                'area_hectare': area_ha,
                'area_m2': round(area_ha * 10000, 2),
                'area_are': round(area_ha * 100, 2),
                'confidence': det['score'],
                'crop_type': 'padi'
            })
        except Exception as e:
            continue

    # Filter by area min/max
    before_filter = len(all_parcels)
    all_parcels = [p for p in all_parcels
                   if MIN_AREA_HA <= p['area_hectare'] <= MAX_AREA_HA]
    print(f"   Filter area [{MIN_AREA_HA}-{MAX_AREA_HA} ha]: {before_filter} → {len(all_parcels)} petak")

    # Greedy IoU NMS with Shapely to eliminate duplicate polygons that survived torchvision NMS
    all_parcels_sorted = sorted(all_parcels, key=lambda x: x['confidence'], reverse=True)
    kept = []
    for candidate in all_parcels_sorted:
        poly_c = candidate['geometry']
        is_dup = False
        for kept_item in kept:
            poly_k = kept_item['geometry']
            try:
                intersection = poly_c.intersection(poly_k).area
                union = poly_c.union(poly_k).area
                iou_val = intersection / union if union > 0 else 0
                if iou_val > IOU_MERGE:
                    is_dup = True
                    break
            except Exception:
                pass
        if not is_dup:
            kept.append(candidate)
    all_parcels = kept
    print(f"   Setelah greedy IoU NMS (IOU_MERGE={IOU_MERGE}): {len(all_parcels)} petak")

    print(f"\n✅ Ditemukan {len(all_parcels)} petak sawah")

    if len(all_parcels) == 0:
        print("⚠️  Tidak ada petak terdeteksi.")
        return None

    gdf = gpd.GeoDataFrame(all_parcels, crs='EPSG:4326')
    geojson_path = os.path.join(OUTPUT_FOLDER, f'detected_parcels_{ortho_id}.geojson')
    gdf.to_file(geojson_path, driver='GeoJSON')
    print(f"💾 GeoJSON: {geojson_path}")
    print(f"📊 Total luas: {gdf['area_hectare'].sum():.2f} ha")

    return gdf


def import_to_postgresql(gdf, ortho_id=None):
    print("\n" + "=" * 60)
    print("🗄️  MENYIMPAN KE DATABASE")
    print("=" * 60)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        if ortho_id:
            cur.execute("DELETE FROM farm_parcels WHERE ortho_id = %s", (ortho_id,))
            conn.commit()

        inserted = 0
        for idx, row in gdf.iterrows():
            parcel_id = f"PARCEL_{ortho_id}_{idx+1:04d}"
            geom_wkt = row['geometry'].wkt
            cur.execute("""
                INSERT INTO farm_parcels (parcel_id, geometry, area_hectare, crop_type, ortho_id)
                VALUES (%s, ST_GeomFromText(%s, 4326), %s, %s, %s)
                ON CONFLICT (parcel_id) DO UPDATE
                SET geometry = EXCLUDED.geometry,
                    area_hectare = EXCLUDED.area_hectare,
                    crop_type = EXCLUDED.crop_type,
                    ortho_id = EXCLUDED.ortho_id
            """, (parcel_id, geom_wkt, row['area_hectare'], row['crop_type'], ortho_id))
            inserted += 1

        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ {inserted} petak berhasil disimpan")
        return inserted
    except Exception as e:
        print(f"❌ Error database: {e}")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ortho", required=True)
    parser.add_argument("--ortho_id", default="unknown")
    parser.add_argument("--weights", default=MODEL_PATH)
    args = parser.parse_args()

    MODEL_PATH = args.weights
    gdf = detect_parcels(args.ortho, args.ortho_id)

    if gdf is not None and len(gdf) > 0:
        import_to_postgresql(gdf, args.ortho_id)
        print("\n🎉 SELESAI!")
    else:
        print("\n⚠️  Tidak ada petak terdeteksi.")