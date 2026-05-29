import os
import re

peta_file = 'frontend/peta_multispektral.html'
upload_file = 'frontend/upload.html'

def fix_peta():
    with open(peta_file, 'r') as f:
        content = f.read()

    # 1. Add var currentUploadData = null;
    if 'var currentUploadData = null;' not in content:
        content = content.replace("var currentLayerKey = null;", "var currentLayerKey = null;\n            var currentUploadData = null;")

    # 5. Add escapeHtml
    escape_func = """
            function escapeHtml(str) { 
                return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); 
            }
"""
    if 'function escapeHtml' not in content:
        content = content.replace("function loadSavedUploads() {", escape_func + "\n            function loadSavedUploads() {")

    # 5. Use escapeHtml in popup
    content = content.replace('id="input_nama_${petak.id}"', 'id="input_nama_${escapeHtml(petak.id)}"')
    content = content.replace('value="${petak.nama}"', 'value="${escapeHtml(petak.nama)}"')
    content = content.replace("renamePetak('${currentUploadData.id}', '${petak.id}')", "renamePetak('${currentUploadData.id}', '${escapeHtml(petak.id)}')")
    content = content.replace("deletePetak('${currentUploadData.id}', '${petak.id}')", "deletePetak('${currentUploadData.id}', '${escapeHtml(petak.id)}')")

    # 2. Move focusOnPetak and remove setTimeout
    if 'setTimeout(function()' in content:
        # Remove the setTimeout block
        content = re.sub(r'// Cek jika ada petak_id di URL setelah load data\s*setTimeout\(function\(\) \{\s*var urlParams = new URLSearchParams\(window\.location\.search\);\s*var petakId = urlParams\.get\(\'petak_id\'\);\s*if \(petakId\) \{\s*focusOnPetak\(petakId\);\s*\}\s*\}, 2000\); // Tunggu load data selesai', '', content)
        
        # Insert into loadUploadData
        insertion = """
                        var urlParams = new URLSearchParams(window.location.search);
                        var petakId = urlParams.get('petak_id');
                        if (petakId) {
                            focusOnPetak(petakId);
                        }
"""
        content = content.replace('document.querySelectorAll(".sidebar-item").forEach(function (el) {', insertion + '\n                        document.querySelectorAll(".sidebar-item").forEach(function (el) {')

    # 3. Fix toggleSegmentation double call
    if 'function renderParcels()' not in content:
        render_logic = """
            function renderParcels() {
                if (!currentUploadData || !currentUploadData.info || !currentUploadData.info.petak_list) return;
                petakLayerGroup.clearLayers();
                currentUploadData.info.petak_list.forEach(function(petak) {
                    var poly = L.polygon(petak.polygon, {
                        color: '#16a34a',
                        weight: 2,
                        fillOpacity: 0.15,
                        dashArray: '5, 5'
                    });
                    
                    var rec = petak.lcc_rec;
                    var recType = "LCC";
                    if(currentLayerKey === 'ndvi') {
                        rec = petak.ndvi_rec;
                        recType = "NDVI";
                    } else if(currentLayerKey === 'ndre') {
                        rec = petak.ndre_rec;
                        recType = "NDRE";
                    }

                    var rangeArr = rec.split('-').map(Number);
                    var pupukTotal = "";
                    if(rangeArr.length === 2) {
                        var sorted = [...rangeArr].sort((a,b)=>a-b);
                        var low = (sorted[0] * petak.luas_ha).toFixed(2);
                        var high = (sorted[1] * petak.luas_ha).toFixed(2);
                        pupukTotal = low + " - " + high + " kg N";
                    }
                    
                    var lccColor = "";
                    if(petak.lcc_scale == 5) lccColor = "#385a28";
                    if(petak.lcc_scale == 4) lccColor = "#466929";
                    if(petak.lcc_scale == 3) lccColor = "#578836";
                    if(petak.lcc_scale == 2) lccColor = "#70a732";
                    
                    var lccHtml = currentLayerKey === 'rgb' ? `
                        <div style="margin-bottom:5px;">
                            <b>Skala LCC:</b> ${petak.lcc_scale} 
                            <span style="display:inline-block; width:15px; height:15px; background-color:${lccColor}; border:1px solid #000; vertical-align:middle;"></span>
                        </div>` : '';

                    var popupHtml = `
                        <div style="min-width: 200px; font-family: 'Segoe UI', sans-serif;">
                            <h4 style="margin-bottom:8px; color:#1a472a; border-bottom: 2px solid #4caf50; padding-bottom:4px;">
                                <i class="fas fa-leaf"></i> Parcel Info (${recType})
                            </h4>
                            <div style="margin-bottom:8px;">
                                <b>Name:</b> 
                                <div class="d-flex gap-1 mt-1">
                                    <input type="text" id="input_nama_${escapeHtml(petak.id)}" value="${escapeHtml(petak.nama)}" style="width:110px; padding:4px 8px; border:1px solid #ccc; border-radius:4px; font-size:12px;">
                                    <button onclick="renamePetak('${currentUploadData.id}', '${escapeHtml(petak.id)}')" style="background:#2563eb; color:white; border:none; padding:4px 8px; border-radius:4px; cursor:pointer; font-size:11px;">Save</button>
                                </div>
                            </div>
                            <div style="margin-bottom:5px; font-size:12px;"><b>Area:</b> ${petak.luas_ha} Ha</div>
                            ${lccHtml}
                            <div style="margin-bottom:5px; font-size:12px;"><b>Recommendation (${recType}):</b><br>${rec} kg N/ha</div>
                            <div style="margin-bottom:10px; color:#b91c1c; font-size:12px;"><b>Requirement for this Parcel:</b><br><strong>${pupukTotal}</strong></div>
                            <hr style="margin: 8px 0; border:0; border-top:1px solid #eee;">
                            <button onclick="deletePetak('${currentUploadData.id}', '${escapeHtml(petak.id)}')" style="background:#dc2626; color:white; border:none; padding:6px; border-radius:4px; cursor:pointer; width:100%; font-size:11px; display:flex; align-items:center; justify-content:center; gap:6px;">
                                <i class="fas fa-trash"></i> Delete Parcel
                            </button>
                        </div>
                    `;
                    poly.bindPopup(popupHtml);
                    
                    poly.on('mouseover', function(e) { this.setStyle({ fillOpacity: 0.4, weight: 3 }); });
                    poly.on('mouseout', function(e) { this.setStyle({ fillOpacity: 0.15, weight: 2 }); });

                    petakLayerGroup.addLayer(poly);
                });
                petakLayerGroup.addTo(currentMap);
            }
"""
        # Replace toggleSegmentation body
        new_toggle = """
            function toggleSegmentation() {
                var isChecked = document.getElementById('checkSeg').checked;
                var segInfo = document.getElementById('segInfo');

                if (isChecked) {
                    if (!currentUploadData || !currentUploadData.info || !currentUploadData.info.petak_list) {
                        alert('Parcel detection data is not available for this upload.');
                        document.getElementById('checkSeg').checked = false;
                        return;
                    }
                    renderParcels();
                    segInfo.style.display = 'block';
                    segInfo.innerHTML = '<span style="color:#16a34a;"><i class="fas fa-check-circle"></i> ' + currentUploadData.info.total_petak + ' active parcels</span>';
                } else {
                    petakLayerGroup.clearLayers();
                    segInfo.style.display = 'none';
                }
            }
"""
        content = re.sub(r'function toggleSegmentation\(\) \{.*?\n            \}\n', new_toggle + render_logic, content, flags=re.DOTALL)
        
        # Modify switchLayer to use renderParcels instead of toggleSegmentation
        content = content.replace("if (document.getElementById('checkSeg').checked) {\n                    toggleSegmentation();\n                }", "if (document.getElementById('checkSeg').checked) {\n                    renderParcels();\n                }")

    with open(peta_file, 'w') as f:
        f.write(content)

