"""
Deteksi Petak Sawah menggunakan YOLOv11
Input: Orthomosaic (GeoTIFF)
Output: GeoJSON polygon petak sawah + insert ke PostgreSQL
"""

import cv2
import numpy as np
import rasterio
from rasterio.plot import reshape_as_image
from ultralytics import YOLO
from shapely.geometry import Polygon
from shapely.ops import transform
import geopandas as gpd
import psycopg2
import pyproj
import os
import warnings
import argparse
warnings.filterwarnings('ignore')

# =====================================================
# KONFIGURASI DEFAULT (akan ditimpa oleh argument)
# =====================================================

# Atau jika pakai absolute path:
MODEL_PATH = "/home/labtefa/webgis_agriculture/backend/models/bestn.pt"

# Output folder
OUTPUT_FOLDER = os.path.join(os.path.dirname(__file__), "..", "data", "output")
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Konfigurasi Database
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'agriculture_db',
    'user': 'postgres',
    'password': '456'
}

# Parameter deteksi
CONFIDENCE_THRESHOLD = 0.3
IOU_THRESHOLD = 0.7
DOWNSAMPLE_FACTOR = 2

# =====================================================
# FUNGSI UTAMA
# =====================================================

def load_orthomosaic(ortho_path):
    """Membaca orthomosaic dan mendapatkan transformasi koordinat"""
    print(f" Membaca orthomosaic: {ortho_path}")
    src = rasterio.open(ortho_path)
    
    img = src.read()
    
    if img.shape[0] >= 3:
        img_rgb = img[:3]
        img_display = reshape_as_image(img_rgb)
    else:
        img_gray = img[0]
        img_display = cv2.cvtColor(img_gray.astype('uint8'), cv2.COLOR_GRAY2RGB)
    
    print(f"   Dimensi gambar: {img_display.shape}")
    print(f"   CRS: {src.crs}")
    
    return src, img_display


def pixel_to_geo(x, y, transform):
    """Konversi koordinat pixel ke koordinat geografis (lon, lat)"""
    lon, lat = transform * (x, y)
    return lon, lat


def mask_to_polygons(mask, transform, min_area_pixels=100):
    """Konversi mask YOLO (binary) ke polygon"""
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), 
        cv2.RETR_EXTERNAL, 
        cv2.CHAIN_APPROX_SIMPLE
    )
    
    polygons = []
    for contour in contours:
        if cv2.contourArea(contour) < min_area_pixels:
            continue
        
        epsilon = 0.005 * cv2.arcLength(contour, True)
        simplified = cv2.approxPolyDP(contour, epsilon, True)
        
        if len(simplified) >= 3:
            geo_points = []
            for point in simplified:
                x, y = point[0]
                lon, lat = pixel_to_geo(x, y, transform)
                geo_points.append((lon, lat))
            
            poly = Polygon(geo_points)
            if poly.is_valid and not poly.is_empty and poly.area > 0:
                polygons.append(poly)
    
    return polygons


def calculate_area_hectare(polygon, src_crs):
    """Hitung luas polygon dalam hektar"""
    try:
        centroid_lon = polygon.centroid.x
        zone = int((centroid_lon + 180) / 6) + 1
        utm_crs = f"EPSG:327{zone}" if centroid_lon < 0 else f"EPSG:326{zone}"
        
        project = pyproj.Transformer.from_crs(
            'EPSG:4326',
            utm_crs,
            always_xy=True
        ).transform
        
        poly_meter = transform(project, polygon)
        area_sqm = poly_meter.area
        area_hectare = area_sqm / 10000
        return round(area_hectare, 4)
    except Exception as e:
        print(f"    Error hitung luas: {e}")
        return 0


