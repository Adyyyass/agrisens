import os
import asyncio
import shutil
import uuid
import json
import hashlib
import secrets
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, BackgroundTasks, Form, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import rasterio
import numpy as np
import cv2
from pyproj import Transformer
from ultralytics import YOLO

from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import Session

from fastapi.responses import HTMLResponse
from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Agrisens WebGIS API", version="1.0.0")
import os
from pathlib import Path

STATIC_DIR = Path("backend/static")
STATIC_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_STATIC_DIR = Path("backend/static/uploads")
UPLOAD_STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")


# =====================================================
# UPLOAD FOLDER CONFIGURATION
# =====================================================
ORTHO_UPLOAD_DIR = Path("backend/uploads/orthomosaic")
ORTHO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MULTISPECTRAL_DIR = Path("backend/uploads/multispektral")
MULTISPECTRAL_DIR.mkdir(parents=True, exist_ok=True)

# Model YOLO — loaded lazily on first use to avoid double-load segfault
_sahi_model = None

def get_sahi_model():
    global _sahi_model
    if _sahi_model is None:
        from sahi import AutoDetectionModel
        _sahi_model = AutoDetectionModel.from_pretrained(
            model_type='yolov8',
            model_path='/home/labtefa/webgis_agriculture/backend/models/best.pt',
            confidence_threshold=0.40,
            device='cpu',
        )
        print('[SAHI] Model loaded.')
    return _sahi_model

# =====================================================
# CORS MIDDLEWARE
# =====================================================
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# =====================================================
# UTILS
# =====================================================

def get_raster_center_and_bounds(tif_path):
    """Mendapatkan pusat koordinat dan bounding box dari file GeoTIFF serta luasnya"""
    try:
        with rasterio.open(tif_path) as src:
            print(f" Membaca file: {tif_path}")
            print(f"   CRS: {src.crs}")
            bounds = src.bounds
            print(f"   Bounds: {bounds}")
            center_lon = (bounds.left + bounds.right) / 2
            center_lat = (bounds.bottom + bounds.top) / 2
            print(f"   Center: {center_lon}, {center_lat}")
            
            # Hitung Luas
            res = src.res
            # Gunakan dataset_mask untuk mendapatkan area yang ada datanya saja
            mask = src.dataset_mask()
            valid_pixels = np.count_nonzero(mask)
            
            area_m2 = valid_pixels * abs(res[0] * res[1])
            
            if src.crs and src.crs.is_geographic:
                lat_center = (bounds.bottom + bounds.top) / 2
                meters_per_deg_lat = 111320
                meters_per_deg_lon = 111320 * np.cos(np.radians(lat_center))
                area_m2 = valid_pixels * (abs(res[0] * meters_per_deg_lon) * abs(res[1] * meters_per_deg_lat))
            
            area_ha = area_m2 / 10000
            print(f"   Area: {area_ha:.4f} Ha")
            
            return {
                'center_lon': center_lon,
                'center_lat': center_lat,
                'bounds': f"{bounds.left},{bounds.bottom},{bounds.right},{bounds.top}",
                'area_hectare': area_ha
            }
    except Exception as e:
        print(f" Error membaca koordinat/luas: {e}")
        return None

# =====================================================
# Pydantic Models
# =====================================================

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: Optional[str] = None

class LoginRequest(BaseModel):
    username: str
    password: str

# =====================================================
# DATABASE
# =====================================================

DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'agriculture_db', # Sesuaikan dengan database Anda
    'user': 'postgres',
    'password': '456'
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed

def generate_token() -> str:
    return secrets.token_urlsafe(32)

