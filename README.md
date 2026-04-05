# 3MF Agent Tools

A Python library for reading and writing Bambu Studio `.3mf` project files with byte-perfect round-trip fidelity.

No third-party dependencies — pure Python stdlib (`zipfile`, `xml.etree`, `json`, `dataclasses`). Python 3.10+.

## Install

**pip** (standalone):
```bash
pip install bambu3mf
```

**Claude Code plugin** (AI agent):
```
/plugin install bambu-3mf
```

Or add to your marketplace:
```json
{
  "name": "bambu-3mf",
  "source": { "source": "github", "repo": "lostprojects/3MF-Agent-Tools" }
}
```

## Why this exists

Bambu Studio's `.3mf` project format is a ZIP archive containing XML model data, JSON print settings, per-triangle painting data, plate layouts, AMS filament assignments, thumbnails, and optionally sliced gcode. There was no library — for AI or otherwise — that could read and write these files without losing data.

Built by analyzing the [BambuStudio source code](https://github.com/bambulab/BambuStudio) (`bbs_3mf.cpp`). Every constant verified against 3+ sources: BambuStudio C++ line numbers, 3MF Core/Production/Materials specifications, and real exported files.

## What it handles

| Feature | Details |
|---------|---------|
| **Mesh data** | Vertices, triangles, transforms — full precision |
| **Per-triangle painting** | Support, seam, MMU color, fuzzy skin |
| **Print settings** | All 700+ slicer keys |
| **Per-object overrides** | Object and part-level setting overrides, ordering preserved |
| **Multi-plate layout** | Plate definitions, assignments, thumbnails, per-plate settings |
| **Per-plate settings** | bed_type, print_sequence, spiral_mode, filament_map_mode, pattern_file |
| **AMS filament mapping** | Colors, types, IDs, flush matrix, extruder assignments |
| **Cut/connector info** | Cut planes, connector geometry |
| **Assembly transforms** | Object positions/rotations |
| **Gcode bundles** | Sliced `.gcode.3mf` with embedded gcode, MD5, slice metadata |
| **Thumbnails** | All resolutions and views |
| **Embedded presets** | Process, filament, machine settings preserved through round-trip |
| **Shape/emboss** | BambuStudioShape text embossing configuration |
| **Mesh sharing** | Part-level `mesh_shared` flag |

## Round-trip fidelity

**Unmodified sections are preserved byte-for-byte.** Load and save without changes — every file in the ZIP is identical. Modify specific sections with `mark_modified()` and only those parts regenerate.

Verified on 26 real project files: 3-entry minimal files to 243-entry/40-object/32-plate projects, gcode bundles, AMS multi-material, multi-plate layouts.

## Quick start

```python
from bambu3mf import Bambu3MF

# Load
proj = Bambu3MF.load("my_print.3mf")
print(proj.summary())

# Create from scratch
proj = Bambu3MF.new()
proj.set_setting("layer_height", "0.2")
proj.save("new_project.3mf")
```

## Examples

### Read and change settings

```python
proj = Bambu3MF.load("file.3mf")

print(proj.get_setting("layer_height"))          # "0.2"
print(proj.get_filament_colors())                # ["#FFFFFF", "#000000"]
print(proj.get_filament_types())                 # ["PLA", "PLA"]

proj.set_setting("layer_height", "0.12")
proj.set_setting("sparse_infill_density", "20%")
proj.save("modified.3mf")
```

### Inspect objects and parts

```python
for obj in proj.objects:
    print(f"Object {obj.id}: {obj.name} (extruder={obj.extruder})")
    for part in obj.parts:
        print(f"  Part {part.id}: {part.name} (shared={part.mesh_shared})")
```

### Work with plates

```python
for plate in proj.plates:
    print(f"Plate {plate.plater_id}: bed_type={plate.bed_type}")

proj.plates[0].bed_type = "textured_plate"
proj.plates[0].print_sequence = "by_object"
proj.mark_modified("model_settings")
proj.save("updated.3mf")
```

### Modify mesh data

```python
from bambu3mf import Vertex, Triangle

leaf = proj.sub_models["3D/Objects/object_1.model"][0]
leaf.mesh.vertices = [Vertex(0, 0, 0), Vertex(10, 0, 0), ...]
leaf.mesh.triangles = [Triangle(v1=0, v2=1, v3=2), ...]
proj.mark_modified("3D/Objects/object_1.model")
proj.save("new.3mf")
```

## CLI

```bash
bambu3mf file.3mf                    # summary
bambu3mf file.3mf list-objects       # list objects and parts
bambu3mf file.3mf dump-settings      # print all settings as JSON
bambu3mf file.3mf round-trip out.3mf # load → save (verify fidelity)
```

## Data classes

| Class | Purpose |
|-------|--------------------|
| `Bambu3MF` | Top-level project — `load()`, `new()`, `save()`, `summary()` |
| `ModelObject` | Object with name, extruder, settings, parts, shape_config |
| `Part` | Sub-volume — name, matrix, extruder, mesh_shared |
| `Plate` | Build plate — ID, name, thumbnails, instances, per-plate settings |
| `BuildItem` | Build entry — object reference, transform, printable |
| `ObjectMesh` | Vertex + triangle arrays |
| `Triangle` | Indices + per-triangle painting |
| `SliceInfo` / `SlicePlate` / `SliceFilament` | Slice metadata |
| `ShapeConfig` | BambuStudioShape emboss config |
| `ColorGroup` / `ColorDef` | Materials Extension colors |
| `CutObject` / `CutConnector` | Cut definitions |

## Project structure

```
3MF-Agent-Tools/
├── .claude-plugin/           # Claude Code plugin manifest
│   └── plugin.json
├── skills/                   # Claude skill (agent instructions)
│   └── bambu-3mf/
│       ├── SKILL.md
│       ├── scripts/
│       │   └── bambu3mf.py
│       └── references/
│           └── file-format.md
├── src/                      # PyPI package (standalone library)
│   └── bambu3mf/
│       ├── __init__.py
│       └── bambu3mf.py
├── pyproject.toml
├── README.md
└── LICENSE
```

Two distribution channels, one codebase. The `skills/` tree is for Claude Code plugin users. The `src/` tree is for pip/PyPI.

## How it was built

1. Dissected real `.3mf` files from Bambu Studio 01.08 through 02.00
2. Extracted every constant from BambuStudio C++ source (`bbs_3mf.cpp`)
3. Cross-referenced with [3MF Core](https://github.com/3MFConsortium/spec_core), [Production](https://github.com/3MFConsortium/spec_production), and [Materials](https://github.com/3MFConsortium/spec_materials) specs
4. Every constant verified against 3+ independent sources
5. Byte-perfect round-trip verified on 26 real project files

## License

MIT
