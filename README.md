# 3MF Agent Tools

3MF Agent Tools helps an AI agent read and edit Bambu Studio `.3mf` project files without you having to manually unpack ZIPs, inspect XML, or guess where settings live.

If you are here because you want an agent to help with real print files, start at the top and stop when you have what you need. If you are building tooling or automation, the lower sections go deeper.

It is both:

- a Python library for technical users who want to script against `.3mf` files
- an agent-friendly toolkit for printing enthusiasts who want to say things like “change all plates to textured bed” or “show me what filament settings this project uses”

No third-party Python dependencies. Pure stdlib. Python 3.10+.

## What This Is For

If you mostly care about printing, this project lets an agent help you work inside Bambu Studio project files safely and quickly.

Examples:

- inspect a `.3mf` and explain what is on each plate
- change layer height, infill, wall count, or filament colors
- switch plates from smooth to textured
- compare two project files and summarize the differences
- batch-update a whole folder of projects
- extract mesh, thumbnail, or sliced metadata without opening Bambu Studio

If you are more technical, you can use the same code as a normal Python package or CLI.

## Who This Is For

This README is written for two groups:

1. Printing enthusiasts who are not trying to become 3MF format experts, but want an agent to help them make reliable changes.
2. Technical users who want enough detail to automate, audit, or build tools on top of Bambu `.3mf` projects.

The top half stays practical and plain-language. The lower sections get more technical.

## Quick Start

### Use It With An Agent

If your agent has access to this repo or the packaged skill, you can ask for things like:

- “Open this `.3mf` and tell me what print settings matter most.”
- “Change all plates to textured bed and save a copy.”
- “Show me which objects use which filament.”
- “Compare these two projects and explain the meaningful differences.”
- “Change layer height to `0.16` and keep everything else the same.”

You do not need to know where Bambu stores each setting. The agent can use this library to do the file surgery for you.

### Install

If you only plan to use this through an agent integration, the plugin route is usually the simplest. If you want to script against it yourself, use `pip`.

**pip**

```bash
pip install bambu3mf
```

**Claude Code plugin**

```text
/plugin install bambu-3mf
```

Or add it to your marketplace config:

```json
{
  "name": "bambu-3mf",
  "source": { "source": "github", "repo": "lostprojects/3MF-Agent-Tools" }
}
```

## What You Can Actually Do With It

### Check A File Without Opening Bambu Studio

```bash
bambu3mf file.3mf
bambu3mf file.3mf dump-settings | grep infill
```

### Batch-Change Settings Across Many Projects

```python
from pathlib import Path
from bambu3mf import Bambu3MF

for path in Path(".").glob("*.3mf"):
    proj = Bambu3MF.load(str(path))
    proj.set_setting("layer_height", "0.16")
    proj.set_setting("wall_loops", "3")
    proj.save(str(path))
```

### Switch Every Plate To A Textured Bed

```python
from bambu3mf import Bambu3MF

proj = Bambu3MF.load("big_project.3mf")
for plate in proj.plates:
    plate.bed_type = "textured_plate"
proj.mark_modified("model_settings")
proj.save("big_project.3mf")
```

### Update Filament Colors

```python
from bambu3mf import Bambu3MF

proj = Bambu3MF.load("file.3mf")
colors = proj.get_filament_colors()
colors[0] = "#FF0000"
proj.set_setting("filament_colour", colors)
proj.save("file.3mf")
```

### Audit A Whole Collection

```python
from pathlib import Path
from bambu3mf import Bambu3MF

for path in Path(".").rglob("*.3mf"):
    proj = Bambu3MF.load(str(path))
    layer_height = proj.get_setting("layer_height")
    infill = proj.get_setting("sparse_infill_density")
    colors = proj.get_filament_colors()
    print(f"{path.name}: {layer_height}mm, {infill} infill, {len(colors)} filaments")
```

### Read Mesh Dimensions Without Slicing

