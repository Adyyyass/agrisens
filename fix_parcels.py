import os
from pathlib import Path

peta_file = Path('frontend/peta_multispektral.html')
content = peta_file.read_text()

fixes = {
    "parcelsLayerGroup": "petakLayerGroup",
    "parcels.lcc_rec": "petak.lcc_rec",
    "parcels.ndvi_rec": "petak.ndvi_rec",
    "parcels.ndre_rec": "petak.ndre_rec",
    "parcels.luas_ha": "petak.luas_ha",
    "parcels.nama": "petak.nama",
    "parcelsId": "petakId",
    "parcels_id": "petak_id",
    "Nama parcels berhasil diubah!": "Parcel name successfully changed!",
    "Hapus parcels ini secara permanen dari database?": "Delete this parcel permanently from the database?",
    "Cari parcels di layer group": "Find parcel in layer group",
    "Gagal menghapus parcels:": "Failed to delete parcel:",
    "Data deteksi parcels tidak tersedia untuk upload ini.": "Parcel detection data is not available for this upload."
}

for k, v in fixes.items():
    content = content.replace(k, v)
    
peta_file.write_text(content)
