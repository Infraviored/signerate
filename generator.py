import json
import os
import glob
import re
from pathlib import Path
from typing import List, Tuple, Dict

import cadquery as cq
from cadquery import Color, Location, Vector
from exporter_3mf import save_3mf


SETTINGS_FILE = "settings.json"


DEFAULT_SETTINGS = {
    "width": 120.0,
    "height": 40.0,
    "min_margin": 3.0,
    "base_thickness": 2.0,
    "text_thickness": 1.5,   # visible / flush text height and pocket depth
    "bg_color": "#ffffff",
    "text_color": "#000000",
    "text_protrusion": 0.0,
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
        json.dump(settings, f, indent=2)


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

    for text in texts:
        text_strip = text.strip()
        if not text_strip:
            continue

        sample = (
            cq.Workplane("XY")
            .text(
                text_strip,
                fontsize=ref_size,
                distance=0.5,
                fontPath=font_path,
                halign="center",
                valign="center",
            )
        )

        bb = sample.val().BoundingBox()
        text_w = bb.xmax - bb.xmin
        text_h = bb.ymax - bb.ymin
        if text_w <= 0 or text_h <= 0: continue

        scale_w = available_width / text_w
        scale_h = available_height / text_h
        
        # The actual scale for this sign is min(scale_w, scale_h)
        local_scale = min(scale_w, scale_h)
        
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

    return ref_size * max_scale, limiting_text


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

    for i, text in enumerate(clean_texts):
        y_offset = i * (height + spacing)
        
        # Background Rect
        svg.append(f'  <rect x="0" y="{y_offset}" width="{width}" height="{height}" fill="{bg_color}" rx="2" ry="2" />')
        
        # Placeholder for Text (SVG text rendering is approximate, but good for layout)
        # We use a generic sans-serif for the browser but position it according to the CQ math
        svg.append(
            f'  <text x="{width/2}" y="{y_offset + height/2}" '
            f'fill="{tx_color}" font-family="sans-serif" font-size="{font_size * 0.9}px" '
            f'text-anchor="middle" dominant-baseline="central" style="pointer-events:none;">'
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
    export_type: str = "3mf"
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
        safe = _safe_name(text)
        y_offset = i * (height + spacing)
        
        # Text always ends at base_h + protrusion
        # If protrusion is 0, top of text is flush with base_h.
        text_z_top = base_h + protrusion
        text_z_bottom = text_z_top - text_h
        
        print(f"  Sign '{text}': Z_bottom={text_z_bottom:.3f}, Z_top={text_z_top:.3f}")

        # 1. Generate Letter Solid
        letters_raw = (
            cq.Workplane("XY")
            .workplane(offset=text_z_bottom)
            .text(text, fontsize=font_size, distance=text_h, fontPath=font_path,
                  halign="center", valign="center")
        )
        letters_shape = _fuse_all_solids(letters_raw, tol=pocket_tol).clean()

        # 2. Generate Pocket
        # Pocket depth is only the part that is below the top surface
        pocket_depth = base_h - text_z_bottom
        
        if pocket_depth > 0.001:
            pocket_raw = (
                cq.Workplane("XY")
                .workplane(offset=text_z_bottom)
                .text(text, fontsize=font_size, distance=pocket_depth + top_opening_overshoot,
                fontPath=font_path, halign="center", valign="center")
            )
            pocket_shape = _fuse_all_solids(pocket_raw, tol=pocket_tol).clean()

            # 3. Cut Background
            base_solid = cq.Workplane("XY").box(width, height, base_h, centered=(True, True, False)).val()
            base_cut_shape = base_solid.cut(pocket_shape, tol=pocket_tol).clean()
        else:
            # If text is floating entirely above (protrusion > text_h), no pocket needed
            base_cut_shape = cq.Workplane("XY").box(width, height, base_h, centered=(True, True, False)).val()

        if export_type.lower() == "3mf":
            # Tessellieren für 3MF
            base_verts, base_faces = _tessellate(
                base_cut_shape,
                y_offset=y_offset,
                tolerance=tess_tol,
                ang_tol=tess_ang_tol,
            )
            text_verts, text_faces = _tessellate(
                letters_shape,
                y_offset=y_offset,
                tolerance=tess_tol,
                ang_tol=tess_ang_tol,
            )
            objects_3mf.append({
                "name": f"Background_{safe}_{i}",
                "verts": base_verts, "faces": base_faces, "color": bg_color_hex
            })
            objects_3mf.append({
                "name": f"{safe}_{i}",
                "verts": text_verts, "faces": text_faces, "color": tx_color_hex
            })
        else:
            # Assembly für STEP
            loc = Location(Vector(0, y_offset, 0))
            sign_assy = cq.Assembly(name=f"Sign_{safe}_{i}", loc=loc)
            sign_assy.add(base_cut_shape, name="Background", color=Color(bg_r, bg_g, bg_b))
            sign_assy.add(letters_shape, name="Text", color=Color(tx_r, tx_g, tx_b))
            assy.add(sign_assy)

    if export_type.lower() == "3mf":
        save_3mf(objects_3mf, output_path)
    else:
        assy.export(output_path)  # STEP export via assembly

    return font_size, output_path
