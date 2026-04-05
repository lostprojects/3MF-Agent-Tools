# Bambu .3mf file format reference

## File structure

A Bambu `.3mf` is a ZIP archive:

```
file.3mf
├── [Content_Types].xml                    # MIME type mappings
├── _rels/.rels                            # Package relationships
├── 3D/
│   ├── 3dmodel.model                      # Main model: metadata, objects, build items
│   ├── _rels/3dmodel.model.rels           # Links to sub-model files
│   └── Objects/
│       └── object_N.model                 # Mesh data (vertices, triangles, painting)
├── Metadata/
│   ├── project_settings.config            # 700+ slicer settings (JSON)
│   ├── model_settings.config              # Per-object/part overrides, plates, assembly (XML)
│   ├── slice_info.config                  # Slice metadata: time, weight, filament usage (XML)
│   ├── cut_information.xml                # Cut planes and connectors
│   ├── plate_N.json                       # Per-plate bounding box / layout
│   ├── plate_N.png                        # Plate preview thumbnails
│   ├── plate_N.gcode                      # Sliced gcode (in .gcode.3mf only)
│   ├── top_N.png, pick_N.png             # Additional view thumbnails
│   ├── process_settings_N.config          # Embedded process presets
│   ├── filament_settings_N.config         # Embedded filament presets
│   ├── machine_settings_N.config          # Embedded machine presets
│   ├── custom_gcode_per_layer.xml         # Per-layer custom gcode
│   ├── layer_heights_profile.txt          # Variable layer height profile
│   ├── print_profile.config               # Print profile configuration
│   └── _rels/model_settings.config.rels   # Gcode relationships
└── Auxiliaries/
    ├── .thumbnails/                       # 3MF cover images
    └── Model Pictures/                    # User reference images
```

## XML namespaces

| Prefix | URI | Purpose |
|--------|-----|---------|
| *(default)* | `http://schemas.microsoft.com/3dmanufacturing/core/2015/02` | Core 3MF |
| `p:` | `http://schemas.microsoft.com/3dmanufacturing/production/2015/06` | Production extension (UUIDs, sub-models) |
| `BambuStudio:` | `http://schemas.bambulab.com/package/2021` | Bambu-specific extensions |
| `m:` | `http://schemas.microsoft.com/3dmanufacturing/material/2015/02` | Materials/colors |

## Key file details

### project_settings.config (JSON)

Contains all slicer settings as a flat JSON object. 700+ keys covering layer height, speeds, temperatures, infill, walls, support, retraction, etc.

### model_settings.config (XML)

Contains per-object settings overrides, plate definitions with object-to-plate assignments, assembly transforms, and per-plate settings (bed_type, print_sequence, spiral_mode, etc.).

### 3dmodel.model (XML)

Main model file with:
- Metadata elements (Application, BambuStudio version, etc.)
- Object definitions with references to sub-model mesh files via components
- Build items with transforms and printable flags
- Color groups (Materials Extension)

### Objects/object_N.model (XML)

Sub-model files containing actual mesh data:
- Vertices (`<vertex x= y= z=/>`)
- Triangles (`<triangle v1= v2= v3=/>`) with per-triangle painting attributes:
  - `paint_supports` — support painting hex data
  - `paint_seam` — seam painting hex data
  - `paint_color` — MMU color painting hex data
  - `paint_fuzzy_skin` — fuzzy skin painting hex data

### slice_info.config (XML)

Slice metadata after slicing:
- Per-plate: print time, weight, filament usage, gcode path
- Per-filament: color, type, usage amounts
- Printer model and profile information

## Specifications

- [3MF Core Specification](https://github.com/3MFConsortium/spec_core)
- [3MF Production Extension](https://github.com/3MFConsortium/spec_production)
- [3MF Materials Extension](https://github.com/3MFConsortium/spec_materials)
- [BambuStudio source (bbs_3mf.cpp)](https://github.com/bambulab/BambuStudio/blob/main/src/libslic3r/Format/bbs_3mf.cpp)