```python
from bambu3mf import Bambu3MF

proj = Bambu3MF.load("file.3mf")
for _, objects in proj.sub_models.items():
    for obj in objects:
        if obj.mesh:
            xs = [v.x for v in obj.mesh.vertices]
            ys = [v.y for v in obj.mesh.vertices]
            zs = [v.z for v in obj.mesh.vertices]
            print(f"{obj.name}: {max(xs)-min(xs):.1f} x {max(ys)-min(ys):.1f} x {max(zs)-min(zs):.1f} mm")
```

## What It Handles

| Feature | What that means in practice |
|---------|------------------------------|
| Mesh data | Vertices, triangles, transforms, sub-model files |
| Per-triangle painting | Support paint, seam paint, MMU color paint, fuzzy skin |
| Print settings | Project settings and many object and plate overrides |
| Multi-plate layout | Plate definitions, assignments, thumbnails, plate metadata |
| AMS mapping | Filament colors, types, tray IDs, mapping data |
| Cut and connector info | Cut metadata and connector geometry |
| Assembly transforms | Object placement and assembly view data |
| Gcode bundles | `.gcode.3mf` files with embedded gcode and slice metadata |
| Thumbnails | Plate thumbnails and auxiliary thumbnails |
| Embedded presets | Process, filament, and machine preset files inside the archive |
| Shape and emboss data | BambuStudio shape and text emboss configuration |
| Mesh sharing | Shared sub-mesh references between parts |

## Verified Scope Limits

External verification against the 3MF Core, Materials, and Production specs plus current BambuStudio source shows this library is a practical Bambu project editor, not a full implementation of every 3MF extension.

- Materials support is limited to `m:colorgroup` plus triangle `pid`/`p1`/`p2`/`p3` properties.
- It does not provide first-class read/write support for broader 3MF materials resources such as `basematerials`, `texture2d`, `texture2dgroup`, `multiproperties`, or display-property groups.
- Production support covers the BambuStudio paths used here: `p:UUID`, `p:path`, split sub-model relationships, and gcode relationships.
- It does not implement the Production Alternatives schema such as `pa:alternatives` or `modelresolution`.
- Unknown archive files are preserved when possible, but unknown XML structures are only preserved if the corresponding section is not regenerated.

## What “Round-Trip” Means Here

This part matters, especially if you want an agent to edit files confidently.

The goal of this project is:

- keep Bambu project files usable after inspection and edits
- preserve unmodified sections when possible
- regenerate only the pieces you intentionally changed

What it does well:

- preserves many unmodified payload files directly
- keeps unknown and future files in the archive instead of dropping them
- lets you target only the sections you changed with `mark_modified()`

What it does **not** guarantee:

- a completely binary-identical ZIP archive after save
- every entry in the archive staying byte-for-byte identical after a no-op load/save

In plain English: this is built for safe project editing, not for forensic cloning of the original ZIP file.

## Why This Exists

Bambu Studio `.3mf` files are not just “models in a ZIP.” They usually contain a mix of:

- main model XML
- external object model files
- JSON print settings
- XML model settings
- plate metadata
- thumbnails
- optional gcode bundles
- Bambu-specific metadata and relationships

There was no focused library that made these files easy for an agent to inspect and modify without writing a lot of custom one-off parsing code.

This project exists so an agent can answer normal printing questions and make real edits instead of saying “that file format is proprietary, try opening it manually.”

## Technical Overview

### Project Structure

```text
3MF-Agent-Tools/
├── skills/
│   └── bambu-3mf/
│       ├── SKILL.md
│       ├── scripts/
│       │   └── bambu3mf.py
│       └── references/
│           └── file-format.md
├── src/
│   └── bambu3mf/
│       ├── __init__.py
│       └── bambu3mf.py
├── pyproject.toml
├── README.md
└── LICENSE
```

There are two distribution paths, but one codebase concept:

- `skills/` is for agent/plugin use
- `src/` is for pip/PyPI use

