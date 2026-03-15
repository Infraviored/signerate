import json
import os
import glob
import re
from pathlib import Path
from typing import List, Tuple, Dict, Callable
from PIL import ImageFont

import cadquery as cq
from cadquery import Color, Location, Vector
from exporter_3mf import save_3mf


SETTINGS_FILE = "settings.json"


DEFAULT_SETTINGS = {
    "width": 120.0,
    "height": 40.0,
    "min_margin": 3.0,
    "base_thickness": 2.0,   # Total thickness of the sign plate (background)
    "text_thickness": 1.5,   # Full vertical extrusion height of the letters
    "bg_color": "#ffffff",
    "text_color": "#000000",
    "text_protrusion": 0.0,  # 0 = Flush with top surface. Positive = sticks out.
    "font_path": "",
    "export_format": "3mf"
}


def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {**DEFAULT_SETTINGS, **data}
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def hex_to_hex(hex_color: str) -> str:
    h = hex_color.strip().lstrip("#").upper()
    if len(h) != 6:
        raise ValueError(f"Invalid color: {hex_color}")
    return f"#{h}"


def hex_to_rgb_float(hex_color: str) -> Tuple[float, float, float]:
    h = hex_color.strip().lstrip("#").upper()
    if len(h) != 6:
        raise ValueError(f"Ungültige Farbe: {hex_color}")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r / 255.0, g / 255.0, b / 255.0


def find_system_fonts() -> List[dict]:
    if os.name == "nt":
        search_paths = [r"C:\Windows\Fonts"]
    else:
        search_paths = [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.expanduser("~/.fonts"),
            os.path.expanduser("~/.local/share/fonts"),
            "/Library/Fonts",
            "/System/Library/Fonts",
            os.path.expanduser("~/Library/Fonts"),
        ]

    fonts = []
    seen_paths = set()

    for base_path in search_paths:
        if not os.path.exists(base_path):
            continue
        for ext in ("*.ttf", "*.TTF", "*.otf", "*.OTF"):
            pattern = os.path.join(base_path, "**", ext)
            for path in glob.glob(pattern, recursive=True):
                norm = os.path.normpath(path)
                if norm in seen_paths:
                    continue
                seen_paths.add(norm)
                fonts.append({
                    "name": Path(path).stem,
                    "path": norm.replace("\\", "/") # Use forward slashes for JS safety
                })

    fonts.sort(key=lambda x: x["name"].lower())
    return fonts


def find_arial_path() -> str:
    """Returns the path to the Arial font if found, otherwise the first system font."""
    fonts = find_system_fonts()
    for f in fonts:
        if "arial" in f["name"].lower():
            return f["path"]
    return fonts[0]["path"] if fonts else ""


def _safe_name(text: str, max_len: int = 48) -> str:
    name = text.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F&\']', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name[:max_len] if name else "Schild"


def _shape_from_wp(obj) -> cq.Shape:
    if isinstance(obj, cq.Workplane):
        val = obj.val()
        if isinstance(val, (cq.Vector, float, int)):
            raise ValueError("Workplane enthält keine Shape-Geometrie.")
        return val
    return obj


def _fuse_all_solids(wp: cq.Workplane, tol: float = 0.02) -> cq.Shape:
    solids = wp.solids().vals()
    if not solids:
        raise ValueError("No solids found in text body.")

    fused = solids[0]
    for s in solids[1:]:
        fused = fused.fuse(s, tol=tol)

    return fused.clean()


def _tessellate(shape, y_offset: float = 0.0, tolerance: float = 0.05, ang_tol: float = 0.5):
    shape = _shape_from_wp(shape)
    verts_raw, faces_raw = shape.tessellate(tolerance, ang_tol)

    verts = [[v.x, v.y + y_offset, v.z] for v in verts_raw]
    faces = [list(f) for f in faces_raw]
    return verts, faces


def calculate_optimal_font_size(
    texts: List[str],
    font_path: str,
    available_width: float,
    available_height: float,
    ref_size: float = 20.0,
) -> Tuple[float, str]:
    """
    Calculates font size and returns which text was the width-limiting factor.
    Note: If the height is the limiting factor, it affects all signs equally,
    so we only report a 'limiting_text' if it's limited by width (too long).
    """
    # 1. Global height limit (applied to everyone)
    # We sample a generic tall character to find height scale if texts are empty? 
    # No, we'll just check it during the loop.
    
    max_scale = float("inf")
    limiting_text = ""
    is_width_limited = False

    # Use Pillow for extremely fast metrics
    try:
        # We use a large ref_size in pixels to get good precision
        pilot_size = 100
        font = ImageFont.truetype(font_path, size=pilot_size)
    except Exception as e:
        print(f"DEBUG: Pillow font load failed: {e}")
        # Full fallback if Pillow fails (unlikely given it's installed)
        return _calculate_legacy(texts, font_path, available_width, available_height, ref_size)

    for text in texts:
        text_strip = text.strip()
        if not text_strip:
            continue

        # getbbox returns (left, top, right, bottom)
        bbox = font.getbbox(text_strip)
        raw_w = bbox[2] - bbox[0]
        raw_h = bbox[3] - bbox[1]

        # Convert back to 'ref_size' scale
        text_w = raw_w * (ref_size / pilot_size)
        text_h = raw_h * (ref_size / pilot_size)
        if text_w <= 0 or text_h <= 0: continue

        # Scale based on geometric bounding box
        scale_w = available_width / text_w
        scale_h = available_height / text_h
        
        # The actual scale for this sign is min(scale_w, scale_h)
        # Apply a small safety factor (e.g. 0.975) to account for tessellation variations
        local_scale = min(scale_w, scale_h) * 0.975
        
        if local_scale < max_scale:
            max_scale = local_scale
            # We ONLY report a bottleneck if the width was what constrained THIS local sign
            # AND this local sign is the new global minimum.
            if scale_w < scale_h:
                limiting_text = text_strip
                is_width_limited = True
            else:
                limiting_text = ""
                is_width_limited = False

    if max_scale == float("inf"):
        return ref_size, ""

    return round(max_scale * ref_size, 3), limiting_text


