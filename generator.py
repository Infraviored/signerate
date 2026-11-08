import os
import re
from typing import List, Tuple, Callable

import cadquery as cq
from cadquery import Color, Location, Vector
from exporter_3mf import save_3mf


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


def generate_signs(
    texts: List[str],
    font_sizes: List[float],
    settings: dict,
    output_path: str,
    export_type: str = "3mf",
    progress_callback: Callable[[int, int], None] = None
) -> str:
    """Builds one sign per text, in list order. `font_sizes` is parallel to `texts`
    (planned by fonts.plan_sizes, the same function the preview uses)."""
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

    if len(texts) != len(font_sizes):
        raise ValueError("texts and font_sizes must have the same length.")
    pairs = [(t.strip(), float(fs)) for t, fs in zip(texts, font_sizes) if t.strip()]
    if not pairs:
        raise ValueError("No text provided for generation.")
    clean_texts = [t for t, _ in pairs]

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

    for i, (text, font_size) in enumerate(pairs):
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
                        "verts": base_verts,
                        "faces": base_faces,
                        "color": bg_color_hex,
                    },
                    {
                        "name": f"{sign_name}_Text",
                        "verts": text_verts,
                        "faces": text_faces,
                        "color": tx_color_hex,
                    },
                ],
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

    return output_path