### Core File Layout

A typical Bambu `.3mf` project is a ZIP archive containing files such as:

- `3D/3dmodel.model`
- `3D/Objects/object_N.model`
- `Metadata/project_settings.config`
- `Metadata/model_settings.config`
- `Metadata/slice_info.config`
- `Metadata/plate_N.json`
- `Metadata/cut_information.xml`
- thumbnails and auxiliary files

For a deeper format walkthrough, see [skills/bambu-3mf/references/file-format.md](skills/bambu-3mf/references/file-format.md).

## Python API

### Load, Inspect, Save

```python
from bambu3mf import Bambu3MF

proj = Bambu3MF.load("file.3mf")

print(proj.summary())
print(proj.metadata)
print(proj.get_setting("layer_height"))
print(proj.get_filament_colors())
print(proj.get_filament_types())

proj.set_setting("layer_height", "0.12")
proj.save("output.3mf")
```

### Objects, Parts, Plates

```python
for obj in proj.objects:
    print(f"Object {obj.id}: {obj.name} (extruder={obj.extruder})")
    for part in obj.parts:
        print(f"  Part {part.id}: {part.name}")

for plate in proj.plates:
    print(f"Plate {plate.plater_id}: bed_type={plate.bed_type}")
    plate.bed_type = "textured_plate"
    plate.print_sequence = "by_object"

proj.mark_modified("model_settings")
proj.save("updated.3mf")
```

### Mesh Access

```python
from bambu3mf import Vertex, Triangle

for path, objects in proj.sub_models.items():
    for obj in objects:
        if obj.mesh:
            print(path, len(obj.mesh.vertices), len(obj.mesh.triangles))

leaf = proj.sub_models["3D/Objects/object_1.model"][0]
leaf.mesh.vertices = [Vertex(0, 0, 0), Vertex(10, 0, 0), ...]
leaf.mesh.triangles = [Triangle(v1=0, v2=1, v3=2), ...]
proj.mark_modified("3D/Objects/object_1.model")
proj.save("new.3mf")
```

### Creating A New Project

```python
from bambu3mf import Bambu3MF

proj = Bambu3MF.new()
proj.set_setting("layer_height", "0.2")
proj.set_setting("filament_colour", ["#FFFFFF"])
proj.set_setting("filament_type", ["PLA"])
proj.save("starter.3mf")
```

`new()` gives you a minimal project scaffold. It is a starting point, not a promise that you already have a fully prepared printable job without adding geometry and the settings you need.

## CLI

```bash
bambu3mf file.3mf
bambu3mf file.3mf list-objects
bambu3mf file.3mf dump-settings
bambu3mf file.3mf round-trip out.3mf
```

## Key Data Structures

| Class | Purpose |
|-------|---------|
| `Bambu3MF` | Top-level project object with `load()`, `new()`, `save()`, `summary()` |
| `ModelObject` | Object with name, extruder, settings, parts, and components |
| `Part` | Sub-volume metadata including matrix and per-part overrides |
| `Plate` | Build plate definition and per-plate settings |
| `BuildItem` | Entry in the root `<build>` section |
| `ComponentRef` | Reference from one object to another |
| `ObjectMesh` | Vertex and triangle arrays |
| `Triangle` | Triangle indices and triangle-level paint data |
| `SliceInfo` / `SlicePlate` / `SliceFilament` | Slice and filament metadata |
| `ColorGroup` / `ColorDef` | Materials extension color data |
| `CutObject` / `CutConnector` | Cut information and connectors |
| `ShapeConfig` | BambuStudio shape and emboss configuration |

## How It Was Built

This project was built by combining three kinds of evidence:

1. Real exported `.3mf` files from Bambu Studio.
2. BambuStudio source code, especially `bbs_3mf.cpp`.
3. Official 3MF Core, Production, and Materials specifications.

The project has been tested on a set of real project files ranging from very small archives to large multi-object and multi-plate files.

## License

MIT