def _calculate_legacy(texts, font_path, available_width, available_height, ref_size):
    """Fallback using CadQuery for metrics (SLOW)"""
    max_scale = float("inf")
    limiting_text = ""
    for text in texts:
        text_strip = text.strip()
        if not text_strip: continue
        sample = cq.Workplane("XY").text(text_strip, fontsize=ref_size, distance=0.1, fontPath=font_path)
        bb = sample.val().BoundingBox()
        text_w, text_h = bb.xmax - bb.xmin, bb.ymax - bb.ymin
        sw = available_width / text_w
        sh = available_height / text_h
        scale = min(sw, sh)
        if scale < max_scale:
            max_scale = scale
            limiting_text = text_strip
    return round(max_scale * ref_size, 3), limiting_text


def generate_preview_svg(
    texts: List[str],
    settings: dict
) -> str:
    """Generates an SVG string showing a 2D preview of all signs."""
    width = float(settings["width"])
    height = float(settings["height"])
    margin = float(settings["min_margin"])
    bg_color = settings["bg_color"]
    tx_color = settings["text_color"]
    font_path = settings["font_path"]

    clean_texts = [t.strip() for t in texts if t.strip()]
    if not clean_texts:
        return ""
    if not font_path or not os.path.exists(font_path):
        raise ValueError("No valid font selected.")

    available_w = width - 2 * margin
    available_h = height - 2 * margin
    font_size, _ = calculate_optimal_font_size(clean_texts, font_path, available_w, available_h)

    spacing = 10.0
    total_height = len(clean_texts) * height + (len(clean_texts) - 1) * spacing
    
    # SVG Boilerplate
    svg = [
        f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        f'<svg width="{width}mm" height="{total_height}mm" viewBox="0 0 {width} {total_height}" xmlns="http://www.w3.org/2000/svg">'
    ]

    align = settings.get("horizontal_align", "center").lower()
    x_pos = width / 2
    anchor = "middle"
    
    if align == "left":
        x_pos = margin
        anchor = "start"
    elif align == "right":
        x_pos = width - margin
        anchor = "end"

    for i, text in enumerate(clean_texts):
        y_offset = i * (height + spacing)
        
        # Background Rect
        svg.append(f'  <rect x="0" y="{y_offset}" width="{width}" height="{height}" fill="{bg_color}" rx="2" ry="2" />')
        
        # Text positioning in SVG
        svg.append(
            f'  <text x="{x_pos}" y="{y_offset + height/2}" '
            f'fill="{tx_color}" font-family="sans-serif" font-size="{font_size * 0.9}px" '
            f'text-anchor="{anchor}" dominant-baseline="central" style="pointer-events:none;">'
            f'{_xml_attr(text)}'
            f'</text>'
        )

    svg.append('</svg>')
    return "\n".join(svg)


