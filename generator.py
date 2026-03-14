import json
import os
import glob
import re
import trimesh
import numpy as np
from pathlib import Path
from typing import List, Tuple

import cadquery as cq
from cadquery import Color, Location, Vector

SETTINGS_FILE = "settings.json"

DEFAULT_SETTINGS = {
    "width": 120.0,
    "height": 40.0,
    "min_margin": 3.0,
    "base_thickness": 2.0,
    "text_thickness": 1.5,
    "bg_color": "#ffffff",
    "text_color": "#000000",
    "text_protrusion": 1.5,
    "font_path": ""
}


def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
        return {**DEFAULT_SETTINGS, **data}
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def hex_to_rgb_float(hex_color: str) -> Tuple[float, float, float]:
    h = hex_color.lstrip("#")
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
    seen = set()
    for base_path in search_paths:
        for ext in ("*.ttf", "*.TTF", "*.otf", "*.OTF"):
            for path in glob.glob(os.path.join(base_path, "**", ext), recursive=True):
                name = Path(path).stem
                if name not in seen:
                    seen.add(name)
                    fonts.append({"name": name, "path": path})

    return sorted(fonts, key=lambda x: x["name"].lower())


def calculate_optimal_font_size(
    texts: List[str],
    font_path: str,
    available_width: float,
    available_height: float,
    ref_size: float = 20.0,
) -> float:
    """
    Renders every text at ref_size, measures bounding boxes, then scales
    proportionally so that the widest/tallest entry still fits.
    One CadQuery render per text — no binary search needed.
    """
    max_scale = float("inf")

    for text in texts:
        text = text.strip()
        if not text:
            continue
        shape = (
            cq.Workplane("XY")
            .text(
                text,
                fontsize=ref_size,
                distance=0.5,
                fontPath=font_path,
                halign="center",
                valign="center",
            )
        )
        bb = shape.val().BoundingBox()
        text_w = bb.xmax - bb.xmin
        text_h = bb.ymax - bb.ymin
        if text_w > 0:
            max_scale = min(max_scale, available_width / text_w)
        if text_h > 0:
            max_scale = min(max_scale, available_height / text_h)

    return ref_size * max_scale if max_scale != float("inf") else ref_size


def shape_to_trimesh(cq_shape, color_rgba=(255, 255, 255, 255)):
    """
    Converts a CadQuery shape (Workplane or Compound) into a single trimesh Mesh.
    Tessellates the shape and applies the specified color.
    """
    # tessellate(tolerance, angularTolerance)
    tess = cq_shape.val().tessellate(0.05, 0.5)
    
    verts = np.array([[v.x, v.y, v.z] for v in tess[0]])
    faces = np.array(tess[1])
    
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    
    # Apply color to all faces
    mesh.visual = trimesh.visual.ColorVisuals(
        mesh=mesh,
        face_colors=np.tile(color_rgba, (len(faces), 1))
    )
    return mesh


def _safe_name(text: str, max_len: int = 40) -> str:
    """Bereinigt Text zu einem gültigen 3MF-Komponentennamen."""
    name = text.strip()
    # Entferne Zeichen die in XML/3MF-Namen Probleme machen
    name = re.sub(r'[<>&"\'/\\]', '_', name)
    return name[:max_len] if name else "Schild"


def generate_signs(
    texts: List[str],
    settings: dict,
    output_path: str = "signs.3mf",
) -> Tuple[float, str]:
    width       = float(settings["width"])
    height      = float(settings["height"])
    min_margin  = float(settings["min_margin"])
    base_h      = float(settings["base_thickness"])
    text_h      = float(settings["text_thickness"])
    font_path   = settings["font_path"]

    available_w = width  - 2 * min_margin
    available_h = height - 2 * min_margin

    clean_texts = [t for t in texts if t.strip()]
    font_size = calculate_optimal_font_size(
        clean_texts, font_path, available_w, available_h
    )

    # Convert colors to 0-255 RGBA
    bg_rgba = tuple(int(x * 255) for x in hex_to_rgb_float(settings["bg_color"]))   + (255,)
    tx_rgba = tuple(int(x * 255) for x in hex_to_rgb_float(settings["text_color"])) + (255,)

    SPACING    = 10.0
    protrusion = float(settings.get("text_protrusion", text_h))
    
    # ── Trimesh Scene ──────────────────────────────────────────────────────
    scene = trimesh.scene.Scene()

    for i, raw_text in enumerate(clean_texts):
        text      = raw_text.strip()
        safe      = _safe_name(text)
        y_offset  = i * (height + SPACING)

        # Z-Math for embedded text:
        # text_z_top is base_h + protrusion
        # text_z_bottom is (base_h + protrusion) - text_h
        text_z_top    = base_h + protrusion
        text_z_bottom = text_z_top - text_h

        # 1. Create text body in CadQuery
        letters_cq = (
            cq.Workplane("XY")
            .workplane(offset=text_z_bottom)
            .text(
                text,
                fontsize=font_size,
                distance=text_h,
                fontPath=font_path,
                halign="center",
                valign="center",
            )
        )

        # 2. Create base plate and cut if overlap
        base_cq = cq.Workplane("XY").box(width, height, base_h, centered=(True, True, False))
        if text_z_bottom < base_h:
            base_cq = base_cq.cut(letters_cq)

        # 3. Convert to trimesh and add to scene
        # This treats the whole 'letters' block as ONE mesh object in the 3mf
        base_mesh    = shape_to_trimesh(base_cq,    color_rgba=bg_rgba)
        letters_mesh = shape_to_trimesh(letters_cq, color_rgba=tx_rgba)

        # Create translation matrix
        T = trimesh.transformations.translation_matrix([0.0, y_offset, 0.0])
        
        # Add to scene with appropriate names for Slicer clarity
        scene.add_geometry(base_mesh,    node_name=f"Background_{safe}_{i}", transform=T)
        scene.add_geometry(letters_mesh, node_name=f"{safe}_{i}",            transform=T)

    # Export using trimesh which creates a valid colored 3mf
    data = trimesh.exchange.export.export_scene(scene, file_type="3mf")
    with open(output_path, "wb") as f:
        f.write(data)

    return font_size, output_path
