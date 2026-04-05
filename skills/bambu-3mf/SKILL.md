---
name: bambu-3mf
description: "Use when working with .3mf files — reading print settings, changing plate layout, swapping meshes, modifying filament assignments, inspecting project metadata, creating Bambu Studio projects from scratch, or batch-editing multiple projects. Triggers: .3mf, 3mf, bambu, print settings, plate layout, filament, slicer, gcode bundle."
license: MIT
metadata:
  version: 2.0.0
  author: lostprojects
  category: 3d-printing
  updated: 2026-04-05
---

# bambu-3mf

Read, modify, and write Bambu Studio .3mf project files while preserving unmodified sections when possible.

This skill is meant to work for two kinds of requests:

- practical print-file help, like changing settings or explaining what is in a project
- deeper technical work, like scripting, auditing file contents, or editing mesh-related data

## Library location

The library is at `${CLAUDE_SKILL_DIR}/scripts/bambu3mf.py`. Pure Python stdlib — no dependencies.

```python
import sys, os
sys.path.insert(0, os.path.join(os.environ.get("CLAUDE_SKILL_DIR", "."), "scripts"))
from bambu3mf import Bambu3MF
```

If `CLAUDE_SKILL_DIR` is not set (standalone use), the library is also pip-installable from the repo root:

```python
from bambu3mf import Bambu3MF
```

## Quick reference

```python
# Load
proj = Bambu3MF.load("file.3mf")

# Create from scratch
proj = Bambu3MF.new()  # minimal project scaffold with 1 plate and BambuStudio metadata

# Inspect
print(proj.summary())                   # includes plate bed_type
print(proj.metadata)                    # {'Application': 'BambuStudio-01.10.02.76', ...}
print(proj.get_filament_colors())       # ['#FFFFFF', '#000000']
print(proj.get_filament_types())        # ['PLA', 'PLA']
print(proj.get_setting("layer_height")) # '0.2'

# Objects
for obj in proj.objects:
    print(f"Object {obj.id}: {obj.name}, extruder={obj.extruder}")
    if obj.shape_config:                # BambuStudioShape emboss data
        print(f"  shape: {obj.shape_config.shape_name}")
    for part in obj.parts:
        print(f"  Part {part.id}: {part.name}, extruder={part.extruder}, shared={part.mesh_shared}")
    for key, value in obj.settings:     # ordered list of (key, value) tuples
        print(f"  {key} = {value}")

# Plates — per-plate settings as named fields
for plate in proj.plates:
    print(f"Plate {plate.plater_id}: {len(plate.instances)} objects")
    print(f"  bed_type={plate.bed_type}, print_sequence={plate.print_sequence}")
    print(f"  spiral_mode={plate.spiral_mode}, filament_map_mode={plate.filament_map_mode}")

# Mesh access (in sub-models)
for path, leaf_objects in proj.sub_models.items():
    for obj in leaf_objects:
        if obj.mesh:
            print(f"  {len(obj.mesh.vertices)} verts, {len(obj.mesh.triangles)} tris")

# Modify settings
proj.set_setting("layer_height", "0.12")
proj.set_setting("sparse_infill_density", "20%")

# Modify object settings
obj = proj.objects[0]
obj.settings.append(("wall_loops", "4"))
proj.mark_modified("model_settings")

# Change filament colors
proj.set_setting("filament_colour", ["#FF0000", "#00FF00"])

# Modify per-plate settings
proj.plates[0].bed_type = "textured_plate"
proj.plates[0].print_sequence = "by_object"
proj.mark_modified("model_settings")

# Save
proj.save("modified.3mf")
```

## Modifying meshes

```python
from bambu3mf import Vertex, Triangle

leaf = proj.sub_models["3D/Objects/object_1.model"][0]
leaf.mesh.vertices = [Vertex(0,0,0), Vertex(10,0,0), ...]
leaf.mesh.triangles = [Triangle(v1=0, v2=1, v3=2), ...]
proj.mark_modified("3D/Objects/object_1.model")
proj.save("new.3mf")
```

## CLI

```bash
python bambu3mf.py file.3mf summary
python bambu3mf.py file.3mf list-objects
python bambu3mf.py file.3mf dump-settings
python bambu3mf.py file.3mf round-trip output.3mf
```

## What it handles

- Full Bambu project packaging used by common Bambu Studio project and gcode bundles
- Mesh data with full precision
- Per-triangle painting (support, seam, MMU color, fuzzy skin)
- Multi-plate layout with per-plate settings
- Many slicer settings across project, object, and plate data
- Per-object/part setting overrides
- AMS filament mapping and flush matrix
- Cut/connector info
- Assembly transforms
- Gcode bundles
- Thumbnails and auxiliary files
- Embedded presets (process, filament, machine)
- Shape/emboss config (ShapeConfig)
- Mesh sharing

## Verified scope limits

- This is not a complete implementation of every 3MF extension defined by the 3MF Core, Materials, and Production specs.
- Materials support is limited to Bambu-relevant `m:colorgroup` data plus triangle `pid`/`p1`/`p2`/`p3` properties; it does not provide first-class support for `basematerials`, `texture2d`, `texture2dgroup`, `multiproperties`, or display-property groups.
- Production support covers the BambuStudio paths used here: `p:UUID`, `p:path`, split sub-model relationships, and gcode relationships. It does not implement the Production Alternatives schema (`pa:alternatives`, `modelresolution`).
- Unknown archive files are preserved through the raw-file catch-all, but unknown XML structures are only preserved if the corresponding section is not regenerated.

## Key data structures

| Class | Purpose |
|-------|---------|
| `Bambu3MF` | Top-level project — `load()`, `new()`, `save()`, `summary()` |
| `ModelObject` | Object with name, extruder, settings, parts, components, shape_config |
| `Part` | Sub-volume — name, matrix, extruder, mesh_shared, mesh stats |
| `Plate` | Build plate — ID, name, thumbnails, instances, per-plate settings |
| `BuildItem` | Build entry — object reference, transform, printable flag |
| `ComponentRef` | Component link — parent to child object via path |
| `AssembleItem` | Assembly view positioning |
| `CutObject` / `CutConnector` | Cut plane and connector definitions |
| `ObjectMesh` | Vertex + triangle arrays |
| `Triangle` | v1/v2/v3 indices + per-triangle painting |
| `Vertex` | x, y, z coordinates |
| `ColorGroup` / `ColorDef` | Materials Extension colors |
| `SliceInfo` / `SlicePlate` | Slice metadata — time, weight, filament, nozzle info |
| `SliceFilament` | Per-filament slice data |
| `ShapeConfig` | BambuStudioShape emboss text config |
| `MeshStat` | Per-part mesh statistics |

For detailed file structure and namespace reference, see `references/file-format.md`.

## Important constraints

- Pure Python stdlib — no pip dependencies needed
- Per-triangle painting data is hex strings — preserve exactly
- UUID suffixes are constants from BambuStudio source — don't invent new ones
- `project_settings.config` is JSON; `model_settings.config` is XML
- Object `settings` is an ordered list of `(key, value)` tuples — order matters
- Accepts both `p:UUID` and `p:uuid` attribute forms when reading files
- Embedded presets preserved through round-trip via raw_files catch-all
