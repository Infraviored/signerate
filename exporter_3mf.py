import zipfile
import re

def _xml_attr(s: str) -> str:
    """Escapes characters for XML attributes."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )

def _build_3mf_xml(objects: list) -> str:
    """
    Builds the 3dmodel.model XML content.
    Expects a list of dicts: { 'name': str, 'verts': List[List[float]], 'faces': List[List[int]], 'color': '#RRGGBB' }
    """
    core_ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    mat_ns = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"

    colors = []
    for obj in objects:
        if obj["color"] not in colors:
            colors.append(obj["color"])

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{core_ns}" xmlns:m="{mat_ns}">',
        "  <resources>",
        '    <m:colorgroup id="1">',
    ]

    for c in colors:
        # 3MF colors are usually #RRGGBBAA
        lines.append(f'      <m:color color="{c}FF"/>')

    lines.append("    </m:colorgroup>")

    for i, obj in enumerate(objects, start=2):
        color_index = colors.index(obj["color"])
        lines.append(
            f'    <object id="{i}" type="model" name="{_xml_attr(obj["name"])}" pid="1" pindex="{color_index}">'
        )
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
    for i in range(len(objects)):
        lines.append(f'    <item objectid="{i + 2}"/>')
    lines.append("  </build>")
    lines.append("</model>")

    return "\n".join(lines)

def save_3mf(objects: list, output_path: str):
    """
    Saves a list of objects as a valid 3MF package.
    """
    model_xml = _build_3mf_xml(objects)

    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""

    rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("3D/3dmodel.model", model_xml.encode("utf-8"))
