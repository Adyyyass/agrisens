"""
Menghitung indeks vegetasi (NDVI, NDRE) per petak dari data multispektral
dan menyimpannya ke tabel parcel_vegetation_indices.
"""

import os
import sys
import json
import numpy as np
import rasterio
from rasterio.mask import mask
from shapely.geometry import shape
import psycopg2
import geopandas as gpd
from rasterio.transform import from_bounds

DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'agriculture_db',
    'user': 'postgres',
    'password': '456'
}

def calculate_ndvi(nir, red):
    with np.errstate(divide='ignore', invalid='ignore'):
        ndvi = np.where((nir + red) == 0, 0, (nir - red) / (nir + red))
    return np.clip(ndvi, -1, 1)

def calculate_ndre(nir, rededge):
    with np.errstate(divide='ignore', invalid='ignore'):
        ndre = np.where((nir + rededge) == 0, 0, (nir - rededge) / (nir + rededge))
    return np.clip(ndre, -1, 1)

def get_parcels(conn):
    """Ambil semua polygon petak dari database (yang sudah terdeteksi YOLO)"""
    gdf = gpd.read_postgis("SELECT parcel_id, geometry FROM farm_parcels", conn, geom_col='geometry')
    return gdf

def load_band(path):
    """Load band raster dan return array serta profile"""
    with rasterio.open(path) as src:
        data = src.read(1).astype(float)
        profile = src.profile
        transform = src.transform
    return data, profile, transform

def zonal_statistics(polygon, band_array, transform):
    """Hitung rata-rata nilai band di dalam polygon (zonal statistics)."""
    from rasterio import features
    from rasterio.transform import from_bounds as _from_bounds
    from shapely.geometry import mapping, box as shapely_box

    geoms = [mapping(polygon)]

    # Guard: if polygon is entirely outside raster extent, return None silently
    raster_h, raster_w = band_array.shape
    raster_bounds = rasterio.transform.array_bounds(raster_h, raster_w, transform)
    raster_box = shapely_box(*raster_bounds)
    try:
        if not polygon.intersects(raster_box):
            print(f"[zonal_statistics] Polygon outside raster extent — skipped.")
            return None
    except Exception:
        pass

    try:
        # Build an in-memory rasterio dataset from the numpy array
        profile = {
            'driver': 'MEM',
            'dtype': str(band_array.dtype),
            'width': raster_w,
            'height': raster_h,
            'count': 1,
            'crs': None,
            'transform': transform,
        }
        import io
        with rasterio.MemoryFile() as memfile:
            with memfile.open(**{**profile, 'driver': 'GTiff'}) as dataset:
                dataset.write(band_array.astype(np.float32), 1)
            with memfile.open() as dataset:
                out_image, _ = rasterio.mask.mask(
                    dataset, geoms, crop=True, all_touched=True
                )
        valid = out_image[0][~np.isnan(out_image[0])]
        if len(valid) == 0:
            return None
        return float(np.mean(valid))
    except Exception as e:
        print(f"[zonal_statistics] Error: {e}")
        return None

def process_upload(upload_id, files_dict):
    """Proses utama: hitung NDVI dan NDRE untuk setiap petak"""
    print(f" Memproses upload_id: {upload_id}")
    
    # 1. Koneksi database
    conn = psycopg2.connect(**DB_CONFIG)
    
    # 2. Ambil semua petak
    gdf = get_parcels(conn)
    if len(gdf) == 0:
        print(" Tidak ada petak sawah. Lakukan deteksi YOLO terlebih dahulu.")
        conn.close()
        return
    
    print(f"   Ditemukan {len(gdf)} petak.")
    
    # 3. Load band-band yang diperlukan
    bands = {}
    for name, path in files_dict.items():
        if os.path.exists(path):
            data, prof, trans = load_band(path)
            bands[name] = data
            # Simpan transform dari salah satu band (misal nir)
            if name == 'nir':
                transform = trans
    
    if 'nir' not in bands:
        print(" NIR band wajib ada.")
        conn.close()
        return
    
    # 4. Hitung indeks per petak
    results = []
    for idx, row in gdf.iterrows():
        parcel_id = row['parcel_id']
        geometry = row['geometry']
        
        ndvi_val = None
        ndre_val = None
        
        # NDVI if red available
        if 'red' in bands:
            ndvi_arr = calculate_ndvi(bands['nir'], bands['red'])
            ndvi_val = zonal_statistics(geometry, ndvi_arr, transform)
        
        # NDRE if rededge available
        if 'rededge' in bands:
            ndre_arr = calculate_ndre(bands['nir'], bands['rededge'])
            ndre_val = zonal_statistics(geometry, ndre_arr, transform)
        
        results.append((parcel_id, upload_id, ndvi_val, ndre_val))
        
        if (idx+1) % 10 == 0:
            print(f"   Progress: {idx+1}/{len(gdf)} petak")
    
    # 5. Simpan ke database
    cur = conn.cursor()
    for parcel_id, up_id, ndvi, ndre in results:
        cur.execute("""
            INSERT INTO parcel_vegetation_indices (parcel_id, upload_id, ndvi_value, ndre_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (parcel_id, upload_id) DO UPDATE
            SET ndvi_value = EXCLUDED.ndvi_value,
                ndre_value = EXCLUDED.ndre_value,
                calculation_date = NOW()
        """, (parcel_id, up_id, ndvi, ndre))
    conn.commit()
    cur.close()
    conn.close()
    
    print(f" Selesai. {len(results)} petak diperbarui.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--upload_id", required=True)
    parser.add_argument("--files_json", required=True)
    parser.add_argument("--output_dir", required=False)
    args = parser.parse_args()
    
    files_dict = json.loads(args.files_json)
    process_upload(args.upload_id, files_dict)