def fix_upload():
    with open(upload_file, 'r') as f:
        content = f.read()

    # 5. Add escapeHtml
    escape_func = """
        function escapeHtml(str) { 
            return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); 
        }
"""
    if 'function escapeHtml' not in content:
        content = content.replace("function setStatus(msg, type) {", escape_func + "\n        function setStatus(msg, type) {")

    # 5. Use escapeHtml in popup
    content = content.replace('id="input_nama_${petak.id}"', 'id="input_nama_${escapeHtml(petak.id)}"')
    content = content.replace('value="${petak.nama}"', 'value="${escapeHtml(petak.nama)}"')
    content = content.replace("renamePetakLocal('${petak.id}')", "renamePetakLocal('${escapeHtml(petak.id)}')")

    # 4. renamePetakLocal loose equality
    content = content.replace("p => p.id === petakId", "p => String(p.id) === String(petakId)")

    # 6. HTML File size tags and JS logic
    if 'id="sizeRGB"' not in content:
        content = content.replace('<label>RGB File (.tif)</label>', '<label>RGB File (.tif) <span id="sizeRGB" style="font-size:11px;color:#666;margin-left:10px;"></span></label>')
        content = content.replace('<label>NDVI File (.tif)</label>', '<label>NDVI File (.tif) <span id="sizeNDVI" style="font-size:11px;color:#666;margin-left:10px;"></span></label>')
        content = content.replace('<label>NDRE File (.tif)</label>', '<label>NDRE File (.tif) <span id="sizeNDRE" style="font-size:11px;color:#666;margin-left:10px;"></span></label>')

        size_js = """
        function formatBytes(bytes) {
            if(bytes === 0) return '0 Bytes';
            var k = 1024, dm = 2, sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
            var i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
        }

        document.getElementById('fileRGB').addEventListener('change', function(e) {
            document.getElementById('sizeRGB').textContent = this.files[0] ? formatBytes(this.files[0].size) : '';
        });
        document.getElementById('fileNDVI').addEventListener('change', function(e) {
            document.getElementById('sizeNDVI').textContent = this.files[0] ? formatBytes(this.files[0].size) : '';
        });
        document.getElementById('fileNDRE').addEventListener('change', function(e) {
            document.getElementById('sizeNDRE').textContent = this.files[0] ? formatBytes(this.files[0].size) : '';
        });
"""
        content = content.replace("async function uploadLayers() {", size_js + "\n        async function uploadLayers() {")

    if 'if (rgb && rgb.size > maxFileSize)' not in content:
        validation_code = """
            var maxFileSize = 500 * 1024 * 1024; // 500MB
            if (rgb && rgb.size > maxFileSize) { alert("RGB file size exceeds 500MB limit!"); return; }
            if (ndvi && ndvi.size > maxFileSize) { alert("NDVI file size exceeds 500MB limit!"); return; }
            if (ndre && ndre.size > maxFileSize) { alert("NDRE file size exceeds 500MB limit!"); return; }
"""
        content = content.replace("if (!rgb && !ndvi && !ndre) {", validation_code + "\n            if (!rgb && !ndvi && !ndre) {")

    # 3. Fix toggleSegmentation double call
    if 'function renderParcels()' not in content:
        render_logic = """
        function renderParcels() {
            if (segmentOverlay) { map.removeLayer(segmentOverlay); segmentOverlay = null; }
            segmentOverlay = L.imageOverlay(activeLayers['segmentasi'].url, activeLayers['segmentasi'].bounds, {
                zIndex: 1000,
                interactive: false
            }).addTo(map);
            
            if (detectionInfo && detectionInfo.petak_list) {
                petakLayerGroup.clearLayers();
                detectionInfo.petak_list.forEach(function(petak) {
                    var poly = L.polygon(petak.polygon, {
                        color: '#16a34a',
                        weight: 2,
                        fillOpacity: 0.1
                    });
                    
                    var rec = petak.lcc_rec;
                    var recType = "LCC";
                    if(currentLayerKey === 'ndvi') {
                        rec = petak.ndvi_rec;
                        recType = "NDVI";
                    } else if(currentLayerKey === 'ndre') {
                        rec = petak.ndre_rec;
                        recType = "NDRE";
                    }

                    var rangeArr = rec.split('-').map(Number);
                    var pupukTotal = "";
                    if(rangeArr.length === 2) {
                        var sorted = [...rangeArr].sort((a,b)=>a-b);
                        var low = (sorted[0] * petak.luas_ha).toFixed(2);
                        var high = (sorted[1] * petak.luas_ha).toFixed(2);
                        pupukTotal = low + " - " + high + " kg N";
                    }
                    
                    var lccColor = "";
                    if(petak.lcc_scale == 5) lccColor = "#385a28";
                    if(petak.lcc_scale == 4) lccColor = "#466929";
                    if(petak.lcc_scale == 3) lccColor = "#578836";
                    if(petak.lcc_scale == 2) lccColor = "#70a732";
                    
                    var lccHtml = currentLayerKey === 'rgb' ? `
                        <div style="margin-bottom:5px;">
                            <b>LCC Scale:</b> ${petak.lcc_scale} 
                            <span style="display:inline-block; width:15px; height:15px; background-color:${lccColor}; border:1px solid #000; vertical-align:middle;"></span>
                        </div>` : '';

                    var popupHtml = `
                        <div style="min-width: 180px; font-family: 'Segoe UI', sans-serif;">
                            <h4 style="margin-bottom:8px; color:#1a472a; border-bottom: 2px solid #4caf50; padding-bottom:4px;">
                                <i class="fas fa-leaf"></i> Parcel Info (${recType})
                            </h4>
                            <div style="margin-bottom:8px;">
                                <b>Name:</b> 
                                <div class="d-flex gap-1 mt-1">
                                    <input type="text" id="input_nama_${escapeHtml(petak.id)}" value="${escapeHtml(petak.nama)}" style="width:100px; padding:2px; border:1px solid #ccc; border-radius:4px;">
                                    <button onclick="renamePetakLocal('${escapeHtml(petak.id)}')" style="background:#2563eb; color:white; border:none; padding:2px 8px; border-radius:4px; cursor:pointer;">Save</button>
                                </div>
                            </div>
                            <div style="margin-bottom:5px;"><b>Area:</b> ${petak.luas_ha} Ha (${petak.luas_m2} m2)</div>
                            ${lccHtml}
                            <div style="margin-bottom:5px;"><b>Recommendation (${recType}):</b><br>${rec} kg N/ha</div>
                            <div style="margin-bottom:10px; color:#b91c1c;"><b>Requirement for this Parcel:</b><br><strong>${pupukTotal}</strong></div>
                        </div>
                    `;
                    poly.bindPopup(popupHtml);
                    petakLayerGroup.addLayer(poly);
                });
                petakLayerGroup.addTo(map);
            }
        }
"""
        new_toggle = """
        function toggleSegmentation() {
            var isChecked = document.getElementById("checkSeg").checked;
            var infoBox = document.getElementById("infoBox");

            if (isChecked) {
                if (!activeLayers['segmentasi']) return alert("Segmentation data not found!");
                renderParcels();
                infoBox.style.display = "block";
            } else {
                if (segmentOverlay) { map.removeLayer(segmentOverlay); segmentOverlay = null; }
                petakLayerGroup.clearLayers();
                infoBox.style.display = "none";
            }
        }
"""
        content = re.sub(r'function toggleSegmentation\(\) \{.*?\n        \}\n', new_toggle + render_logic, content, flags=re.DOTALL)
        
        # Modify switchLayer to use renderParcels
        content = content.replace("if (document.getElementById(\"checkSeg\").checked) {\n                toggleSegmentation();\n            }", "if (document.getElementById(\"checkSeg\").checked) {\n                renderParcels();\n            }")

    with open(upload_file, 'w') as f:
        f.write(content)

fix_peta()
fix_upload()
print("Done")
