import os
from pathlib import Path

upload_file = Path('frontend/upload.html')
content = upload_file.read_text()

fixes = {
    "parcelsLayerGroup": "petakLayerGroup",
    "total_parcels": "total_petak",
    "parcels_list": "petak_list",
    "parcels.polygon": "petak.polygon",
    "parcels.lcc_rec": "petak.lcc_rec",
    "parcels.ndvi_rec": "petak.ndvi_rec",
    "parcels.ndre_rec": "petak.ndre_rec",
    "parcels.luas_ha": "petak.luas_ha",
    "parcels.lcc_scale": "petak.lcc_scale",
    "parcels.id": "petak.id",
    "parcels.nama": "petak.nama",
    "parcels.luas_m2": "petak.luas_m2",
    "renameMapkLocal": "renamePetakLocal",
    "parcelsId": "petakId",
    "function(parcels)": "function(petak)",
    "if(parcels)": "if(petak)",
    "parcels.nama =": "petak.nama =",
    "var parcels =": "var petak =",
    "p => p.id === petakId": "p => p.id === petakId" # just in case
}

for k, v in fixes.items():
    content = content.replace(k, v)

upload_file.write_text(content)