def create_session(user_id: int) -> str:
    token = generate_token()
    expires_at = datetime.now() + timedelta(hours=24)
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_sessions (user_id, token, expires_at)
        VALUES (%s, %s, %s)
    """, (user_id, token, expires_at))
    conn.commit()
    cur.close()
    conn.close()
    
    return token

# =====================================================
# ENDPOINTS AUTH
# =====================================================


@app.post("/api/auth/register")
async def register(request: RegisterRequest):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT id FROM users WHERE username = %s", (request.username,))
        if cur.fetchone():
            raise HTTPException(400, "Username sudah digunakan")
        
        cur.execute("SELECT id FROM users WHERE email = %s", (request.email,))
        if cur.fetchone():
            raise HTTPException(400, "Email sudah digunakan")
        
        password_hash = hash_password(request.password)
        
        cur.execute("""
            INSERT INTO users (username, email, password_hash, full_name, role)
            VALUES (%s, %s, %s, %s, 'user')
            RETURNING id, username, email, full_name
        """, (request.username, request.email, password_hash, request.full_name))
        
        user = cur.fetchone()
        conn.commit()
        token = create_session(user[0])
        
        cur.close()
        conn.close()
        
        return {
            "success": True,
            "message": "Registrasi berhasil",
            "user": {
                "id": user[0],
                "username": user[1],
                "email": user[2],
                "full_name": user[3]
            },
            "token": token
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(500, f"Internal error: {str(e)}")

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            SELECT id, username, email, full_name, password_hash, role
            FROM users 
            WHERE username = %s OR email = %s
        """, (request.username, request.username))
        
        user = cur.fetchone()
        
        if not user or not verify_password(request.password, user['password_hash']):
            raise HTTPException(401, "Username atau password salah")
        
        cur.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user['id'],))
        conn.commit()
        token = create_session(user['id'])
        
        cur.close()
        conn.close()
        
        return {
            "success": True,
            "message": "Login berhasil",
            "user": {
                "id": user['id'],
                "username": user['username'],
                "email": user['email'],
                "full_name": user['full_name'],
                "role": user['role']
            },
            "token": token
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(500, f"Internal error: {str(e)}")

@app.post("/api/auth/logout")
async def logout(token: str):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM user_sessions WHERE token = %s", (token,))
        conn.commit()
        cur.close()
        conn.close()
        return {"success": True, "message": "Logout berhasil"}
    except Exception as e:
        return {"success": True, "message": "Logout berhasil"}

# =====================================================
# ENDPOINT STATS
# =====================================================

@app.get("/api/stats")
async def get_statistics():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. Total Petak Sawah
        cur.execute("SELECT COUNT(*) as count FROM farm_parcels")
        count_ortho = cur.fetchone()['count'] or 0
        
        cur.execute("SELECT info_json FROM multispectral_uploads")
        rows_ms = cur.fetchall()
        
        count_ms = 0
        total_area_ms = 0
        healthy_parcels_ms = 0
        total_parcels_ms = 0

        for row in rows_ms:
            try:
                if not row['info_json']: continue
                info = json.loads(row['info_json'])
                count_ms += info.get('total_petak', 0)
                
                petak_list = info.get('petak_list', [])
                for petak in petak_list:
                    total_parcels_ms += 1
                    total_area_ms += petak.get('luas_ha', 0)
                    
                    is_healthy = False
                    if petak.get('lcc_scale') in [4, 5]:
                        is_healthy = True
                    m_ndvi = petak.get('mean_ndvi')
                    if m_ndvi is not None and m_ndvi >= 0.6:
                        is_healthy = True
                    m_ndre = petak.get('mean_ndre')
                    if m_ndre is not None and m_ndre >= 0.6:
                        is_healthy = True
                        
                    if is_healthy:
                        healthy_parcels_ms += 1
            except: continue

        # 2. Total Luas Lahan (Semua petak di farm_parcels)
        cur.execute("SELECT COALESCE(SUM(area_hectare), 0) as area FROM farm_parcels")
        area_ortho = float(cur.fetchone()['area'])
        
        # 3. Lahan Sehat dari farm_parcels
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN ndvi_value >= 0.6 OR ndre_value >= 0.6 THEN 1 ELSE 0 END) as healthy
            FROM vegetation_indices
        """)
        ortho_health = cur.fetchone()
        total_ortho_analyzed = ortho_health['total'] or 0
        healthy_ortho = ortho_health['healthy'] or 0
        
        total_analyzed = total_ortho_analyzed + total_parcels_ms
        total_healthy = healthy_ortho + healthy_parcels_ms
        
        healthy_percent = (total_healthy / total_analyzed * 100) if total_analyzed > 0 else 0
        
        # 4. Update Terakhir
        cur.execute("""
            SELECT MAX(updated_at) as last_update FROM (
                SELECT MAX(created_at) as updated_at FROM multispectral_uploads
                UNION
                SELECT MAX(uploaded_at) as updated_at FROM orthomosaic_history
                UNION
                SELECT MAX(created_at) as updated_at FROM farm_parcels
            ) as updates
        """)
        last_update = cur.fetchone()['last_update']
        
        # 5. Ambil data peta multispektral terbaru
        cur.execute("SELECT id, layers_json, info_json FROM multispectral_uploads ORDER BY created_at DESC LIMIT 1")
        latest_map = cur.fetchone()
        latest_map_data = None
        if latest_map:
            try:
                layers = json.loads(latest_map['layers_json'])
                # Tambahkan info tiles jika ada
                if 'rgb' in layers:
                    tiles_path = Path(f"frontend/tiles_{latest_map['id']}")
                    if tiles_path.exists() and any(tiles_path.iterdir()):
                        layers['rgb']['has_tiles'] = True
                        layers['rgb']['tiles_url'] = f"/tiles_{latest_map['id']}/{{z}}/{{x}}/{{y}}.png"
                
                latest_map_data = {
                    "id": latest_map['id'],
                    "layers": layers,
                    "info": json.loads(latest_map['info_json'])
                }
            except: pass
        
        cur.close()
        conn.close()
        
        return {
            "total_parcels": int(count_ortho + count_ms),
            "total_area": float(area_ortho + total_area_ms),
            "healthy_percent": round(float(healthy_percent), 1),
            "last_update": last_update.isoformat() if last_update else None,
            "latest_map": latest_map_data
        }
    except Exception as e:
        print(f"Error /api/stats: {e}")
        return {
            "total_parcels": 0,
            "total_area": 0,
            "healthy_percent": 0,
            "last_update": None,
            "latest_map": None
        }

# =====================================================
# UPLOAD ORTHOMOSAIC
# =====================================================

def generate_tiles_background(ortho_id: str, file_path: str):
    """
    Menjalankan gdal2tiles.py di background untuk mempercepat tampilan peta
    """
    output_dir = Path(f"frontend/tiles_{ortho_id}")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Tambahkan file README sebagai info
    with open(output_dir / "README.txt", "w") as f:
        f.write(f"Folder tiles untuk orthomosaic ID: {ortho_id}\n")
        f.write(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Perintah gdal2tiles (asumsi gdal2tiles.py ada di system)
    # Gunakan format Leaflet dan zoom 1-20
    cmd = [
        "gdal2tiles.py", 
        "--zoom=1-20", 
        "--webviewer=leaflet", 
        "--processes=4", 
        file_path, 
        str(output_dir)
    ]
    
    print(f"[*] Menjalankan gdal2tiles untuk {ortho_id}...")
    try:
        # Cari lokasi gdal2tiles.py
        gdal_path = shutil.which("gdal2tiles.py") or shutil.which("gdal2tiles")
        if not gdal_path:
            print("[!] gdal2tiles tidak ditemukan di system PATH")
            return

        result = subprocess.run([gdal_path] + cmd[1:], capture_output=True, text=True, timeout=1800)
        if result.returncode == 0:
            print(f"[+] Tiling selesai untuk {ortho_id}")
            # Update status di database
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE orthomosaic_history SET progress = 100, progress_message = 'Tiles ready' WHERE id = %s", (ortho_id,))
            conn.commit()
            cur.close()
            conn.close()
        else:
            print(f"[!] Tiling gagal: {result.stderr}")
    except Exception as e:
        print(f"[!] Error saat running tiling: {e}")

@app.post("/api/upload/orthomosaic")
async def upload_orthomosaic(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    nama_lahan: str = Form(None)
):
    print(f" Menerima upload: {file.filename}, nama_lahan={nama_lahan}")
    
    if not file.filename.endswith(('.tif', '.tiff', '.jpg', '.jpeg', '.png')):
        raise HTTPException(404, "Hanya file .tif, .jpg, .png yang diperbolehkan")
    
    ortho_id = str(uuid.uuid4())[:8]
    file_extension = file.filename.split('.')[-1]
    saved_path = ORTHO_UPLOAD_DIR / f"{ortho_id}_orthomosaic.{file_extension}"
    
    with open(saved_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Jalankan tiling di background jika file adalah TIFF
    if file.filename.endswith(('.tif', '.tiff')):
        background_tasks.add_task(generate_tiles_background, ortho_id, str(saved_path))
    
    coords = get_raster_center_and_bounds(str(saved_path))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orthomosaic_history (
            id VARCHAR(50) PRIMARY KEY,
            filename VARCHAR(255),
            nama_lahan VARCHAR(255),
            file_path TEXT,
            status VARCHAR(50) DEFAULT 'pending',
            uploaded_at TIMESTAMP DEFAULT NOW(),
            processed_at TIMESTAMP,
            progress INT DEFAULT 0,
            progress_message TEXT,
            has_analysis BOOLEAN DEFAULT FALSE,
            parcel_count INT DEFAULT 0,
            analysis_date TIMESTAMP,
            center_lon DOUBLE PRECISION,
            center_lat DOUBLE PRECISION,
            bounds TEXT,
            area_hectare DOUBLE PRECISION DEFAULT 0
        );
        -- Tambahkan kolom jika belum ada (untuk database yang sudah ada)
        DO $$ 
        BEGIN 
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                           WHERE table_name='orthomosaic_history' AND column_name='area_hectare') THEN
                ALTER TABLE orthomosaic_history ADD COLUMN area_hectare DOUBLE PRECISION DEFAULT 0;
            END IF;
        END $$;
    """)
    
    if coords:
        cur.execute("""
            INSERT INTO orthomosaic_history 
            (id, filename, nama_lahan, file_path, status, center_lon, center_lat, bounds, area_hectare)
            VALUES (%s, %s, %s, %s, 'completed', %s, %s, %s, %s)
        """, (ortho_id, file.filename, nama_lahan, str(saved_path), 
              coords['center_lon'], coords['center_lat'], coords['bounds'], coords['area_hectare']))
    else:
        cur.execute("""
            INSERT INTO orthomosaic_history (id, filename, nama_lahan, file_path, status)
            VALUES (%s, %s, %s, %s, 'completed')
        """, (ortho_id, file.filename, nama_lahan, str(saved_path)))
    
    conn.commit()
    cur.close()
    conn.close()
    
    tiles_folder = Path(f"frontend/tiles_{ortho_id}")
    tiles_folder.mkdir(parents=True, exist_ok=True)
    
    return {
        "success": True,
        "message": "Orthomosaic berhasil diupload",
        "ortho_id": ortho_id,
        "filename": file.filename,
        "nama_lahan": nama_lahan,
        "status": "completed",
        "tiles_folder": str(tiles_folder),
        "center_lon": coords['center_lon'] if coords else None,
        "center_lat": coords['center_lat'] if coords else None
    }