def detect_parcels(ortho_path, ortho_id=None):
    """Fungsi utama deteksi petak sawah"""
    
    print("=" * 60)
    print(" DETEKSI PETAK SAWAH DENGAN YOLOv11")
    print("=" * 60)
    print(f" Ortho path: {ortho_path}")
    print(f" Ortho ID: {ortho_id}")
    print(f" Model path: {MODEL_PATH}")
    
    # Cek apakah model ada
    if not os.path.exists(MODEL_PATH):
        print(f" Model YOLO tidak ditemukan di: {MODEL_PATH}")
        print("   Pastikan file best.pt sudah ditempatkan di backend/models/")
        return None
    
    # 1. Load orthomosaic
    src, img_display = load_orthomosaic(ortho_path)
    
    # 2. Downsample
    if DOWNSAMPLE_FACTOR > 1:
        h, w = img_display.shape[:2]
        new_h, new_w = h // DOWNSAMPLE_FACTOR, w // DOWNSAMPLE_FACTOR
        img_display = cv2.resize(img_display, (new_w, new_h))
        print(f"   Downsample: {h}x{w} → {new_h}x{new_w}")
    
    # 3. Load model YOLO
    print(" Memuat model YOLO...")
    model = YOLO(MODEL_PATH)
    
    # 4. Deteksi
    print(" Melakukan deteksi...")
    results = model(img_display, conf=CONFIDENCE_THRESHOLD, iou=IOU_THRESHOLD)
    
    # 5. Proses hasil
    print(" Mengkonversi hasil ke polygon...")
    all_parcels = []
    transform = src.transform
    
    for result in results:
        if result.masks is not None:
            masks = result.masks.data.cpu().numpy()
            boxes = result.boxes if result.boxes else None
            
            for i, mask in enumerate(masks):
                if DOWNSAMPLE_FACTOR > 1:
                    original_shape = (src.height, src.width)
                    mask = cv2.resize(mask, (original_shape[1], original_shape[0]))
                
                mask_binary = (mask > 0.5).astype(np.uint8)
                polygons = mask_to_polygons(mask_binary, transform)
                confidence = float(boxes.conf[i]) if boxes is not None else 1.0
                
                for poly in polygons:
                    area_ha = calculate_area_hectare(poly, src.crs)
                    if area_ha < 0.001:
                        continue
                    all_parcels.append({
                        'geometry': poly,
                        'area_hectare': area_ha,
                        'confidence': confidence,
                        'crop_type': 'padi'
                    })
    
    print(f"\n Deteksi selesai! Ditemukan {len(all_parcels)} petak sawah")
    
    if len(all_parcels) == 0:
        print(" Tidak ada petak terdeteksi.")
        return None
    
    gdf = gpd.GeoDataFrame(all_parcels, crs='EPSG:4326')
    
    # Simpan ke GeoJSON
    geojson_path = os.path.join(OUTPUT_FOLDER, f'detected_parcels_{ortho_id}.geojson')
    gdf.to_file(geojson_path, driver='GeoJSON')
    print(f" GeoJSON disimpan: {geojson_path}")
    
    total_area = gdf['area_hectare'].sum()
    print(f"\n STATISTIK: Total {total_area:.2f} ha, {len(all_parcels)} petak")
    
    return gdf


def import_to_postgresql(gdf, ortho_id=None):
    """Impor data petak ke PostgreSQL"""
    
    print("\n" + "=" * 60)
    print(" MENYIMPAN KE DATABASE")
    print("=" * 60)
    
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        if ortho_id:
            print(f" Menghapus data lama untuk ortho_id: {ortho_id}")
            cur.execute("DELETE FROM farm_parcels WHERE ortho_id = %s", (ortho_id,))
            conn.commit()
        
        inserted = 0
        for idx, row in gdf.iterrows():
            parcel_id = f"PARCEL_{idx+1:04d}"
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
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f" {inserted} petak berhasil disimpan ke database")
        return inserted
        
    except Exception as e:
        print(f" Error database: {e}")
        return 0


# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ortho", help="Path orthomosaic", required=True)
    parser.add_argument("--ortho_id", help="ID orthomosaic untuk disimpan di database")
    parser.add_argument("--upload_id", help="Upload ID untuk multispektral (opsional)")
    args = parser.parse_args()
    
    # Gunakan argument dari command line
    ortho_path = args.ortho
    ortho_id = args.ortho_id if args.ortho_id else "unknown"
    
    if args.upload_id:
        print(f" Upload ID (multispektral): {args.upload_id}")
    
    # Jalankan deteksi
    gdf = detect_parcels(ortho_path, ortho_id)
    
    if gdf is not None and len(gdf) > 0:
        import_to_postgresql(gdf, ortho_id)
        print("\n SEMUA SELESAI!")
    else:
        print("\n Tidak ada petak yang dideteksi.")