import zipfile
from collections import Counter


def _xml_attr(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _normalize_color(color: str) -> str:
    c = color.strip().lstrip("#").upper()
    if len(c) == 6:
        c += "FF"
    if len(c) != 8:
        raise ValueError(f"Invalid color: {color}")
    return f"#{c}"


def _clean_mesh(verts, faces, weld_eps: float = 1e-7):
    key_to_index = {}
    old_to_new = []
    clean_verts = []

    for v in verts:
        x, y, z = float(v[0]), float(v[1]), float(v[2])
        key = (
            round(x / weld_eps),
            round(y / weld_eps),
            round(z / weld_eps),
        )
        idx = key_to_index.get(key)
        if idx is None:
            idx = len(clean_verts)
            key_to_index[key] = idx
            clean_verts.append([x, y, z])
        old_to_new.append(idx)

    clean_faces = []
    seen = set()

    for f in faces:
        a, b, c = old_to_new[int(f[0])], old_to_new[int(f[1])], old_to_new[int(f[2])]
        if len({a, b, c}) < 3:
            continue
        key = tuple(sorted((a, b, c)))
        if key in seen:
            continue
        seen.add(key)
        clean_faces.append([a, b, c])

    return clean_verts, clean_faces


def _build_3mf_xml(objects: list) -> str:
    """
    Builds the 3dmodel.model XML content.
    Expects a list of dicts: { 'name': str, 'verts': List[List[float]], 'faces': List[List[int]], 'color': '#RRGGBB' }
    """
    core_ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    mat_ns = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"

    colors = []
    for obj in objects:
        c = _normalize_color(obj["color"])
        if c not in colors:
            colors.append(c)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{core_ns}" xmlns:m="{mat_ns}">',
        "  <resources>",
        '    <m:colorgroup id="1">',
    ]

    for c in colors:
        lines.append(f'      <m:color color="{c}"/>')

    lines.append("    </m:colorgroup>")

    # Each object gets its own ID starting from 2
    for i, obj in enumerate(objects, start=2):
        color = _normalize_color(obj["color"])
        color_index = colors.index(color)
        
        lines.append(f'    <object id="{i}" type="model" name="{_xml_attr(obj["name"])}" pid="1" pindex="{color_index}">')
        lines.append("      <mesh>")
        lines.append("        <vertices>")
        for v in obj["verts"]:
            lines.append(f'          <vertex x="{v[0]:.6f}" y="{v[1]:.6f}" z="{v[2]:.6f}"/>')
        lines.append("        </vertices>")
        lines.append("        <triangles>")
        for f in obj["faces"]:
            lines.append(f'          <triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"/>')
        lines.append("        </triangles>")
        lines.append("      </mesh>")
        lines.append("    </object>")

    lines.append("  </resources>")
    lines.append("  <build>")
    
    # We want to group pairs (Background + Text) into components so they stay relative to each other
    # But for now, let's just output them and see if the color works first.
    # Actually, let's try to output them as separate items like Step 666 did.
    for i in range(len(objects)):
        lines.append(f'    <item objectid="{i + 2}"/>')
        
    lines.append("  </build>")
    lines.append("</model>")

    return "\n".join(lines)


def save_3mf(objects: list, output_path: str):
    """
    Saves a list of objects as a valid 3MF package.
    """
    cleaned_objects = []
    for obj in objects:
        v, f = _clean_mesh(obj["verts"], obj["faces"])
        cleaned_objects.append({
            "name": obj["name"],
            "verts": v,
            "faces": f,
            "color": obj["color"]
        })
        
    model_xml = _build_3mf_xml(cleaned_objects)

    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""

    rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Targeted="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""
    # Fix typo in Relationship XML (Target, not Targeted)
    rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("3D/3dmodel.model", model_xml.encode("utf-8"))