# =====================================================
# ENDPOINT ORTHOMOSAIC HISTORY & PETA
# =====================================================

@app.get("/api/orthomosaic/history")
async def get_orthomosaic_list():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("""
        SELECT 
            o.id, 
            o.filename, 
            o.nama_lahan, 
            o.status, 
            o.uploaded_at,
            o.center_lon,
            o.center_lat,
            o.area_hectare,
            COUNT(p.parcel_id) as parcel_count,
            COALESCE(SUM(p.area_hectare), 0) as total_area
        FROM orthomosaic_history o
        LEFT JOIN farm_parcels p ON o.id = p.ortho_id
        GROUP BY o.id, o.filename, o.nama_lahan, o.status, o.uploaded_at, o.center_lon, o.center_lat, o.area_hectare
        ORDER BY o.uploaded_at DESC
    """)
    
    history = cur.fetchall()
    cur.close()
    conn.close()
    
    return history

@app.get("/api/orthomosaic/{ortho_id}")
async def get_orthomosaic_detail(ortho_id: str):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("SELECT * FROM orthomosaic_history WHERE id = %s", (ortho_id,))
    ortho = cur.fetchone()
    cur.close()
    conn.close()
    
    if not ortho:
        raise HTTPException(404, "Orthomosaic tidak ditemukan")
    
    return ortho

@app.get("/api/orthomosaic/{ortho_id}/parcels")
async def get_parcels_by_ortho(ortho_id: str):
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM farm_parcels WHERE ortho_id = %s", (ortho_id,))
    count = cur.fetchone()[0]
    
    cur.execute("""
        SELECT 
            parcel_id,
            area_hectare,
            crop_type,
            ST_AsGeoJSON(geometry) as geojson
        FROM farm_parcels
        WHERE ortho_id = %s
    """, (ortho_id,))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    features = []
    for row in rows:
        if row[3]:
            features.append({
                "type": "Feature",
                "geometry": json.loads(row[3]),
                "properties": {
                    "parcel_id": row[0],
                    "area_hectare": float(row[1]) if row[1] else 0,
                    "crop_type": row[2] or "padi"
                }
            })
            
    return {"type": "FeatureCollection", "features": features}

@app.get("/api/orthomosaic/{ortho_id}/progress")
async def get_orthomosaic_progress(ortho_id: str):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("""
        SELECT status, progress, progress_message 
        FROM orthomosaic_history 
        WHERE id = %s
    """, (ortho_id,))
    
    result = cur.fetchone()
    cur.close()
    conn.close()
    
    if not result:
        raise HTTPException(404, "Orthomosaic tidak ditemukan")
        
    return {
        "ortho_id": ortho_id,
        "status": result['status'],
        "progress": result['progress'] or 0,
        "message": result['progress_message'] or ""
    }

# =====================================================
# UPLOAD TERPADU DAN ANALISIS MULTISPEKTRAL
# =====================================================

def get_ndvi_rec(val):
    if val > 0.8: return "0-25" # Sangat Sehat
    if val >= 0.6: return "25-50" # Sehat
    if val >= 0.4: return "50-75" # Sedang
    return "75-100" # Kurang Sehat

def get_ndre_rec(val):
    if val > 0.8: return "0-25" # Sangat Sehat
    if val >= 0.6: return "25-50" # Sehat
    if val >= 0.4: return "50-75" # Sedang
    return "75-100" # Kurang Sehat