def _xml_attr(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def generate_signs(
    texts: List[str],
    settings: dict,
    output_path: str,
    export_type: str = "3mf",
    progress_callback: Callable[[int, int], None] = None
) -> Tuple[float, str]:
    width = float(settings["width"])
    height = float(settings["height"])
    min_margin = float(settings["min_margin"])
    base_h = float(settings["base_thickness"])
    text_h = float(settings["text_thickness"])
    font_path = settings["font_path"]

    if not font_path or not os.name == 'nt' and not os.path.exists(font_path):
        raise ValueError("No valid font selected.")

    if text_h <= 0:
        raise ValueError("text_thickness must be > 0.")

    available_w = width - 2 * min_margin
    available_h = height - 2 * min_margin

    if available_w <= 0 or available_h <= 0:
        raise ValueError("Width/Height too small relative to margin.")

    clean_texts = [t.strip() for t in texts if t.strip()]
    if not clean_texts:
        raise ValueError("No text provided for generation.")

    font_size, _ = calculate_optimal_font_size(
        clean_texts, font_path, available_w, available_h
    )

    bg_color_hex = hex_to_hex(settings["bg_color"])
    tx_color_hex = hex_to_hex(settings["text_color"])
    bg_r, bg_g, bg_b = hex_to_rgb_float(bg_color_hex)
    tx_r, tx_g, tx_b = hex_to_rgb_float(tx_color_hex)

    spacing = 10.0
    pocket_tol = 0.02
    top_opening_overshoot = 0.05
    tess_tol = 0.01
    tess_ang_tol = 0.1

    protrusion = float(settings.get("text_protrusion", 0.0))
    print(f"DEBUG: Generating signs with base_h={base_h}, text_h={text_h}, protrusion={protrusion}")

    # Für 3MF-Export (Tessellierte Objekte)
    objects_3mf = []
    # Für STEP-Export (Assembly)
    assy = cq.Assembly(name="Signs")

    for i, text in enumerate(clean_texts):
        if progress_callback:
            progress_callback(i, len(clean_texts))
        
        safe = _safe_name(text)
        # Calculate padding for numeric prefix
        pad = max(2, len(str(len(clean_texts))))
        idx_str = str(i).zfill(pad)
        sign_name = f"{idx_str}_{safe}"

        y_offset = i * (height + spacing)
        
        # Text always ends at base_h + protrusion
        # If protrusion is 0, top of text is flush with base_h.
        text_z_top = base_h + protrusion
        text_z_bottom = text_z_top - text_h
        
        print(f"  Sign '{text}': Z_bottom={text_z_bottom:.3f}, Z_top={text_z_top:.3f}")

        # 1. Generate Letter Solid (Temporary for bounding box)
        text_align = settings.get("horizontal_align", "center").lower()
        
        letters_raw = (
            cq.Workplane("XY")
            .text(text, fontsize=font_size, distance=text_h, fontPath=font_path,
                  halign="center", valign="center")
        )
        # We use a temporary object to find the GEOMETRIC center
        bb = letters_raw.val().BoundingBox()
        
        # Calculate Offsets
        # Center horizontally vs left/right
        h_offset = 0
        if text_align == "left":
            # (bb.xmin+bb.xmax)/2 is where the center IS currently.
            # We want bb.xmin to be at -available_w / 2
            available_w = width - 2 * min_margin
            h_offset = (-available_w / 2) - bb.xmin
        elif text_align == "right":
            # We want bb.xmax to be at available_w / 2
            available_w = width - 2 * min_margin
            h_offset = (available_w / 2) - bb.xmax
        else:
            # Geometric center horizontally
            h_offset = - (bb.xmin + bb.xmax) / 2
            
        # Vertical: Always geometric center
        v_offset = - (bb.ymin + bb.ymax) / 2
        
        # Final Letter Shape translated to correct 3D spot
        letters_shape = (
            letters_raw
            .translate((h_offset, v_offset, text_z_bottom))
            .clean()
            .val()
        )

        # 2. Generate Pocket
        # Pocket depth is only the part that is below the top surface
        pocket_depth = base_h - text_z_bottom
        
        if pocket_depth > 0.001:
            pocket_raw = (
                cq.Workplane("XY")
                .text(text, fontsize=font_size, distance=pocket_depth + top_opening_overshoot,
                      fontPath=font_path, halign="center", valign="center")
            )
            pocket_shape = (
                pocket_raw
                .translate((h_offset, v_offset, text_z_bottom))
                .clean()
                .val()
            )

            # 3. Cut Background
            base_solid = cq.Workplane("XY").box(width, height, base_h, centered=(True, True, False)).val()
            base_cut_shape = base_solid.cut(pocket_shape, tol=pocket_tol).clean()
        else:
            # If text is floating entirely above (protrusion > text_h), no pocket needed
            base_cut_shape = cq.Workplane("XY").box(width, height, base_h, centered=(True, True, False)).val()

        if export_type.lower() == "3mf":
            # Hierarchical structure for 3MF
            base_verts, base_faces = _tessellate(
                base_cut_shape, y_offset=y_offset, tolerance=tess_tol, ang_tol=tess_ang_tol
            )
            text_verts, text_faces = _tessellate(
                letters_shape, y_offset=y_offset, tolerance=tess_tol, ang_tol=tess_ang_tol
            )
            
            objects_3mf.append({
                "name": sign_name,
                "parts": [
                    {
                        "name": f"{sign_name}_Background",
                        "verts": base_verts, "faces": base_faces, "color": bg_color_hex
                    },
                    {
                        "name": f"{sign_name}-text",
                        "verts": text_verts, "faces": text_faces, "color": tx_color_hex
                    }
                ]
            })
        else:
            # Assembly for STEP (keep hierarchical)
            loc = Location(Vector(0, y_offset, 0))
            sign_assy = cq.Assembly(name=sign_name, loc=loc)
            sign_assy.add(base_cut_shape, name=f"{sign_name}_Background", color=Color(bg_r, bg_g, bg_b))
            sign_assy.add(letters_shape, name=f"{sign_name}-text", color=Color(tx_r, tx_g, tx_b))
            assy.add(sign_assy)

    if export_type.lower() == "3mf":
        save_3mf(objects_3mf, output_path)
    else:
        assy.export(output_path)  # STEP export via assembly

    return font_size, output_path
