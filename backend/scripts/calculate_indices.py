"""
Menghitung berbagai indeks vegetasi dari file multispektral
NDVI, NDRE, GNDVI, OSAVI
"""

import rasterio
import numpy as np
import psycopg2
from rasterio.mask import mask
from shapely.geometry import mapping
import geopandas as gpd
import os
import sys
import json

DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'agriculture_db',
    'user': 'postgres',
    'password': '456'
}

def calculate_ndvi(nir, red):
    """NDVI = (NIR - Red) / (NIR + Red)"""
    with np.errstate(divide='ignore', invalid='ignore'):
        ndvi = np.where((nir + red) == 0, -1, (nir - red) / (nir + red))
    return np.clip(ndvi, -1, 1)

def calculate_ndre(nir, rededge):
    """NDRE = (NIR - RedEdge) / (NIR + RedEdge)"""
    with np.errstate(divide='ignore', invalid='ignore'):
        ndre = np.where((nir + rededge) == 0, -1, (nir - rededge) / (nir + rededge))
    return np.clip(ndre, -1, 1)

def calculate_gndvi(nir, green):
    """GNDVI = (NIR - Green) / (NIR + Green)"""
    with np.errstate(divide='ignore', invalid='ignore'):
        gndvi = np.where((nir + green) == 0, -1, (nir - green) / (nir + green))
    return np.clip(gndvi, -1, 1)

def calculate_osavi(nir, red):
    """OSAVI = (NIR - Red) / (NIR + Red + 0.16)"""
    L = 0.16
    with np.errstate(divide='ignore', invalid='ignore'):
        osavi = np.where((nir + red + L) == 0, -1, (nir - red) / (nir + red + L))
    return np.clip(osavi, -1, 1)

def get_parcels():
    """Ambil semua polygon petak dari database"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        gdf = gpd.read_postgis("SELECT parcel_id, geometry FROM farm_parcels", conn, geom_col='geometry')
        conn.close()
        return gdf
    except Exception as e:
        print(f"Error get_parcels: {e}")
        return None

def load_raster_data(file_path):
    """Load raster dan return data array"""
    try:
        with rasterio.open(file_path) as src:
            data = src.read(1).astype(float)
            transform = src.transform
        return data, transform
    except Exception as e:
        print(f"Error load {file_path}: {e}")
        return None, None

def calculate_indices_for_parcels(upload_id, files):
    """Hitung semua indeks untuk setiap petak"""
    
    print(f" Menghitung indeks untuk upload: {upload_id}")
    print(f"   File yang tersedia: {list(files.keys())}")
    
    # Load semua file yang ada
    data = {}
    
    for key, path in files.items():
        if path and os.path.exists(path):
            print(f"   Memuat {key}: {path}")
            arr, trans = load_raster_data(path)
            if arr is not None:
                data[key] = arr
        else:
            print(f"    File {key} tidak ditemukan: {path}")
    
    # Ambil petak dari database
    gdf = get_parcels()
    if gdf is None or len(gdf) == 0:
        print(" Tidak ada data petak di database!")
        return []
    
    print(f"   Memproses {len(gdf)} petak...")
    
    results = []
    
    for idx, row in gdf.iterrows():
        parcel_id = row['parcel_id']
        geometry = row['geometry']
        geoms = [mapping(geometry)]
        
        parcel_result = {
            'parcel_id': parcel_id,
            'upload_id': upload_id,
            'ndvi': None,
            'ndre': None,
            'gndvi': None,
            'osavi': None
        }
        
        # NDVI (butuh NIR dan Red)
        if 'nir' in data and 'red' in data:
            try:
                # Ekstrak nilai dalam polygon (sederhana dengan centroid)
                centroid = geometry.centroid
                # Cari pixel terdekat (perkiraan sederhana)
                # Untuk akurasi lebih baik, gunakan zonal statistics
                parcel_result['ndvi'] = 0.5  # Nilai sementara
                print(f"   {parcel_id}: NDVI = {parcel_result['ndvi']}")
            except Exception as e:
                print(f"   Error NDVI {parcel_id}: {e}")
        
        results.append(parcel_result)
        
        # Progress
        if (idx + 1) % 5 == 0:
            print(f"   Progress: {idx + 1}/{len(gdf)} petak")
    
    return results

def save_to_database(results):
    """Simpan hasil indeks ke database"""
    
    if not results:
        print(" Tidak ada hasil yang disimpan")
        return
    
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        saved = 0
        for r in results:
            cur.execute("""
                INSERT INTO vegetation_indices (parcel_id, upload_id, ndvi_value, ndre_value, gndvi_value, osavi_value)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (r['parcel_id'], r['upload_id'], r['ndvi'], r['ndre'], r['gndvi'], r['osavi']))
            saved += 1
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f" {saved} petak berhasil disimpan ke database")
    except Exception as e:
        print(f" Error simpan ke database: {e}")

def update_upload_status(upload_id, status):
    """Update status upload di database"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("UPDATE multispektral_data SET status = %s WHERE upload_id = %s", (status, upload_id))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error update status: {e}")

if __name__ == "__main__":
    print("=" * 50)
    print(" CALCULATE VEGETATION INDICES")
    print("=" * 50)
    
    if len(sys.argv) > 2:
        upload_id = sys.argv[1]
        files_json = sys.argv[2]
        files = json.loads(files_json)
        
        update_upload_status(upload_id, 'processing')
        
        results = calculate_indices_for_parcels(upload_id, files)
        
        if results:
            save_to_database(results)
            update_upload_status(upload_id, 'completed')
            print(" Perhitungan indeks selesai!")
        else:
            update_upload_status(upload_id, 'failed')
            print(" Perhitungan indeks gagal")
    else:
        print("Usage: python calculate_indices.py <upload_id> '<json_files>'")