def run_segmentation(image_path, output_path, gsd, src, ndvi_path=None, ndre_path=None):
    """Deteksi petak sawah dengan SAHI sliding window (2048x2048, overlap 0.3)."""

    from sahi.predict import get_sliced_prediction
    from pyproj import Transformer as ProjTransformer
    from shapely.geometry import Polygon as ShapelyPolygon
    import cv2

    # ── 1. Get cached SAHI model (avoids double-load segfault) ───────────────
    sahi_model = get_sahi_model()

    # ── 2. Run sliced prediction ─────────────────────────────────────────────
    prediction = get_sliced_prediction(
        image_path,
        sahi_model,
        slice_height=2048,
        slice_width=2048,
        overlap_height_ratio=0.3,
        overlap_width_ratio=0.3,
        perform_standard_pred=True,
        postprocess_type='NMM',
        postprocess_match_metric='IOS',
        postprocess_match_threshold=0.15,
        verbose=0,
    )

    # ── 3. Compute pixel area in m² using UTM projection ────────────────────
    bounds = src.bounds
    center_lon = (bounds.left + bounds.right) / 2
    center_lat = (bounds.top + bounds.bottom) / 2
    utm_zone = int((center_lon + 180) / 6) + 1
    hemi = 'north' if center_lat >= 0 else 'south'
    utm_crs = f'+proj=utm +zone={utm_zone} +{hemi} +ellps=WGS84 +datum=WGS84 +units=m +no_defs'
    t_utm = ProjTransformer.from_crs(str(src.crs), utm_crs, always_xy=True)
    x0, y0 = t_utm.transform(bounds.left, bounds.top)
    x1, y1 = t_utm.transform(bounds.right, bounds.top)
    x2, y2 = t_utm.transform(bounds.left, bounds.bottom)
    pixel_size_x = abs(x1 - x0) / src.width
    pixel_size_y = abs(y2 - y0) / src.height
    pixel_area_m2 = pixel_size_x * pixel_size_y

    print(f"[SAHI] pixel_area_m2={pixel_area_m2:.6f}, image {src.width}x{src.height}")

    # ── 4. Load NDVI / NDRE rasters ─────────────────────────────────────────
    ndvi_data = None
    if ndvi_path and os.path.exists(ndvi_path):
        with rasterio.open(ndvi_path) as n:
            ndvi_data = n.read(1)
    ndre_data = None
    if ndre_path and os.path.exists(ndre_path):
        with rasterio.open(ndre_path) as n:
            ndre_data = n.read(1)

    transformer = Transformer.from_crs(src.crs, "epsg:4326", always_xy=True)
    img_bgr = cv2.imread(image_path)
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB) if img_bgr is not None else None
    h_orig, w_orig = src.height, src.width

    IOU_MERGE_SEG = 0.15
    MIN_AREA_M2 = 200
    MAX_AREA_M2 = 60000

    # Accumulate candidates first for greedy NMS
    candidates = []  # dict: geometry(Shapely), area_m2, bool_mask, confidence

    for obj in prediction.object_prediction_list:
        if obj.mask is None:
            continue
        bool_mask = obj.mask.bool_mask  # numpy bool 2-D array
        area_px = int(bool_mask.sum())
        area_m2 = area_px * pixel_area_m2

        if area_m2 < MIN_AREA_M2 or area_m2 > MAX_AREA_M2:
            continue

        # Build geographic polygon from contour
        mask_uint8 = bool_mask.astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        pts = contour.reshape(-1, 2)
        if len(pts) < 3:
            continue

        polygon_latlon = []
        for px, py in pts:
            crs_x, crs_y = src.xy(float(py), float(px))
            lon, lat = transformer.transform(crs_x, crs_y)
            polygon_latlon.append([lat, lon])

        try:
            shapely_poly = ShapelyPolygon([(p[1], p[0]) for p in polygon_latlon])
            if not shapely_poly.is_valid:
                shapely_poly = shapely_poly.buffer(0)
        except Exception:
            continue

        candidates.append({
            'bool_mask': bool_mask,
            'shapely_poly': shapely_poly,
            'polygon_latlon': polygon_latlon,
            'area_m2': area_m2,
            'confidence': obj.score.value if hasattr(obj.score, 'value') else float(obj.score),
        })

    # ── 5. Greedy Shapely IoU NMS ────────────────────────────────────────────
    candidates.sort(key=lambda x: x['confidence'], reverse=True)
    kept_candidates = []
    for cand in candidates:
        is_dup = False
        for kept in kept_candidates:
            try:
                inter = cand['shapely_poly'].intersection(kept['shapely_poly']).area
                union = cand['shapely_poly'].union(kept['shapely_poly']).area
                if union > 0 and (inter / union) > IOU_MERGE_SEG:
                    is_dup = True
                    break
            except Exception:
                pass
        if not is_dup:
            kept_candidates.append(cand)

    print(f"[SAHI] After greedy NMS: {len(kept_candidates)} parcels (from {len(candidates)} candidates)")

    # ── 6. Build output mask overlay and petak_list ──────────────────────────
    mask_overlay = np.zeros((h_orig, w_orig, 4), dtype=np.uint8)
    color_fill = (255, 165, 0, 100)
    color_border = (139, 0, 0, 255)

    LCC_SCALES = {
        5: (np.array([56, 90, 40]), "0-25"),
        4: (np.array([70, 105, 41]), "25-50"),
        3: (np.array([87, 136, 54]), "50-75"),
        2: (np.array([112, 167, 50]), "75-100"),
    }

    total_petak = 0
    total_luas_m2 = 0.0
    petak_list = []

    for cand in kept_candidates:
        total_petak += 1
        area_m2 = cand['area_m2']
        area_ha = area_m2 / 10000
        total_luas_m2 += area_m2

        bool_mask = cand['bool_mask']
        polygon_latlon = cand['polygon_latlon']

        # Draw on overlay
        pts_cv = np.array([[p[1], p[0]] for p in polygon_latlon], dtype=np.float32)
        # Convert lat/lon back to pixel coords for drawing
        pts_px = []
        for lat, lon in polygon_latlon:
            row, col = src.index(
                *transformer.transform(lon, lat)[::-1]  # src.index(col_x, row_y) — note reversed
            )
            pts_px.append([col, row])
        pts_px = np.array(pts_px, dtype=np.int32)
        if len(pts_px) >= 3:
            cv2.fillPoly(mask_overlay, [pts_px], color_fill)
            cv2.polylines(mask_overlay, [pts_px], True, color_border, 2)

        # LCC from RGB pixels inside mask
        lcc_scale = 0
        rekomendasi_kg_ha = "0"
        if img_rgb is not None:
            try:
                # Ensure bool dtype — SAHI masks can come as object arrays
                bm = bool_mask.astype(np.bool_)
                # Crop/pad to match img_rgb dimensions
                rh, rw = img_rgb.shape[:2]
                bm_crop = bm[:rh, :rw]
                if bm_crop.shape != (rh, rw):
                    # mask smaller than image — pad with False
                    pad = np.zeros((rh, rw), dtype=np.bool_)
                    pad[:bm_crop.shape[0], :bm_crop.shape[1]] = bm_crop
                    bm_crop = pad
                pixels_rgb = img_rgb[bm_crop]
                if len(pixels_rgb) > 0:
                    dists = [np.linalg.norm(pixels_rgb - v[0], axis=1) for v in LCC_SCALES.values()]
                    min_idx = np.argmin(dists, axis=0)
                    scale_keys = list(LCC_SCALES.keys())
                    pixel_scales = [scale_keys[i] for i in min_idx]
                    values, counts = np.unique(pixel_scales, return_counts=True)
                    lcc_scale = int(values[np.argmax(counts)])
                    rekomendasi_kg_ha = LCC_SCALES[lcc_scale][1]
            except Exception:
                pass

        # NDVI / NDRE zonal mean
        mean_ndvi = 0.0
        if ndvi_data is not None:
            try:
                bm_ndvi = bool_mask.astype(np.bool_)[:ndvi_data.shape[0], :ndvi_data.shape[1]]
                mean_ndvi = float(np.nanmean(ndvi_data[bm_ndvi]))
            except Exception:
                pass
        mean_ndre = 0.0
        if ndre_data is not None:
            try:
                bm_ndre = bool_mask.astype(np.bool_)[:ndre_data.shape[0], :ndre_data.shape[1]]
                mean_ndre = float(np.nanmean(ndre_data[bm_ndre]))
            except Exception:
                pass

        petak_list.append({
            "id": f"petak_{total_petak}",
            "nama": f"Petak {total_petak}",
            "luas_m2": round(area_m2, 2),
            "luas_ha": round(area_ha, 4),
            "area_are": round(area_m2 / 100, 2),
            "lcc_scale": lcc_scale,
            "lcc_rec": rekomendasi_kg_ha,
            "mean_ndvi": round(mean_ndvi, 4),
            "mean_ndre": round(mean_ndre, 4),
            "ndvi_rec": get_ndvi_rec(mean_ndvi),
            "ndre_rec": get_ndre_rec(mean_ndre),
            "polygon": polygon_latlon,
        })

    # ── 7. Write mask overlay ────────────────────────────────────────────────
    cv2.imwrite(output_path, mask_overlay)
    print(f"[SAHI] Done: {total_petak} parcels detected")
    return total_petak, round(total_luas_m2, 2), total_petak > 0, petak_list





def process_single_tif(file_obj, layer_type, upload_id):
    os.makedirs(MULTISPECTRAL_DIR, exist_ok=True)
    filepath = MULTISPECTRAL_DIR / f"{upload_id}_{layer_type}.tif"
    
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file_obj.file, buffer)
        
    with rasterio.open(filepath) as src:
        gsd = src.transform[0] 
        bounds = src.bounds
        
        transformer = Transformer.from_crs(src.crs, "epsg:4326", always_xy=True)
        min_lon, min_lat = transformer.transform(bounds.left, bounds.bottom)
        max_lon, max_lat = transformer.transform(bounds.right, bounds.top)
        
        output_png = f"{upload_id}_{layer_type}.png"
        
        # Simpan ke dalam folder backend/static/uploads
        static_dir = Path("backend/static/uploads")
        static_dir.mkdir(parents=True, exist_ok=True)
        output_path = static_dir / output_png
        
        if layer_type == 'rgb':
            r, g, b = src.read(1), src.read(2), src.read(3)
            mask = ((r > 0) | (g > 0) | (b > 0)).astype('uint8') * 255
            
            def normalize(band):
                # Sample data for faster percentile calculation (1 in 25 pixels)
                sample = band[::5, ::5]
                b_min, b_max = np.percentile(sample, (2, 98))
                return np.clip((band - b_min) / (b_max - b_min + 1e-5) * 255, 0, 255).astype('uint8')
            
            rgba = np.dstack((normalize(r), normalize(g), normalize(b), mask))
            cv2.imwrite(str(output_path), rgba[..., [2, 1, 0, 3]])
        elif layer_type in ['ndvi', 'ndre']:
            band = src.read(1).astype(float)
            mask_cond = ~np.isnan(band)
            if src.nodata is not None:
                mask_cond &= (band != src.nodata)
            else:
                mask_cond &= (band != 0)
            
            mask = mask_cond.astype('uint8') * 255
            
            colored = np.zeros((*band.shape, 3), dtype=np.uint8)
            colored[(band < 0)] = [215, 48, 39]
            colored[(band >= 0) & (band < 0.2)] = [253, 174, 97]
            colored[(band >= 0.2) & (band < 0.4)] = [254, 224, 139]
            colored[(band >= 0.4) & (band < 0.6)] = [217, 239, 139]
            colored[(band >= 0.6) & (band < 0.8)] = [102, 189, 99]
            colored[(band >= 0.8)] = [26, 152, 80]
            
            rgba = np.dstack((colored[..., 0], colored[..., 1], colored[..., 2], mask))
            cv2.imwrite(str(output_path), rgba[..., [2, 1, 0, 3]])
        return {
            "url": f"/static/uploads/{output_png}?t=" + secrets.token_hex(4),
            "bounds": [[min_lat, min_lon], [max_lat, max_lon]],
            "gsd": gsd
        }


async def process_multispectral_logic(upload_id: str, nama_lahan: Optional[str], files_map: dict):
    layers_data = {}
    
    # Proses band secara paralel
    async def run_process(filepath, key):
        if filepath and os.path.exists(filepath):
            # Create a dummy file object for process_single_tif if needed, 
            # or refactor process_single_tif to handle paths.
            # Let's refactor process_single_tif slightly.
            return key, await asyncio.to_thread(process_single_tif_from_path, filepath, key, upload_id)
        return key, None

    process_tasks = []
    for key, path in files_map.items():
        process_tasks.append(run_process(path, key))
    
    if not process_tasks:
        return {"status": "error", "message": "Tidak ada data untuk diproses"}

    results = await asyncio.gather(*process_tasks)
    for key, result in results:
        if result:
            layers_data[key] = result
            
    info_deteksi = {
        "status_deteksi": "gagal",
        "pesan": "Tidak ada data RGB untuk diproses.",
        "total_petak": 0,
        "total_luas": 0
    }

    if 'rgb' in layers_data:
        rgb_path = f"backend/static/uploads/{upload_id}_rgb.png"
        seg_path = f"backend/static/uploads/{upload_id}_petak_sawah.png"
        tif_path = MULTISPECTRAL_DIR / f"{upload_id}_rgb.tif"
        
        ndvi_tif = MULTISPECTRAL_DIR / f"{upload_id}_ndvi.tif" if 'ndvi' in layers_data else None
        ndre_tif = MULTISPECTRAL_DIR / f"{upload_id}_ndre.tif" if 'ndre' in layers_data else None

        with rasterio.open(tif_path) as src:
            count, total_area, sukses, petak_list = run_segmentation(
                rgb_path, 
                seg_path, 
                layers_data['rgb']['gsd'],
                src,
                ndvi_path=str(ndvi_tif) if ndvi_tif else None,
                ndre_path=str(ndre_tif) if ndre_tif else None
            )
        
        layers_data['segmentasi'] = {
            "url": f"/static/uploads/{upload_id}_petak_sawah.png?t=" + secrets.token_hex(4),
            "bounds": layers_data['rgb']['bounds']
        }

        info_deteksi = {
            "status_deteksi": "berhasil" if sukses else "gagal",
            "pesan": "Petak sawah berhasil dideteksi." if sukses else "Model tidak menemukan petak sawah pada gambar ini.",
            "total_petak": count,
            "total_luas": total_area,
            "petak_list": petak_list
        }
        
    return {
        "status": "success", 
        "upload_id": upload_id,
        "layers": layers_data, 
        "info": info_deteksi,
        "message": "Data berhasil di-upload dan diproses"
    }

def process_single_tif_from_path(filepath, layer_type, upload_id):
    # This is a version of process_single_tif that takes a path instead of UploadFile
    with rasterio.open(filepath) as src:
        gsd = src.transform[0] 
        bounds = src.bounds
        
        transformer = Transformer.from_crs(src.crs, "epsg:4326", always_xy=True)
        min_lon, min_lat = transformer.transform(bounds.left, bounds.bottom)
        max_lon, max_lat = transformer.transform(bounds.right, bounds.top)
        
        output_png = f"{upload_id}_{layer_type}.png"
        static_dir = Path("backend/static/uploads")
        static_dir.mkdir(parents=True, exist_ok=True)
        output_path = static_dir / output_png
        
        def normalize(band):
            sample = band[::5, ::5]
            b_min, b_max = np.percentile(sample, (2, 98))
            return np.clip((band - b_min) / (b_max - b_min + 1e-5) * 255, 0, 255).astype('uint8')

        if layer_type == 'rgb':
            r, g, b = src.read(1), src.read(2), src.read(3)
            mask = ((r > 0) | (g > 0) | (b > 0)).astype('uint8') * 255
            rgba = np.dstack((normalize(r), normalize(g), normalize(b), mask))
            cv2.imwrite(str(output_path), rgba[..., [2, 1, 0, 3]])
        elif layer_type in ['ndvi', 'ndre']:
            band = src.read(1).astype(float)
            mask_cond = ~np.isnan(band)
            if src.nodata is not None:
                mask_cond &= (band != src.nodata)
            else:
                mask_cond &= (band != 0)
            mask = mask_cond.astype('uint8') * 255
            colored = np.zeros((*band.shape, 3), dtype=np.uint8)
            colored[(band < 0)] = [215, 48, 39]
            colored[(band >= 0) & (band < 0.2)] = [253, 174, 97]
            colored[(band >= 0.2) & (band < 0.4)] = [254, 224, 139]
            colored[(band >= 0.4) & (band < 0.6)] = [217, 239, 139]
            colored[(band >= 0.6) & (band < 0.8)] = [102, 189, 99]
            colored[(band >= 0.8)] = [26, 152, 80]
            rgba = np.dstack((colored[..., 0], colored[..., 1], colored[..., 2], mask))
            cv2.imwrite(str(output_path), rgba[..., [2, 1, 0, 3]])

        return {
            "url": f"/static/uploads/{output_png}?t=" + secrets.token_hex(4),
            "bounds": [[min_lat, min_lon], [max_lat, max_lon]],
            "gsd": gsd
        }

@app.post("/api/upload/multispektral")
async def upload_multispectral(
    nama_lahan: Optional[str] = Form(None),
    rgb: UploadFile = File(None),
    ndvi: UploadFile = File(None),
    ndre: UploadFile = File(None)
):
    upload_id = str(uuid.uuid4())[:8]
    files_map = {}
    
    # Simpan file ke disk dulu
    os.makedirs(MULTISPECTRAL_DIR, exist_ok=True)
    for file_obj, key in [(rgb, "rgb"), (ndvi, "ndvi"), (ndre, "ndre")]:
        if file_obj and file_obj.filename != '':
            filepath = MULTISPECTRAL_DIR / f"{upload_id}_{key}.tif"
            with open(filepath, "wb") as buffer:
                shutil.copyfileobj(file_obj.file, buffer)
            files_map[key] = str(filepath)
            
    result = await process_multispectral_logic(upload_id, nama_lahan, files_map)
    return JSONResponse(content=result)

@app.post("/api/upload/chunk")
async def upload_chunk(
    upload_id: str = Form(...),
    layer_type: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    file: UploadFile = File(...)
):
    temp_dir = Path(f"backend/temp_uploads/{upload_id}/{layer_type}")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    chunk_path = temp_dir / f"chunk_{chunk_index}"
    with open(chunk_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return {"success": True}

@app.post("/api/upload/finalize-chunks")
async def finalize_chunks(
    upload_id: str = Form(...),
    nama_lahan: str = Form(None),
    layers: str = Form(...) # JSON string of available layers
):
    layers_list = json.loads(layers)
    files_map = {}
    
    os.makedirs(MULTISPECTRAL_DIR, exist_ok=True)
    
    for layer in layers_list:
        temp_dir = Path(f"backend/temp_uploads/{upload_id}/{layer}")
        if not temp_dir.exists():
            continue
            
        final_path = MULTISPECTRAL_DIR / f"{upload_id}_{layer}.tif"
        # Join chunks in order
        chunks = sorted(temp_dir.glob("chunk_*"), key=lambda x: int(x.name.split('_')[1]))
        
        with open(final_path, "wb") as outfile:
            for chunk in chunks:
                with open(chunk, "rb") as infile:
                    outfile.write(infile.read())
        
        files_map[layer] = str(final_path)
        # Clean up chunks
        shutil.rmtree(temp_dir)
        
    result = await process_multispectral_logic(upload_id, nama_lahan, files_map)
    
    # Clean up temp upload folder
    try:
        shutil.rmtree(Path(f"backend/temp_uploads/{upload_id}"))
    except: pass
    
    return JSONResponse(content=result)

# =====================================================
# ENDPOINT HISTORY & DELETE
# =====================================================

@app.post("/api/multispektral/save")
async def save_multispectral(
    upload_id: str = Form(...),
    nama_lahan: str = Form(...),
    layers_json: str = Form(...),
    info_json: str = Form(...)
):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO multispectral_uploads (id, nama_lahan, layers_json, info_json)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                nama_lahan = EXCLUDED.nama_lahan,
                layers_json = EXCLUDED.layers_json,
                info_json = EXCLUDED.info_json
        """, (upload_id, nama_lahan, layers_json, info_json))
        conn.commit()
        cur.close()
        conn.close()
        return {"success": True, "message": "Data berhasil disimpan"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/multispektral/uploads")
async def get_multispectral_uploads():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Ambil dari multispectral_uploads
    cur.execute("SELECT id, nama_lahan, layers_json, info_json, created_at, last_opened_at FROM multispectral_uploads ORDER BY created_at DESC")
    rows_ms = cur.fetchall()
    
    # Ambil dari orthomosaic_history (untuk RGB layer yang sudah ditile)
    cur.execute("SELECT id, filename, nama_lahan, uploaded_at as created_at, center_lon, center_lat, bounds, area_hectare FROM orthomosaic_history WHERE status = 'completed' ORDER BY uploaded_at DESC")
    rows_ortho = cur.fetchall()
    
    cur.close()
    conn.close()
    
    results = []
    
    # Proses data multispektral
    for row in rows_ms:
        results.append({
            "id": row["id"],
            "nama_lahan": row["nama_lahan"],
            "type": "multispectral",
            "layers": json.loads(row["layers_json"]) if row["layers_json"] else {},
            "info": json.loads(row["info_json"]) if row["info_json"] else {},
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "last_opened_at": row["last_opened_at"].isoformat() if row["last_opened_at"] else None
        })
        
    # Proses data orthomosaic (RGB Only)
    for row in rows_ortho:
        # Cek apakah sudah ada di results (mungkin ada duplikasi ID?)
        if any(r["id"] == row["id"] for r in results):
            continue
            
        # Cek apakah folder tiles ada
        tiles_path = Path(f"frontend/tiles_{row['id']}")
        has_tiles = tiles_path.is_dir() and any(tiles_path.iterdir())
        
        # Format bounds jika dalam string
        bounds = row["bounds"]
        if isinstance(bounds, str):
            try:
                bounds = json.loads(bounds.replace("'", '"'))
            except:
                pass
        
        results.append({
            "id": row["id"],
            "nama_lahan": row["nama_lahan"] or row["filename"],
            "type": "orthomosaic",
            "layers": {
                "rgb": {
                    "url": f"/backend/uploads/orthomosaic/{row['id']}_orthomosaic.tif", # Placeholder, actually the PNG is better if tiled
                    "bounds": bounds,
                    "has_tiles": has_tiles,
                    "tiles_url": f"/tiles_{row['id']}/{{z}}/{{x}}/{{y}}.png" if has_tiles else None
                }
            },
            "info": {
                "total_petak": 0, # Akan diisi jika ada analisis
                "total_luas": (row["area_hectare"] or 0) * 10000
            },
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "last_opened_at": None
        })
        
    return results

@app.get("/api/multispektral/uploads/{upload_id}")
async def get_multispectral_upload_detail(upload_id: str):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Cek di multispectral_uploads
    cur.execute("UPDATE multispectral_uploads SET last_opened_at = NOW() WHERE id = %s RETURNING id, nama_lahan, layers_json, info_json, created_at, last_opened_at", (upload_id,))
    row = cur.fetchone()
    
    if row:
        conn.commit()
        cur.close()
        conn.close()
        return {
            "id": row["id"],
            "nama_lahan": row["nama_lahan"],
            "type": "multispectral",
            "layers": json.loads(row["layers_json"]) if row["layers_json"] else {},
            "info": json.loads(row["info_json"]) if row["info_json"] else {},
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "last_opened_at": row["last_opened_at"].isoformat() if row["last_opened_at"] else None
        }
        
    # Jika tidak ada, cek di orthomosaic_history
    cur.execute("SELECT id, filename, nama_lahan, uploaded_at as created_at, bounds, area_hectare FROM orthomosaic_history WHERE id = %s", (upload_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if not row:
        raise HTTPException(404, "Data tidak ditemukan")
        
    # Cek apakah folder tiles ada
    tiles_path = Path(f"frontend/tiles_{row['id']}")
    has_tiles = tiles_path.is_dir() and any(tiles_path.iterdir())
    
    bounds = row["bounds"]
    if isinstance(bounds, str):
        try:
            bounds = json.loads(bounds.replace("'", '"'))
        except:
            pass
            
    return {
        "id": row["id"],
        "nama_lahan": row["nama_lahan"] or row["filename"],
        "type": "orthomosaic",
        "layers": {
            "rgb": {
                "url": f"/backend/uploads/orthomosaic/{row['id']}_orthomosaic.tif",
                "bounds": bounds,
                "has_tiles": has_tiles,
                "tiles_url": f"/tiles_{row['id']}/{{z}}/{{x}}/{{y}}.png" if has_tiles else None
            }
        },
        "info": {
            "total_petak": 0,
            "total_luas": (row["area_hectare"] or 0) * 10000
        },
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "last_opened_at": None
    }

@app.delete("/api/multispektral/uploads/{upload_id}")
async def delete_multispectral_upload(upload_id: str):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM multispectral_uploads WHERE id = %s", (upload_id,))
        conn.commit()
        cur.close()
        conn.close()
        return {"success": True, "message": "Data berhasil dihapus"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/multispektral/{upload_id}/vegetation-data")
async def get_vegetation_data(upload_id: str):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("""
        SELECT parcel_id, ndvi_value, ndre_value, gndvi_value
        FROM parcel_vegetation_indices
        WHERE upload_id = %s
    """, (upload_id,))
    
    results = cur.fetchall()
    cur.close()
    conn.close()
    
    return results

@app.get("/api/multispektral/history")
async def get_multispektral_history():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("""
        SELECT upload_id, nama_lahan, has_red, has_green, has_nir, has_rededge, has_rgb,
               status, created_at
        FROM multispectral_data
        ORDER BY created_at DESC
    """)
    
    history = cur.fetchall()
    cur.close()
    conn.close()
    
    return history

@app.post("/api/orthomosaic/{ortho_id}/activate")
async def activate_orthomosaic(ortho_id: str):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Set status active untuk satu, completed untuk yang lain
        cur.execute("UPDATE orthomosaic_history SET status = 'completed' WHERE status = 'active'")
        cur.execute("UPDATE orthomosaic_history SET status = 'active' WHERE id = %s", (ortho_id,))
        conn.commit()
        cur.close()
        conn.close()
        return {"success": True, "message": "Orthomosaic diaktifkan"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/orthomosaic/{ortho_id}/generate-tiles")
async def trigger_tiling(ortho_id: str, background_tasks: BackgroundTasks):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT file_path FROM orthomosaic_history WHERE id = %s", (ortho_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if not row:
        raise HTTPException(404, "Data tidak ditemukan")
        
    background_tasks.add_task(generate_tiles_background, ortho_id, row['file_path'])
    return {"success": True, "message": "Tiling process started in background"}

@app.delete("/api/orthomosaic/{ortho_id}")
async def delete_orthomosaic(ortho_id: str):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Ambil file path untuk dihapus juga
        cur.execute("SELECT file_path FROM orthomosaic_history WHERE id = %s", (ortho_id,))
        row = cur.fetchone()
        if row and os.path.exists(row[0]):
            os.remove(row[0])
            
        cur.execute("DELETE FROM orthomosaic_history WHERE id = %s", (ortho_id,))
        cur.execute("DELETE FROM farm_parcels WHERE ortho_id = %s", (ortho_id,))
        conn.commit()
        cur.close()
        conn.close()
        
        # Hapus folder tiles
        await delete_tiles_folder(ortho_id)
        
        return {"success": True, "message": "Orthomosaic berhasil dihapus"}
    except Exception as e:
        return {"success": False, "message": str(e)}
        cur.execute("DELETE FROM farm_parcels WHERE ortho_id = %s", (ortho_id,))
        cur.execute("DELETE FROM orthomosaic_history WHERE id = %s", (ortho_id,))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return {"success": True, "message": "Orthomosaic berhasil dihapus"}
    except Exception as e:
        raise HTTPException(500, f"Error: {str(e)}")

@app.delete("/api/orthomosaic/{ortho_id}/delete-tiles")
async def delete_tiles_folder(ortho_id: str):
    try:
        tiles_folder = Path(f"frontend/tiles_{ortho_id}")
        if tiles_folder.exists():
            shutil.rmtree(tiles_folder)
            return {"success": True, "message": "Folder tiles berhasil dihapus"}
        else:
            return {"success": True, "message": "Folder tiles tidak ditemukan"}
    except Exception as e:
        return {"success": False, "message": str(e)}

# =====================================================
# ENDPOINT ANALISIS PETAK SAWAH
# =====================================================

@app.post("/api/orthomosaic/{ortho_id}/analyze")
async def analyze_parcels(ortho_id: str, background_tasks: BackgroundTasks):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM orthomosaic_history WHERE id = %s", (ortho_id,))
    ortho = cur.fetchone()
    cur.close()
    conn.close()
    
    if not ortho:
        raise HTTPException(404, "Orthomosaic tidak ditemukan")
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orthomosaic_history SET status = 'analyzing' WHERE id = %s", (ortho_id,))
    conn.commit()
    cur.close()
    conn.close()
    
    background_tasks.add_task(run_yolo_analysis, ortho_id, ortho['file_path'])
    return {"message": "Analisis dimulai", "ortho_id": ortho_id}

def run_yolo_analysis(ortho_id: str, ortho_path: str):
    print(f" Memulai analisis untuk {ortho_id}")
    script_path = Path("backend/scripts/detect_parcels.py")
    if not script_path.exists():
        print(f" Script tidak ditemukan: {script_path}")
        return
        
    model_path = "/home/labtefa/webgis_agriculture/backend/models/best.pt"
    cmd = [
        sys.executable,
        str(script_path),
        "--ortho", ortho_path,
        "--ortho_id", ortho_id,
        "--weights", model_path
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM farm_parcels WHERE ortho_id = %s", (ortho_id,))
            parcel_count = cur.fetchone()[0]
            
            cur.execute("""
                UPDATE orthomosaic_history 
                SET has_analysis = TRUE, 
                    parcel_count = %s,
                    status = 'completed'
                WHERE id = %s
            """, (parcel_count, ortho_id))
            conn.commit()
            cur.close()
            conn.close()
            print(f" Analisis selesai! {parcel_count} petak ditemukan")
    except Exception as e:
        print(f" Error: {e}")

# =====================================================
# ENDPOINT MULTISPECTRAL UNTUK PETA
# =====================================================

@app.get("/api/multispektral/{upload_id}/parcels/{layer_type}")
async def get_parcels_with_indices(upload_id: str, layer_type: str):
    conn = get_db_connection()
    cur = conn.cursor()
    
    index_column = {
        'ndvi': 'ndvi_value',
        'ndre': 'ndre_value',
        'gndvi': 'gndvi_value',
        'osavi': 'osavi_value'
    }.get(layer_type)
    
    if index_column:
        query = f"""
            SELECT 
                p.parcel_id,
                p.area_hectare,
                ST_AsGeoJSON(p.geometry) as geojson,
                v.{index_column} as index_value
            FROM farm_parcels p
            LEFT JOIN parcel_vegetation_indices v 
                ON p.parcel_id = v.parcel_id AND v.upload_id = %s
            WHERE p.ortho_id = %s
        """
        cur.execute(query, (upload_id, upload_id))
    else:
        query = """
            SELECT 
                parcel_id,
                area_hectare,
                ST_AsGeoJSON(geometry) as geojson,
                NULL as index_value
            FROM farm_parcels
            WHERE ortho_id = %s
        """
        cur.execute(query, (upload_id,))
        
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    def get_color_by_index(value, layer_type):
        if value is None:
            return '#888888'
        if layer_type in ['ndvi', 'ndre', 'gndvi']:
            if value > 0.6:
                return '#1a9850'
            elif value > 0.3:
                return '#d9ef8b'
            elif value > 0:
                return '#fdae61'
            else:
                return '#d73027'
        elif layer_type == 'osavi':
            if value > 0.4:
                return '#1a9850'
            elif value > 0.2:
                return '#d9ef8b'
            else:
                return '#fdae61'
        else:
            return '#ff6600'

    features = []
    for row in rows:
        index_value = row[3] if len(row) > 3 else None
        color = get_color_by_index(index_value, layer_type)
        
        features.append({
            "type": "Feature",
            "geometry": json.loads(row[2]),
            "properties": {
                "parcel_id": row[0],
                "area_hectare": float(row[1]) if row[1] else 0,
                "index_value": float(index_value) if index_value is not None else None,
                "layer_type": layer_type,
                "color": color
            }
        })
        
    return {
        "type": "FeatureCollection",
        "features": features,
        "layer_type": layer_type
    }

@app.post("/api/multispektral/{upload_id}/generate-tiles")
async def generate_multispektral_tiles(upload_id: str):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("""
        SELECT ndvi_path, ndre_path, gndvi_path, osavi_path
        FROM multispectral_data
        WHERE id = %s
    """, (upload_id,))
    
    data = cur.fetchone()
    cur.close()
    conn.close()
    
    if not data:
        raise HTTPException(404, "Data tidak ditemukan")
        
    results = {}
    
    for index_name, file_path in data.items():
        if file_path and os.path.exists(file_path):
            tiles_dir = Path(f"frontend/tiles_{upload_id}_{index_name}")
            tiles_dir.mkdir(parents=True, exist_ok=True)
            results[index_name] = str(tiles_dir)
            
    return {"message": "Tiles generation completed", "paths": results}

@app.post("/api/multispektral/petak/rename")
async def rename_petak(
    upload_id: str = Form(...),
    petak_id: str = Form(...),
    nama_baru: str = Form(...)
):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT info_json FROM multispectral_uploads WHERE id = %s", (upload_id,))
        row = cur.fetchone()
        if row and row['info_json']:
            info = json.loads(row['info_json'])
            if 'petak_list' in info:
                for p in info['petak_list']:
                    if p['id'] == petak_id:
                        p['nama'] = nama_baru
                        break
                
                cur.execute("UPDATE multispectral_uploads SET info_json = %s WHERE id = %s", (json.dumps(info), upload_id))
                conn.commit()
                return {"success": True, "message": "Nama berhasil diubah"}
                
        return {"success": False, "message": "Petak tidak ditemukan"}
    except Exception as e:
        conn.rollback()
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        conn.close()

@app.post("/api/multispektral/petak/delete")
async def delete_petak_from_json(
    upload_id: str = Form(...),
    petak_id: str = Form(...)
):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT info_json FROM multispectral_uploads WHERE id = %s", (upload_id,))
        row = cur.fetchone()
        if row and row['info_json']:
            info = json.loads(row['info_json'])
            if 'petak_list' in info:
                # Cari petak untuk dihapus
                new_list = [p for p in info['petak_list'] if p['id'] != petak_id]
                info['petak_list'] = new_list
                info['total_petak'] = len(new_list)
                info['total_luas'] = sum(p.get('luas_m2', 0) for p in new_list)
                
                cur.execute("UPDATE multispectral_uploads SET info_json = %s WHERE id = %s", (json.dumps(info), upload_id))
                conn.commit()
                return {"success": True, "message": "Petak berhasil dihapus"}
                
        return {"success": False, "message": "Petak tidak ditemukan"}
    except Exception as e:
        conn.rollback()
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        conn.close()

@app.get("/api/parcels")
async def get_all_parcels():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT id, nama_lahan, info_json FROM multispectral_uploads ORDER BY created_at DESC")
        rows = cur.fetchall()
        
        all_parcels = []
        for row in rows:
            if row['info_json']:
                info = json.loads(row['info_json'])
                if 'petak_list' in info:
                    for p in info['petak_list']:
                        # Tambahkan metadata dari parent upload
                        p['upload_id'] = row['id']
                        p['nama_lahan'] = row['nama_lahan']
                        all_parcels.append(p)
        return all_parcels
    except Exception as e:
        print(f"Error /api/parcels: {e}")
        return []
    finally:
        cur.close()
        conn.close()

@app.get("/api/database/stats")
async def get_db_stats():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT COUNT(*) FROM farm_parcels")
        farm_parcels_count = cur.fetchone()['count']
        
        cur.execute("SELECT info_json FROM multispectral_uploads")
        multi_rows = cur.fetchall()
        multi_parcels_count = 0
        for r in multi_rows:
            if r['info_json']:
                info = json.loads(r['info_json'])
                multi_parcels_count += len(info.get('petak_list', []))
        
        cur.execute("SELECT COUNT(*) FROM multispectral_uploads")
        uploads_count = cur.fetchone()['count']
        
        return {
            "farm_parcels": farm_parcels_count + multi_parcels_count,
            "ndvi_data": multi_parcels_count,
            "recommendations": multi_parcels_count,
            "uploads": uploads_count
        }
    except Exception as e:
        print(f"Error /api/database/stats: {e}")
        return {"farm_parcels": 0, "ndvi_data": 0, "recommendations": 0, "uploads": 0}
    finally:
        cur.close()
        conn.close()

# =====================================================
# SERVE FRONTEND
# =====================================================
app.mount("/backend/uploads", StaticFiles(directory="backend/uploads"), name="uploads")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
