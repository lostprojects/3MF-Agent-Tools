"""
bambu3mf.py v2 — Precision Bambu Lab 3MF reader/writer

Reads and writes Bambu Studio .3mf project files for practical inspection and editing:
  - Core 3MF mesh data (vertices, triangles, transforms)
  - Per-triangle painting (support, seam, color/MMU, fuzzy skin)
  - Multi-plate layout with per-plate settings
  - AMS filament assignments and flush matrix
  - Per-object and per-part print setting overrides
  - Cut/connector information
  - Thumbnails and auxiliary files
    - Project settings and other slicer metadata
  - Gcode bundles (gcode.3mf)
  - Assembly/assemble transforms
  - Embedded presets (process, filament, machine settings)
  - Custom gcode per layer, layer heights profile, brim ear points
    - Print profile config

Core namespaces, relationship types, and major attribute names are
verified against BambuStudio source (bbs_3mf.cpp) and the 3MF Core,
Production, and Materials specifications.

v2 changes from v1:
  - Added OBJECT_UUID_SUFFIX2 for mesh sharing (verified: bbs_3mf.cpp:268)
  - Fixed Plate dataclass: per-plate settings (bed_type, print_sequence, etc.)
    now actually parsed from model_settings.config instead of falling through
    to extra_metadata (verified: bbs_3mf.cpp:306-314)
  - Added missing plate attributes: pattern_file, filament_volume_maps,
    other_layers_print_sequence_nums (verified: bbs_3mf.cpp:310,315,321)
  - Added SliceFilament.used_for_support, used_for_object, tray_info_idx,
    group_id, nozzle_diameter, nozzle_volume_type (verified: bbs_3mf.cpp:216-223)
  - Added SlicePlate.extruder_type, nozzle_volume_type, nozzle_types,
    first_layer_time, skipped (verified: bbs_3mf.cpp:330-341)
  - Added embedded presets preservation (process_settings_N, filament_settings_N,
    machine_settings_N) (verified: bbs_3mf.cpp:182-185)
  - Added custom_gcode_per_layer.xml preservation (verified: bbs_3mf.cpp:180)
  - Added layer_heights_profile.txt preservation (verified: bbs_3mf.cpp:175)
  - Added layer_config_ranges.xml preservation (verified: bbs_3mf.cpp:176)
  - Added brim_ear_points.txt preservation (verified: bbs_3mf.cpp:177)
  - Added print_profile.config preservation (verified: bbs_3mf.cpp:169)
  - Added filament_sequence.json preservation (verified: bbs_3mf.cpp:174)
  - Colorgroup export: only emit xmlns:m and m:colorgroup when color_groups
    present (verified: BambuStudio only reads, never writes m: namespace)
  - Main model builder: xmlns:p now conditional on production extension usage,
    matching bbs_3mf.cpp:6807 behavior
  - Thumbnail relationship builder: handles bbl_thumbnail.png for printer
    thumbnails (verified: bbs_3mf.cpp:163)
  - Bambu3MF.new() class method for creating projects from scratch
  - Object lookup by ID uses dict index for O(1) access
  - UUID attribute parsing: handles both p:UUID and p:uuid (verified:
    bbs_3mf.cpp:264-265 — PUUID_ATTR and PUUID_LOWER_ATTR)
  - _xml_escape_attr now escapes single quotes (verified: XML spec)
  - Added Part.mesh_shared field (verified: bbs_3mf.cpp:360)
  - Added ShapeConfig dataclass for BambuStudioShape (verified: bbs_3mf.cpp:393-402)
  - Summary now shows plate bed_type

Usage:
    from bambu3mf import Bambu3MF
    project = Bambu3MF.load("file.3mf")
    project.plates[0].bed_type = "textured_plate"
    project.save("modified.3mf")
"""

import zipfile
import json
import copy
import io
import os
import re
from dataclasses import dataclass, field
from typing import Optional, Any
from xml.etree import ElementTree as ET

# ─── Namespaces ──────────────────────────────────────────────────────────────
# Verified against:
#   1. bbs_3mf.cpp:6805 — xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
#   2. 3MF Core Spec §1.1 — http://schemas.microsoft.com/3dmanufacturing/core/2015/02
#   3. radagast.ca/linux/3mf-file-format.html — confirms core namespace
NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

# Verified against:
#   1. bbs_3mf.cpp:6807 — xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
#   2. 3MF Production Extension §1.1 — same URI
#   3. 3MFConsortium/spec_production on GitHub — same URI
NS_PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"

# Verified against:
#   1. bbs_3mf.cpp:6805 — xmlns:BambuStudio="http://schemas.bambulab.com/package/2021"
#   2. radagast.ca/linux/3mf-file-format.html — confirms BambuStudio namespace
#   3. DeepWiki bambulab/BambuStudio/2.3 — same URI
NS_BAMBU = "http://schemas.bambulab.com/package/2021"

# Verified against:
#   1. 3MF Materials Extension §1.1 — http://schemas.microsoft.com/3dmanufacturing/material/2015/02
#   2. 3MFConsortium/spec_materials on GitHub — same URI
#   3. bbs_3mf.cpp:194 — COLOR_GROUP_TAG = "m:colorgroup" (uses m: prefix)
NS_MATERIAL = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"

# Verified against:
#   1. bbs_3mf.cpp:6557 — xmlns="http://schemas.openxmlformats.org/package/2006/content-types"
#   2. OPC specification — same URI
#   3. 3MF Core Spec §11.1 — references OPC content types
NS_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"

# Verified against:
#   1. bbs_3mf.cpp:6683 — xmlns="http://schemas.openxmlformats.org/package/2006/relationships"
#   2. OPC specification — same URI
#   3. 3MF Core Spec §11.1 — references OPC relationships
NS_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"

# ─── Relationship types ──────────────────────────────────────────────────────
# Verified against:
#   1. bbs_3mf.cpp:6685 — Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
#   2. 3MF Core Spec §11.2 — StartPart relationship type
#   3. 3MF Production Extension — same relationship type for sub-models
REL_3DMODEL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"

# Verified against:
#   1. bbs_3mf.cpp:6691 — Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"
#   2. 3MF Core Spec §11.2 — Thumbnail relationship type
#   3. OPC specification — same URI
REL_THUMBNAIL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"

# Verified against:
#   1. bbs_3mf.cpp:6699 — Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"
#   2. bbs_3mf.cpp:4844 — checked with boost::ends_with(type, "cover-thumbnail-middle")
#   3. radagast.ca/linux/3mf-file-format.html — confirms Bambu thumbnail types
REL_THUMBNAIL_MIDDLE = "http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"

# Verified against:
#   1. bbs_3mf.cpp:6707 — Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-small"
#   2. bbs_3mf.cpp:4846 — checked with boost::ends_with(type, "cover-thumbnail-small")
#   3. radagast.ca/linux/3mf-file-format.html — confirms Bambu thumbnail types
REL_THUMBNAIL_SMALL = "http://schemas.bambulab.com/package/2021/cover-thumbnail-small"

# Verified against:
#   1. bbs_3mf.cpp:8102 — "http://schemas.bambulab.com/package/2021/gcode"
REL_GCODE = "http://schemas.bambulab.com/package/2021/gcode"

# Register namespace prefixes for clean output
ET.register_namespace("", NS_CORE)
ET.register_namespace("p", NS_PRODUCTION)
ET.register_namespace("BambuStudio", NS_BAMBU)
ET.register_namespace("m", NS_MATERIAL)

# ─── Namespace-qualified tag helpers ─────────────────────────────────────────
def _core(tag):   return f"{{{NS_CORE}}}{tag}"
def _prod(tag):   return f"{{{NS_PRODUCTION}}}{tag}"
def _bambu(tag):  return f"{{{NS_BAMBU}}}{tag}"
def _mat(tag):    return f"{{{NS_MATERIAL}}}{tag}"
def _ct(tag):     return f"{{{NS_CONTENT_TYPES}}}{tag}"
def _rel(tag):    return f"{{{NS_RELATIONSHIPS}}}{tag}"

# ─── UUID suffix constants ───────────────────────────────────────────────────
# Verified against bbs_3mf.cpp:267-272 (exact string values)
# Cross-checked with DeepWiki bambulab/BambuStudio/2.3

# bbs_3mf.cpp:267 — used for normal object UUIDs
OBJECT_UUID_SUFFIX = "-61cb-4c03-9d28-80fed5dfa1dc"
# bbs_3mf.cpp:268 — used when mesh is shared between objects (SaveStrategy::ShareMesh)
OBJECT_UUID_SUFFIX2 = "-71cb-4c03-9d28-80fed5dfa1dc"
# bbs_3mf.cpp:269 — used for sub-object (leaf volume) UUIDs
SUB_OBJECT_UUID_SUFFIX = "-81cb-4c03-9d28-80fed5dfa1dc"
# bbs_3mf.cpp:270 — used for component reference UUIDs
COMPONENT_UUID_SUFFIX = "-b206-40ff-9872-83e8017abed1"
# bbs_3mf.cpp:271 — fixed UUID for the <build> element
BUILD_UUID = "2c7c17d8-22b5-4d84-8835-1976022ea369"
# bbs_3mf.cpp:272 — suffix for build item UUIDs
BUILD_UUID_SUFFIX = "-b1ec-4553-aec9-835e5b724bb4"

# ─── Version ─────────────────────────────────────────────────────────────────
# Verified: bbs_3mf.cpp:106,108
VERSION_BBS_3MF = 1
VERSION_BBS_3MF_COMPATIBLE = 2


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Vertex:
    x: float
    y: float
    z: float

@dataclass
class Triangle:
    v1: int
    v2: int
    v3: int
    # Per-triangle painting data (hex strings, empty = unpainted)
    # Verified: bbs_3mf.cpp:293-298
    paint_supports: str = ""    # CUSTOM_SUPPORTS_ATTR
    paint_seam: str = ""        # CUSTOM_SEAM_ATTR
    paint_color: str = ""       # MMU_SEGMENTATION_ATTR — MMU segmentation
    paint_fuzzy_skin: str = ""  # CUSTOM_FUZZY_SKIN_ATTR
    face_property: str = ""     # FACE_PROPERTY_ATTR
    # Material extension (3MF Materials Spec §4.1)
    # Verified: bbs_3mf.cpp:259-263
    pid: Optional[int] = None     # PID_ATTR
    pindex: Optional[int] = None  # PINDEX_ATTR
    p1: Optional[int] = None      # P1_ATTR
    p2: Optional[int] = None      # P2_ATTR
    p3: Optional[int] = None      # P3_ATTR

@dataclass
class MeshStat:
    """Mesh statistics as written by Bambu Studio.
    Verified: bbs_3mf.cpp:362-367"""
    face_count: int = 0           # MESH_STAT_FACE_COUNT
    edges_fixed: int = 0          # MESH_STAT_EDGES_FIXED
    degenerate_facets: int = 0    # MESH_STAT_DEGENERATED_FACETS
    facets_removed: int = 0       # MESH_STAT_FACETS_REMOVED
    facets_reversed: int = 0      # MESH_STAT_FACETS_RESERVED (note: source typo, attr is "facets_reversed")
    backwards_edges: int = 0      # MESH_STAT_BACKWARDS_EDGES

@dataclass
class Part:
    """A part (volume) within an object.
    Verified: bbs_3mf.cpp:304,347-357"""
    id: int
    subtype: str = "normal_part"  # normal_part, modifier_part, support_blocker, support_enforcer
    name: str = ""
    matrix: str = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"  # 4x4 identity
    source_file: str = ""         # SOURCE_FILE_KEY
    source_object_id: str = ""    # SOURCE_OBJECT_ID_KEY
    source_volume_id: str = ""    # SOURCE_VOLUME_ID_KEY
    source_offset_x: str = "0"    # SOURCE_OFFSET_X_KEY
    source_offset_y: str = "0"    # SOURCE_OFFSET_Y_KEY
    source_offset_z: str = "0"    # SOURCE_OFFSET_Z_KEY
    source_in_inches: str = ""    # SOURCE_IN_INCHES_KEY
    source_in_meters: str = ""    # SOURCE_IN_METERS_KEY
    mesh_stat: Optional[MeshStat] = None
    extruder: Optional[str] = None
    # v2: mesh_shared flag (bbs_3mf.cpp:360)
    mesh_shared: Optional[str] = None
    text_info: Optional["TextInfo"] = None
    shape_config: Optional["ShapeConfig"] = None
    # Any additional metadata key/value pairs
    extra_metadata: dict = field(default_factory=dict)

@dataclass
class TextInfo:
    """Embossed/engraved text data on an object.
    Verified: bbs_3mf.cpp:238-255"""
    text: str = ""               # TEXT_ATTR
    font_name: str = ""          # FONT_NAME_ATTR
    font_version: str = ""       # FONT_VERSION_ATTR
    style_name: str = ""         # STYLE_NAME_ATTR
    boldness: str = ""           # BOLDNESS_ATTR
    skew: str = ""               # SKEW_ATTR
    font_index: str = ""         # FONT_INDEX_ATTR
    font_size: str = ""          # FONT_SIZE_ATTR
    thickness: str = ""          # THICKNESS_ATTR
    embeded_depth: str = ""      # EMBEDED_DEPTH_ATTR (note: source has this spelling)
    rotate_angle: str = ""       # ROTATE_ANGLE_ATTR
    text_gap: str = ""           # TEXT_GAP_ATTR
    bold: str = ""               # BOLD_ATTR
    italic: str = ""             # ITALIC_ATTR
    surface_type: str = ""       # SURFACE_TYPE
    surface_text: str = ""       # SURFACE_TEXT_ATTR
    keep_horizontal: str = ""    # KEEP_HORIZONTAL_ATTR
    hit_mesh: str = ""           # HIT_MESH_ATTR
    hit_position: str = ""       # HIT_POSITION_ATTR
    hit_normal: str = ""         # HIT_NORMAL_ATTR

@dataclass
class ShapeConfig:
    """BambuStudioShape / emboss shape configuration.
    Verified: bbs_3mf.cpp:393-402"""
    scale: str = ""              # SHAPE_SCALE_ATTR
    depth: str = ""              # DEPTH_ATTR
    use_surface: str = ""        # USE_SURFACE_ATTR
    unhealed: str = ""           # UNHEALED_ATTR
    transform: str = ""          # TRANSFORM_ATTR
    filepath: str = ""           # SVG_FILE_PATH_ATTR
    filepath3mf: str = ""        # SVG_FILE_PATH_IN_3MF_ATTR
    # Font descriptor fields (bbs_3mf.cpp:372-390)
    style_name: str = ""
    font_descriptor: str = ""
    font_descriptor_type: str = ""
    char_gap: str = ""
    line_gap: str = ""
    line_height: str = ""
    boldness: str = ""
    skew: str = ""
    per_glyph: str = ""
    horizontal: str = ""
    vertical: str = ""
    collection: str = ""
    family: str = ""
    face_name: str = ""
    style: str = ""
    weight: str = ""
    svg_file_data: bytes = b""
    # Raw attributes for forward-compat
    extra: dict = field(default_factory=dict)

@dataclass
class ObjectMesh:
    """Mesh data for a single object model file."""
    vertices: list = field(default_factory=list)   # list of Vertex
    triangles: list = field(default_factory=list)   # list of Triangle

@dataclass
class ModelObject:
    """An object in the 3MF project."""
    id: int
    uuid: str = ""
    backup_id: Optional[int] = None
    type: str = "model"         # model, support, other
    name: str = ""
    extruder: Optional[str] = None
    # Per-object setting overrides — ORDERED list of (key, value) to preserve file order
    settings: list = field(default_factory=list)     # list of (key, value) tuples
    # Parts within this object
    parts: list = field(default_factory=list)       # list of Part
    # If this is a component assembly
    components: list = field(default_factory=list)  # list of ComponentRef
    # If this has its own mesh (leaf objects in sub-model files)
    mesh: Optional[ObjectMesh] = None
    # Text info if any
    text_info: Optional[TextInfo] = None
    # v2: Shape config (BambuStudioShape)
    shape_config: Optional[ShapeConfig] = None
    # face_count metadata (on the object element itself)
    face_count: Optional[int] = None
    # Raw extra metadata
    extra_metadata: dict = field(default_factory=dict)
    # Track: was the name present on the <object> element in 3dmodel.model?
    _name_in_main_model: bool = False

@dataclass
class ComponentRef:
    """Reference from a parent object to a child via component.
    Verified: bbs_3mf.cpp:264,266,287-288"""
    objectid: int
    path: str = ""              # p:path (PPATH_ATTR) to external model file
    uuid: str = ""              # p:UUID (PUUID_ATTR)
    transform: str = "1 0 0 0 1 0 0 0 1 0 0 0"  # 3x4 affine (TRANSFORM_ATTR)

@dataclass
class BuildItem:
    """An item in the <build> section.
    Verified: bbs_3mf.cpp:287-291"""
    objectid: int               # OBJECTID_ATTR
    uuid: str = ""              # PUUID_ATTR
    path: str = ""              # PPATH_ATTR
    transform: str = "1 0 0 0 1 0 0 0 1 0 0 0"  # TRANSFORM_ATTR
    printable: str = "1"        # PRINTABLE_ATTR

@dataclass
class ModelInstance:
    """Object placement on a plate.
    Verified: bbs_3mf.cpp:323-325"""
    object_id: int              # OBJECT_ID_ATTR
    instance_id: int = 0        # INSTANCEID_ATTR
    identify_id: int = 0        # IDENTIFYID_ATTR

@dataclass
class Plate:
    """A build plate definition.
    Verified: bbs_3mf.cpp:305-322,326-327"""
    plater_id: int = 1                    # PLATERID_ATTR
    plater_name: str = ""                 # PLATER_NAME_ATTR
    locked: str = "false"                 # LOCK_ATTR
    thumbnail_file: str = ""              # THUMBNAIL_FILE_ATTR
    thumbnail_no_light_file: str = ""     # NO_LIGHT_THUMBNAIL_FILE_ATTR
    top_file: str = ""                    # TOP_FILE_ATTR
    pick_file: str = ""                   # PICK_FILE_ATTR
    gcode_file: str = ""                  # GCODE_FILE_ATTR
    pattern_file: str = ""                # PATTERN_FILE_ATTR (v2: was missing)
    pattern_bbox_file: str = ""           # PATTERN_BBOX_FILE_ATTR
    instances: list = field(default_factory=list)     # list of ModelInstance
    # v2: Per-plate settings — now properly parsed into named fields
    # Verified: bbs_3mf.cpp:306-315
    bed_type: str = ""                    # BED_TYPE_ATTR
    print_sequence: str = ""              # PRINT_SEQUENCE_ATTR
    first_layer_print_sequence: str = ""  # FIRST_LAYER_PRINT_SEQUENCE_ATTR
    other_layers_print_sequence: str = "" # OTHER_LAYERS_PRINT_SEQUENCE_ATTR
    other_layers_print_sequence_nums: str = ""  # OTHER_LAYERS_PRINT_SEQUENCE_NUMS_ATTR (v2)
    spiral_mode: str = ""                 # SPIRAL_VASE_MODE
    filament_map_mode: str = ""           # FILAMENT_MAP_MODE_ATTR
    filament_maps: str = ""               # FILAMENT_MAP_ATTR
    limit_filament_maps: str = ""         # LIMIT_FILAMENT_MAP_ATTR
    filament_volume_maps: str = ""        # FILAMENT_VOL_MAP_ATTR (v2)
    # Extra plate metadata for forward-compat
    extra_metadata: dict = field(default_factory=dict)

@dataclass
class AssembleItem:
    """Assembly view transform for an object.
    Verified: bbs_3mf.cpp:232-233,289-290,323-325"""
    object_id: int              # OBJECT_ID_ATTR
    instance_id: int = 0        # INSTANCEID_ATTR
    transform: str = "1 0 0 0 1 0 0 0 1 0 0 0"  # TRANSFORM_ATTR
    offset: str = "0 0 0"       # OFFSET_ATTR

@dataclass
class CutConnector:
    """Cut connector geometry.
    Verified: bbs_3mf.cpp cut_information.xml handling"""
    volume_id: int = 0
    type: int = 0
    radius: float = 0.0
    height: float = 0.0
    r_tolerance: float = 0.0
    h_tolerance: float = 0.0

@dataclass
class CutObject:
    object_id: int
    cut_id: int = 0
    check_sum: int = 1
    connectors_cnt: int = 0
    connectors: list = field(default_factory=list)  # list of CutConnector

@dataclass
class ColorDef:
    """A color in a color group (Materials Extension §4.1.3).
    Verified: bbs_3mf.cpp:195 — COLOR_TAG = "m:color" """
    color: str = "#FFFFFFFF"    # #RRGGBBAA

@dataclass
class ColorGroup:
    """Color group (Materials Extension §4.1.2).
    Verified: bbs_3mf.cpp:194 — COLOR_GROUP_TAG = "m:colorgroup" """
    id: int
    colors: list = field(default_factory=list)  # list of ColorDef

@dataclass
class SliceFilament:
    """Filament usage info in slice_info.config.
    Verified: bbs_3mf.cpp:211-223"""
    id: int = 1                              # FILAMENT_ID_TAG
    type: str = ""                           # FILAMENT_TYPE_TAG
    color: str = ""                          # FILAMENT_COLOR_TAG
    used_m: str = ""                         # FILAMENT_USED_M_TAG
    used_g: str = ""                         # FILAMENT_USED_G_TAG
    # v2: additional filament attrs from bbs_3mf.cpp:216-223
    used_for_support: str = ""               # FILAMENT_USED_FOR_SUPPORT
    used_for_object: str = ""                # FILAMENT_USED_FOR_OBJECT
    tray_info_idx: str = ""                  # FILAMENT_TRAY_INFO_ID_TAG
    group_id: str = ""                       # FILAMENT_NOZZLE_GROUP_ID_TAG
    nozzle_diameter: str = ""                # FILAMENT_NOZZLE_DIAMETER_TAG
    nozzle_volume_type: str = ""             # FILAMENT_NOZZLE_VOLUME_TYPE_TAG

@dataclass
class SlicePlate:
    """Per-plate slice info from slice_info.config.
    Verified: bbs_3mf.cpp:328-341"""
    index: int = 1                           # PLATE_IDX_ATTR
    printer_model_id: str = ""               # PRINTER_MODEL_ID_ATTR
    nozzle_diameters: str = ""               # NOZZLE_DIAMETERS_ATTR
    timelapse_type: str = "0"                # TIMELAPSE_TYPE_ATTR
    prediction: str = ""                     # SLICE_PREDICTION_ATTR
    weight: str = ""                         # SLICE_WEIGHT_ATTR
    outside: str = "false"                   # OUTSIDE_ATTR
    support_used: str = "false"              # SUPPORT_USED_ATTR
    label_object_enabled: str = "false"      # LABEL_OBJECT_ENABLED_ATTR
    # v2: additional slice plate attrs from bbs_3mf.cpp:330-341
    extruder_type: str = ""                  # EXTRUDER_TYPE_ATTR
    nozzle_volume_type: str = ""             # NOZZLE_VOLUME_TYPE_ATTR
    nozzle_types: str = ""                   # NOZZLE_TYPE_ATTR
    first_layer_time: str = ""               # FIRST_LAYER_TIME_ATTR
    skipped: str = ""                        # SKIPPED_ATTR
    filaments: list = field(default_factory=list)   # list of SliceFilament
    objects: list = field(default_factory=list)      # list of dicts
    warnings: list = field(default_factory=list)     # list of dicts
    layer_filament_lists: list = field(default_factory=list)  # list of dicts
    extra: dict = field(default_factory=dict)

@dataclass
class SliceInfo:
    """Contents of slice_info.config."""
    client_type: str = "slicer"
    client_version: str = ""
    plates: list = field(default_factory=list)       # list of SlicePlate

@dataclass
class PatternBBox:
    """Contents of plate_N.json."""
    raw: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class Bambu3MF:
    """
    Reader/writer for Bambu Studio .3mf project files focused on safe inspection
    and editing while preserving unmodified payloads when possible.

    Attributes:
        metadata        dict of model-level metadata (Application, Title, etc.)
        objects         list[ModelObject] — all objects in the project
        build_items     list[BuildItem] — the <build> section entries
        build_uuid      str — UUID of the build element
        plates          list[Plate] — build plate definitions
        assemble_items  list[AssembleItem] — assembly view data
        cut_objects     list[CutObject] — cut/split information
        color_groups    list[ColorGroup] — Materials Extension colors
        project_settings  dict — full project_settings.config (JSON)
        slice_info      SliceInfo — slice_info.config data
        pattern_bboxes  dict[int, dict] — plate_N.json data
        thumbnails      dict[str, bytes] — path → PNG data for all thumbnails
        auxiliary_files dict[str, bytes] — Auxiliaries/ contents
        gcode_files     dict[str, bytes] — plate gcode data
        sub_models      dict[str, list[ModelObject]] — external model file parsed objects
        raw_files       dict[str, bytes] — all other files preserved verbatim
    """

    def __init__(self):
        self.metadata: dict[str, str] = {}
        self.objects: list[ModelObject] = []
        self.build_items: list[BuildItem] = []
        self.build_uuid: str = BUILD_UUID
        self.plates: list[Plate] = []
        self.assemble_items: list[AssembleItem] = []
        self.cut_objects: list[CutObject] = []
        self.color_groups: list[ColorGroup] = []
        self.project_settings: dict[str, Any] = {}
        self._project_settings_raw: Optional[str] = None  # preserve original JSON formatting
        self.slice_info: Optional[SliceInfo] = None
        self.pattern_bboxes: dict[int, dict] = {}
        self._pattern_bbox_raw: dict[int, bytes] = {}   # preserve original JSON bytes
        self.thumbnails: dict[str, bytes] = {}
        self.auxiliary_files: dict[str, bytes] = {}
        self.gcode_files: dict[str, bytes] = {}
        self.sub_models: dict[str, list[ModelObject]] = {}  # path → list of leaf objects
        self.raw_files: dict[str, bytes] = {}
        # v2: dict index for O(1) object lookup
        self._obj_index: dict[int, ModelObject] = {}
        # Internal: preserve original bytes for round-trip fidelity
        self._content_types_xml: Optional[bytes] = None
        self._rels_xml: Optional[bytes] = None
        self._model_rels_xml: Optional[bytes] = None
        self._model_config_rels_xml: Optional[bytes] = None
        self._main_model_raw: Optional[bytes] = None
        self._sub_model_raw: dict[str, bytes] = {}     # path → raw bytes
        self._model_settings_raw: Optional[bytes] = None
        self._slice_info_raw: Optional[bytes] = None
        self._cut_info_raw: Optional[bytes] = None
        self._modified_sections: set = set()    # track which sections were modified

    # ─── FACTORY ─────────────────────────────────────────────────────────────

    @classmethod
    def new(cls, application: str = "BambuStudio-02.01.00.59") -> "Bambu3MF":
        """Create a new empty Bambu 3MF project from scratch.

        This bootstraps the minimum viable structure so you can add objects,
        set settings, and save without needing a template file.
        """
        proj = cls()
        proj.metadata = {
            "Application": application,
            "BambuStudio:3mfVersion": str(VERSION_BBS_3MF),
        }
        proj.project_settings = {}
        # Default plate
        proj.plates.append(Plate(plater_id=1, plater_name=""))
        # Mark everything as needing generation
        proj._modified_sections = {"main_model", "model_settings"}
        return proj

    # ─── LOAD ────────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str) -> "Bambu3MF":
        """Load a Bambu .3mf file."""
        proj = cls()
        with zipfile.ZipFile(path, "r") as z:
            names = set(z.namelist())

            # 1) Preserve structural XML
            for key, attr in [
                ("[Content_Types].xml", "_content_types_xml"),
                ("_rels/.rels", "_rels_xml"),
                ("3D/_rels/3dmodel.model.rels", "_model_rels_xml"),
                ("Metadata/_rels/model_settings.config.rels", "_model_config_rels_xml"),
            ]:
                if key in names:
                    setattr(proj, attr, z.read(key))

            # 2) Parse main model — preserve raw for round-trip
            if "3D/3dmodel.model" in names:
                raw = z.read("3D/3dmodel.model")
                proj._main_model_raw = raw
                proj._parse_main_model(raw)

            # 3) Parse sub-model files (3D/Objects/*.model) — preserve raw
            for name in sorted(names):
                if name.startswith("3D/Objects/") and name.endswith(".model"):
                    raw = z.read(name)
                    proj._sub_model_raw[name] = raw
                    proj._parse_sub_model(name, raw)

            # 4) Parse model_settings.config — preserve raw
            if "Metadata/model_settings.config" in names:
                raw = z.read("Metadata/model_settings.config")
                proj._model_settings_raw = raw
                proj._parse_model_settings(raw)
                proj._load_embedded_shape_files(z, names)

            # 5) Parse project_settings.config (JSON) — preserve raw formatting when possible
            if "Metadata/project_settings.config" in names:
                raw = z.read("Metadata/project_settings.config").decode("utf-8")
                proj._project_settings_raw = raw
                try:
                    proj.project_settings = json.loads(raw)
                except json.JSONDecodeError:
                    proj.project_settings = {"_raw": raw}

            # 6) Parse slice_info.config — preserve raw
            if "Metadata/slice_info.config" in names:
                raw = z.read("Metadata/slice_info.config")
                proj._slice_info_raw = raw
                proj._parse_slice_info(raw)

            # 7) Parse cut_information.xml — preserve raw
            if "Metadata/cut_information.xml" in names:
                raw = z.read("Metadata/cut_information.xml")
                proj._cut_info_raw = raw
                proj._parse_cut_info(raw)

            # 8) Parse plate JSON files — preserve raw
            for name in sorted(names):
                m = re.match(r"Metadata/plate_(\d+)\.json$", name)
                if m:
                    plate_idx = int(m.group(1))
                    raw = z.read(name)
                    proj._pattern_bbox_raw[plate_idx] = raw
                    try:
                        proj.pattern_bboxes[plate_idx] = json.loads(raw)
                    except json.JSONDecodeError:
                        pass

            # 9) Collect thumbnails
            for name in sorted(names):
                if name.endswith(".png") and (
                    name.startswith("Metadata/") or
                    name.startswith("Auxiliaries/.thumbnails/")
                ):
                    proj.thumbnails[name] = z.read(name)

            # 10) Collect auxiliary files
            for name in sorted(names):
                if name.startswith("Auxiliaries/") and not name.startswith("Auxiliaries/.thumbnails/"):
                    proj.auxiliary_files[name] = z.read(name)

            # 11) Collect gcode
            for name in sorted(names):
                if name.endswith(".gcode"):
                    proj.gcode_files[name] = z.read(name)
                elif name.endswith(".gcode.md5"):
                    proj.raw_files[name] = z.read(name)

            # 12) Preserve everything else not already parsed
            #     This captures: embedded presets, custom gcode per layer,
            #     layer heights profile, layer config ranges, brim ear points,
            #     print profile config, filament sequence, and any future files.
            parsed = {
                "[Content_Types].xml", "_rels/.rels",
                "3D/3dmodel.model", "3D/_rels/3dmodel.model.rels",
                "Metadata/model_settings.config", "Metadata/project_settings.config",
                "Metadata/slice_info.config", "Metadata/cut_information.xml",
                "Metadata/_rels/model_settings.config.rels",
            }
            for name in names:
                if (name not in parsed and
                    not name.startswith("3D/Objects/") and
                    not name.endswith(".png") and
                    not name.startswith("Auxiliaries/") and
                    not name.endswith(".gcode") and
                    not name.endswith(".gcode.md5") and
                    not re.match(r"Metadata/plate_\d+\.json$", name) and
                    name not in parsed):
                    proj.raw_files[name] = z.read(name)

        # Build object index
        proj._rebuild_obj_index()
        return proj

    # ─── SAVE ────────────────────────────────────────────────────────────────

    def save(self, path: str):
        """Write the project back to a .3mf file."""
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            # 1) [Content_Types].xml
            z.writestr("[Content_Types].xml",
                       self._content_types_xml.decode("utf-8") if self._content_types_xml
                       else self._build_content_types())

            # 2) _rels/.rels
            z.writestr("_rels/.rels",
                       self._rels_xml.decode("utf-8") if self._rels_xml
                       else self._build_rels())

            # 3) Main model — use raw if unmodified
            if self._main_model_raw and "main_model" not in self._modified_sections:
                z.writestr("3D/3dmodel.model", self._main_model_raw)
            else:
                z.writestr("3D/3dmodel.model", self._build_main_model())

            # 4) Model rels
            if self._model_rels_xml:
                z.writestr("3D/_rels/3dmodel.model.rels",
                           self._model_rels_xml.decode("utf-8"))
            elif self.sub_models:
                z.writestr("3D/_rels/3dmodel.model.rels",
                           self._build_model_rels())

            # 5) Sub-model files — use raw if unmodified
            for model_path, leaf_objects in self.sub_models.items():
                if model_path in self._sub_model_raw and model_path not in self._modified_sections:
                    z.writestr(model_path, self._sub_model_raw[model_path])
                else:
                    z.writestr(model_path, self._build_sub_model(model_path, leaf_objects))

            # 6) model_settings.config — use raw if unmodified, skip if never existed
            if self._model_settings_raw and "model_settings" not in self._modified_sections:
                z.writestr("Metadata/model_settings.config", self._model_settings_raw)
            elif self._model_settings_raw or "model_settings" in self._modified_sections or self.plates:
                z.writestr("Metadata/model_settings.config",
                           self._build_model_settings())

            # 7) project_settings.config — use original raw bytes when unmodified
            if self.project_settings:
                if self._project_settings_raw is not None:
                    try:
                        orig = json.loads(self._project_settings_raw)
                        if orig == self.project_settings:
                            z.writestr("Metadata/project_settings.config", self._project_settings_raw)
                        else:
                            z.writestr("Metadata/project_settings.config",
                                       json.dumps(self.project_settings, indent=4, ensure_ascii=False))
                    except (json.JSONDecodeError, TypeError):
                        z.writestr("Metadata/project_settings.config", self._project_settings_raw)
                else:
                    raw = self.project_settings.get("_raw")
                    if raw:
                        z.writestr("Metadata/project_settings.config", raw)
                    else:
                        z.writestr("Metadata/project_settings.config",
                                   json.dumps(self.project_settings, indent=4, ensure_ascii=False))

            # 8) slice_info.config — use raw if unmodified
            if self.slice_info:
                if self._slice_info_raw and "slice_info" not in self._modified_sections:
                    z.writestr("Metadata/slice_info.config", self._slice_info_raw)
                else:
                    z.writestr("Metadata/slice_info.config",
                               self._build_slice_info())

            # 9) cut_information.xml — use raw if unmodified
            if self.cut_objects:
                if self._cut_info_raw and "cut_info" not in self._modified_sections:
                    z.writestr("Metadata/cut_information.xml", self._cut_info_raw)
                else:
                    z.writestr("Metadata/cut_information.xml",
                               self._build_cut_info())

            # 10) model_settings.config.rels
            if self._model_config_rels_xml:
                z.writestr("Metadata/_rels/model_settings.config.rels",
                           self._model_config_rels_xml.decode("utf-8"))
            elif self.gcode_files:
                z.writestr("Metadata/_rels/model_settings.config.rels",
                           self._build_model_config_rels())

            # 11) Plate JSON — use raw if unmodified
            for idx, data in self.pattern_bboxes.items():
                if idx in self._pattern_bbox_raw:
                    try:
                        orig = json.loads(self._pattern_bbox_raw[idx])
                        if orig == data:
                            z.writestr(f"Metadata/plate_{idx}.json", self._pattern_bbox_raw[idx])
                            continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                z.writestr(f"Metadata/plate_{idx}.json",
                           json.dumps(data, ensure_ascii=False))

            # 12) Thumbnails
            for name, data in self.thumbnails.items():
                z.writestr(name, data)

            # 13) Auxiliary files
            for name, data in self.auxiliary_files.items():
                z.writestr(name, data)

            # 14) Gcode
            for name, data in self.gcode_files.items():
                z.writestr(name, data)

            shape_svg_files = self._collect_shape_svg_files()

            # 15) Everything else (includes embedded presets, custom gcode,
            #     layer heights, brim points, print profile, filament sequence, etc.)
            for name, data in self.raw_files.items():
                if name in shape_svg_files:
                    continue
                z.writestr(name, data)

            for name, data in shape_svg_files.items():
                z.writestr(name, data)

    # ─── PARSERS ─────────────────────────────────────────────────────────────

    def _parse_main_model(self, data: bytes):
        """Parse 3D/3dmodel.model — metadata, objects, components, build items."""
        root = ET.fromstring(data)

        # Metadata
        for meta in root.findall(_core("metadata")):
            name = meta.get("name", "")
            value = meta.text or meta.get("value", "")
            if name:
                self.metadata[name] = value

        # Color groups
        for cg in root.iter(_mat("colorgroup")):
            gid = int(cg.get("id", 0))
            colors = []
            for c in cg.findall(_mat("color")):
                colors.append(ColorDef(color=c.get("color", "#FFFFFFFF")))
            self.color_groups.append(ColorGroup(id=gid, colors=colors))

        # Objects
        resources = root.find(_core("resources"))
        if resources is not None:
            for obj_el in resources.findall(_core("object")):
                _has_name = obj_el.get("name") is not None
                # v2: handle both p:UUID and p:uuid (bbs_3mf.cpp:264-265)
                obj = ModelObject(
                    id=int(obj_el.get("id", 0)),
                    uuid=obj_el.get(f"{{{NS_PRODUCTION}}}UUID",
                                    obj_el.get(f"{{{NS_PRODUCTION}}}uuid", "")),
                    backup_id=self._extract_uuid_seed(
                        obj_el.get(f"{{{NS_PRODUCTION}}}UUID",
                                   obj_el.get(f"{{{NS_PRODUCTION}}}uuid", "")),
                        OBJECT_UUID_SUFFIX,
                        OBJECT_UUID_SUFFIX2,
                    ),
                    type=obj_el.get("type", "model"),
                    name=obj_el.get("name", ""),
                    _name_in_main_model=_has_name,
                )
                # pid/pindex for material references
                if obj_el.get("pid"):
                    obj.extra_metadata["pid"] = obj_el.get("pid")
                if obj_el.get("pindex"):
                    obj.extra_metadata["pindex"] = obj_el.get("pindex")

                # Components
                comps_el = obj_el.find(_core("components"))
                if comps_el is not None:
                    for comp in comps_el.findall(_core("component")):
                        ref = ComponentRef(
                            objectid=int(comp.get("objectid", 0)),
                            path=comp.get(f"{{{NS_PRODUCTION}}}path", ""),
                            uuid=comp.get(f"{{{NS_PRODUCTION}}}UUID",
                                          comp.get(f"{{{NS_PRODUCTION}}}uuid", "")),
                            transform=comp.get("transform", "1 0 0 0 1 0 0 0 1 0 0 0"),
                        )
                        obj.components.append(ref)

                # Inline mesh (rare in main model for Bambu files, but handle it)
                mesh_el = obj_el.find(_core("mesh"))
                if mesh_el is not None:
                    obj.mesh = self._parse_mesh(mesh_el)

                self.objects.append(obj)

        # Build items
        build = root.find(_core("build"))
        if build is not None:
            self.build_uuid = build.get(f"{{{NS_PRODUCTION}}}UUID",
                                        build.get(f"{{{NS_PRODUCTION}}}uuid", BUILD_UUID))
            for item in build.findall(_core("item")):
                bi = BuildItem(
                    objectid=int(item.get("objectid", 0)),
                    uuid=item.get(f"{{{NS_PRODUCTION}}}UUID",
                                  item.get(f"{{{NS_PRODUCTION}}}uuid", "")),
                    path=item.get(f"{{{NS_PRODUCTION}}}path", ""),
                    transform=item.get("transform", "1 0 0 0 1 0 0 0 1 0 0 0"),
                    printable=item.get("printable", "1"),
                )
                self.build_items.append(bi)

    def _parse_mesh(self, mesh_el) -> ObjectMesh:
        """Parse a <mesh> element into vertices and triangles."""
        om = ObjectMesh()

        verts_el = mesh_el.find(_core("vertices"))
        if verts_el is not None:
            for v in verts_el.findall(_core("vertex")):
                om.vertices.append(Vertex(
                    x=float(v.get("x", 0)),
                    y=float(v.get("y", 0)),
                    z=float(v.get("z", 0)),
                ))

        tris_el = mesh_el.find(_core("triangles"))
        if tris_el is not None:
            for t in tris_el.findall(_core("triangle")):
                tri = Triangle(
                    v1=int(t.get("v1", 0)),
                    v2=int(t.get("v2", 0)),
                    v3=int(t.get("v3", 0)),
                    paint_supports=t.get("paint_supports", ""),
                    paint_seam=t.get("paint_seam", ""),
                    paint_color=t.get("paint_color", ""),
                    paint_fuzzy_skin=t.get("paint_fuzzy_skin", ""),
                    face_property=t.get("face_property", ""),
                )
                if t.get("pid") is not None:
                    tri.pid = int(t.get("pid"))
                if t.get("pindex") is not None:
                    tri.pindex = int(t.get("pindex"))
                if t.get("p1") is not None:
                    tri.p1 = int(t.get("p1"))
                if t.get("p2") is not None:
                    tri.p2 = int(t.get("p2"))
                if t.get("p3") is not None:
                    tri.p3 = int(t.get("p3"))
                om.triangles.append(tri)

        return om

    def _parse_sub_model(self, model_path: str, data: bytes):
        """Parse an external sub-model file (3D/Objects/*.model)."""
        root = ET.fromstring(data)
        resources = root.find(_core("resources"))
        leaf_objects = []
        if resources is not None:
            for obj_el in resources.findall(_core("object")):
                obj = ModelObject(
                    id=int(obj_el.get("id", 0)),
                    uuid=obj_el.get(f"{{{NS_PRODUCTION}}}UUID",
                                    obj_el.get(f"{{{NS_PRODUCTION}}}uuid", "")),
                    backup_id=self._extract_uuid_seed(
                        obj_el.get(f"{{{NS_PRODUCTION}}}UUID",
                                   obj_el.get(f"{{{NS_PRODUCTION}}}uuid", "")),
                        SUB_OBJECT_UUID_SUFFIX,
                    ),
                    type=obj_el.get("type", "model"),
                )
                mesh_el = obj_el.find(_core("mesh"))
                if mesh_el is not None:
                    obj.mesh = self._parse_mesh(mesh_el)
                leaf_objects.append(obj)
        self.sub_models[model_path] = leaf_objects

    def _parse_model_settings(self, data: bytes):
        """Parse Metadata/model_settings.config — per-object settings, plates, assembly."""
        root = ET.fromstring(data)

        # Objects
        for obj_el in root.findall("object"):
            obj_id = int(obj_el.get("id", 0))
            obj = self._find_object(obj_id)
            if not obj:
                obj = ModelObject(id=obj_id)
                self.objects.append(obj)
                self._obj_index[obj_id] = obj

            # Object-level metadata — preserve exact ordering
            for meta in obj_el.findall("metadata"):
                key = meta.get("key", "")
                value = meta.get("value", "")
                if key == "name":
                    obj.name = value
                elif key == "extruder":
                    obj.extruder = value
                elif key:
                    obj.settings.append((key, value))
                # face_count is a special attribute-only metadata (no key attr)
                fc = meta.get("face_count")
                if fc is not None:
                    obj.face_count = int(fc)

            # Parts
            for part_el in obj_el.findall("part"):
                part = Part(
                    id=int(part_el.get("id", 0)),
                    subtype=part_el.get("subtype", "normal_part"),
                )
                for meta in part_el.findall("metadata"):
                    key = meta.get("key", "")
                    value = meta.get("value", "")
                    if key == "name":
                        part.name = value
                    elif key == "matrix":
                        part.matrix = value
                    elif key == "source_file":
                        part.source_file = value
                    elif key == "source_object_id":
                        part.source_object_id = value
                    elif key == "source_volume_id":
                        part.source_volume_id = value
                    elif key == "source_offset_x":
                        part.source_offset_x = value
                    elif key == "source_offset_y":
                        part.source_offset_y = value
                    elif key == "source_offset_z":
                        part.source_offset_z = value
                    elif key == "source_in_inches":
                        part.source_in_inches = value
                    elif key == "source_in_meters":
                        part.source_in_meters = value
                    elif key == "extruder":
                        part.extruder = value
                    elif key == "mesh_shared":
                        part.mesh_shared = value
                    elif key:
                        part.extra_metadata[key] = value

                for child in list(part_el):
                    if child.tag == "text_info":
                        text_info = TextInfo(
                            text=child.get("text", ""),
                            font_name=child.get("font_name", ""),
                            font_version=child.get("font_version", ""),
                            style_name=child.get("style_name", ""),
                            boldness=child.get("boldness", ""),
                            skew=child.get("skew", ""),
                            font_index=child.get("font_index", ""),
                            font_size=child.get("font_size", ""),
                            thickness=child.get("thickness", ""),
                            embeded_depth=child.get("embeded_depth", ""),
                            rotate_angle=child.get("rotate_angle", ""),
                            text_gap=child.get("text_gap", ""),
                            bold=child.get("bold", ""),
                            italic=child.get("italic", ""),
                            surface_type=child.get("surface_type", ""),
                            surface_text=child.get("surface_text", ""),
                            keep_horizontal=child.get("keep_horizontal", ""),
                            hit_mesh=child.get("hit_mesh", ""),
                            hit_position=child.get("hit_position", ""),
                            hit_normal=child.get("hit_normal", ""),
                        )
                        part.text_info = text_info
                        if obj.text_info is None:
                            obj.text_info = text_info
                    elif child.tag in {"BambuStudioShape", "slic3rpe:shape"}:
                        known = {
                            "scale", "depth", "use_surface", "unhealed", "transform", "filepath", "filepath3mf",
                            "style_name", "font_descriptor", "font_descriptor_type", "char_gap", "line_gap",
                            "line_height", "boldness", "skew", "per_glyph", "horizontal", "vertical",
                            "collection", "family", "face_name", "style", "weight",
                        }
                        shape_config = ShapeConfig(
                            scale=child.get("scale", ""),
                            depth=child.get("depth", ""),
                            use_surface=child.get("use_surface", ""),
                            unhealed=child.get("unhealed", ""),
                            transform=child.get("transform", ""),
                            filepath=child.get("filepath", ""),
                            filepath3mf=child.get("filepath3mf", ""),
                            style_name=child.get("style_name", ""),
                            font_descriptor=child.get("font_descriptor", ""),
                            font_descriptor_type=child.get("font_descriptor_type", ""),
                            char_gap=child.get("char_gap", ""),
                            line_gap=child.get("line_gap", ""),
                            line_height=child.get("line_height", ""),
                            boldness=child.get("boldness", ""),
                            skew=child.get("skew", ""),
                            per_glyph=child.get("per_glyph", ""),
                            horizontal=child.get("horizontal", ""),
                            vertical=child.get("vertical", ""),
                            collection=child.get("collection", ""),
                            family=child.get("family", ""),
                            face_name=child.get("face_name", ""),
                            style=child.get("style", ""),
                            weight=child.get("weight", ""),
                            extra={k: v for k, v in child.attrib.items() if k not in known},
                        )
                        part.shape_config = shape_config
                        if obj.shape_config is None:
                            obj.shape_config = shape_config

                # mesh_stat
                ms_el = part_el.find("mesh_stat")
                if ms_el is not None:
                    part.mesh_stat = MeshStat(
                        face_count=int(ms_el.get("face_count", 0)),
                        edges_fixed=int(ms_el.get("edges_fixed", 0)),
                        degenerate_facets=int(ms_el.get("degenerate_facets", 0)),
                        facets_removed=int(ms_el.get("facets_removed", 0)),
                        facets_reversed=int(ms_el.get("facets_reversed", 0)),
                        backwards_edges=int(ms_el.get("backwards_edges", 0)),
                    )
                obj.parts.append(part)

        # Plates — v2: properly parse per-plate settings into named fields
        for plate_el in root.findall("plate"):
            plate = Plate()
            for meta in plate_el.findall("metadata"):
                key = meta.get("key", "")
                value = meta.get("value", "")
                if key == "plater_id":
                    plate.plater_id = int(value) if value else 1
                elif key == "plater_name":
                    plate.plater_name = value
                elif key == "locked":
                    plate.locked = value
                elif key == "thumbnail_file":
                    plate.thumbnail_file = value
                elif key == "thumbnail_no_light_file":
                    plate.thumbnail_no_light_file = value
                elif key == "top_file":
                    plate.top_file = value
                elif key == "pick_file":
                    plate.pick_file = value
                elif key == "gcode_file":
                    plate.gcode_file = value
                elif key == "pattern_file":
                    plate.pattern_file = value
                elif key == "pattern_bbox_file":
                    plate.pattern_bbox_file = value
                # v2: per-plate print settings — now properly routed
                elif key == "bed_type":
                    plate.bed_type = value
                elif key == "print_sequence":
                    plate.print_sequence = value
                elif key == "first_layer_print_sequence":
                    plate.first_layer_print_sequence = value
                elif key == "other_layers_print_sequence":
                    plate.other_layers_print_sequence = value
                elif key == "other_layers_print_sequence_nums":
                    plate.other_layers_print_sequence_nums = value
                elif key == "spiral_mode":
                    plate.spiral_mode = value
                elif key == "filament_map_mode":
                    plate.filament_map_mode = value
                elif key == "filament_maps":
                    plate.filament_maps = value
                elif key == "limit_filament_maps":
                    plate.limit_filament_maps = value
                elif key == "filament_volume_maps":
                    plate.filament_volume_maps = value
                else:
                    plate.extra_metadata[key] = value

            for inst_el in plate_el.findall("model_instance"):
                inst = ModelInstance(object_id=0)
                for meta in inst_el.findall("metadata"):
                    key = meta.get("key", "")
                    value = meta.get("value", "")
                    if key == "object_id":
                        inst.object_id = int(value) if value else 0
                    elif key == "instance_id":
                        inst.instance_id = int(value) if value else 0
                    elif key == "identify_id":
                        inst.identify_id = int(value) if value else 0
                plate.instances.append(inst)

            self.plates.append(plate)

        # Assembly
        assemble = root.find("assemble")
        if assemble is not None:
            for item in assemble.findall("assemble_item"):
                ai = AssembleItem(
                    object_id=int(item.get("object_id", 0)),
                    instance_id=int(item.get("instance_id", 0)),
                    transform=item.get("transform", "1 0 0 0 1 0 0 0 1 0 0 0"),
                    offset=item.get("offset", "0 0 0"),
                )
                self.assemble_items.append(ai)

    def _parse_slice_info(self, data: bytes):
        """Parse Metadata/slice_info.config."""
        root = ET.fromstring(data)
        si = SliceInfo()

        header = root.find("header")
        if header is not None:
            for item in header.findall("header_item"):
                key = item.get("key", "")
                value = item.get("value", "")
                if key == "X-BBL-Client-Type":
                    si.client_type = value
                elif key == "X-BBL-Client-Version":
                    si.client_version = value

        for plate_el in root.findall("plate"):
            sp = SlicePlate()
            for meta in plate_el.findall("metadata"):
                key = meta.get("key", "")
                value = meta.get("value", "")
                if key == "index":
                    sp.index = int(value) if value else 1
                elif key == "printer_model_id":
                    sp.printer_model_id = value
                elif key == "nozzle_diameters":
                    sp.nozzle_diameters = value
                elif key == "timelapse_type":
                    sp.timelapse_type = value
                elif key == "prediction":
                    sp.prediction = value
                elif key == "weight":
                    sp.weight = value
                elif key == "outside":
                    sp.outside = value
                elif key == "support_used":
                    sp.support_used = value
                elif key == "label_object_enabled":
                    sp.label_object_enabled = value
                # v2: additional slice plate metadata
                elif key == "extruder_type":
                    sp.extruder_type = value
                elif key == "nozzle_volume_type":
                    sp.nozzle_volume_type = value
                elif key == "nozzle_types":
                    sp.nozzle_types = value
                elif key == "first_layer_time":
                    sp.first_layer_time = value
                elif key == "skipped":
                    sp.skipped = value
                else:
                    sp.extra[key] = value

            for fil_el in plate_el.findall("filament"):
                sf = SliceFilament(
                    id=int(fil_el.get("id", 1)),
                    type=fil_el.get("type", ""),
                    color=fil_el.get("color", ""),
                    used_m=fil_el.get("used_m", ""),
                    used_g=fil_el.get("used_g", ""),
                    # v2: additional filament attributes
                    used_for_support=fil_el.get("used_for_support", ""),
                    used_for_object=fil_el.get("used_for_object", ""),
                    tray_info_idx=fil_el.get("tray_info_idx", ""),
                    group_id=fil_el.get("group_id", ""),
                    nozzle_diameter=fil_el.get("nozzle_diameter", ""),
                    nozzle_volume_type=fil_el.get("volume_type", ""),
                )
                sp.filaments.append(sf)

            for obj_el in plate_el.findall("object"):
                sp.objects.append(dict(obj_el.attrib))

            for warn_el in plate_el.findall("warning"):
                sp.warnings.append(dict(warn_el.attrib))

            layer_lists_el = plate_el.find("layer_filament_lists")
            if layer_lists_el is not None:
                for item_el in layer_lists_el.findall("layer_filament_list"):
                    sp.layer_filament_lists.append(dict(item_el.attrib))

            si.plates.append(sp)

        self.slice_info = si

    def _parse_cut_info(self, data: bytes):
        """Parse Metadata/cut_information.xml."""
        root = ET.fromstring(data)
        for obj_el in root.findall("object"):
            co = CutObject(object_id=int(obj_el.get("id", 0)))
            cut_id_el = obj_el.find("cut_id")
            if cut_id_el is not None:
                co.cut_id = int(cut_id_el.get("id", 0))
                co.check_sum = int(cut_id_el.get("check_sum", 1))
                co.connectors_cnt = int(cut_id_el.get("connectors_cnt", 0))

            connectors_el = obj_el.find("connectors")
            if connectors_el is not None:
                for conn_el in connectors_el.findall("connector"):
                    cc = CutConnector(
                        volume_id=int(conn_el.get("volume_id", 0)),
                        type=int(conn_el.get("type", 0)),
                        radius=float(conn_el.get("radius", 0)),
                        height=float(conn_el.get("height", 0)),
                        r_tolerance=float(conn_el.get("r_tolerance", 0)),
                        h_tolerance=float(conn_el.get("h_tolerance", 0)),
                    )
                    co.connectors.append(cc)
            self.cut_objects.append(co)

    def _load_embedded_shape_files(self, archive: zipfile.ZipFile, names: set[str]):
        for obj in self.objects:
            for part in obj.parts:
                shape_config = part.shape_config
                if shape_config and shape_config.filepath3mf and shape_config.filepath3mf in names:
                    shape_config.svg_file_data = archive.read(shape_config.filepath3mf)

    def _collect_shape_svg_files(self) -> dict[str, bytes]:
        svg_files: dict[str, bytes] = {}
        for obj in self.objects:
            for part in obj.parts:
                shape_config = part.shape_config
                if shape_config and shape_config.filepath3mf and shape_config.svg_file_data:
                    svg_files[shape_config.filepath3mf] = shape_config.svg_file_data
        return svg_files

    # ─── BUILDERS ────────────────────────────────────────────────────────────

    def _build_content_types(self) -> str:
        """Verified: bbs_3mf.cpp:6557-6561"""
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">')
        lines.append(' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>')
        lines.append(' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>')
        lines.append(' <Default Extension="png" ContentType="image/png"/>')
        lines.append(' <Default Extension="gcode" ContentType="text/x.gcode"/>')
        lines.append('</Types>')
        return "\n".join(lines) + "\n"

    def _build_rels(self) -> str:
        """Verified: bbs_3mf.cpp:6683-6727"""
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">')
        lines.append(f' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="{REL_3DMODEL}"/>')

        # Thumbnail relationships
        if self.thumbnails:
            thumb_main = None
            thumb_middle = None
            thumb_small = None
            for name in self.thumbnails:
                if "thumbnail_3mf" in name or "plate_1.png" in name:
                    thumb_main = name
                # v2: handle bbl_thumbnail.png (bbs_3mf.cpp:163)
                if "bbl_thumbnail" in name and thumb_main is None:
                    thumb_main = name
                if "thumbnail_middle" in name:
                    thumb_middle = name
                if "thumbnail_small" in name:
                    thumb_small = name
            if thumb_main is None and "Metadata/plate_1.png" in self.thumbnails:
                thumb_main = "Metadata/plate_1.png"
            if thumb_middle is None:
                thumb_middle = thumb_main
            if thumb_small is None and "Metadata/plate_1_small.png" in self.thumbnails:
                thumb_small = "Metadata/plate_1_small.png"
            if thumb_main:
                lines.append(f' <Relationship Target="/{thumb_main}" Id="rel-2" Type="{REL_THUMBNAIL}"/>')
            if thumb_middle:
                lines.append(f' <Relationship Target="/{thumb_middle}" Id="rel-4" Type="{REL_THUMBNAIL_MIDDLE}"/>')
            if thumb_small:
                lines.append(f' <Relationship Target="/{thumb_small}" Id="rel-5" Type="{REL_THUMBNAIL_SMALL}"/>')

        lines.append('</Relationships>')
        return "\n".join(lines) + "\n"

    def _build_model_rels(self) -> str:
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">')
        for i, path in enumerate(sorted(self.sub_models.keys()), 1):
            lines.append(f' <Relationship Target="/{path}" Id="rel-{i}" Type="{REL_3DMODEL}"/>')
        lines.append('</Relationships>')
        return "\n".join(lines) + "\n"

    def _build_model_config_rels(self) -> str:
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">')
        for index, path in enumerate(sorted(self.gcode_files.keys()), 1):
            lines.append(f' <Relationship Target="/{path}" Id="rel-{index}" Type="{REL_GCODE}"/>')
        lines.append('</Relationships>')
        return "\n".join(lines) + "\n"

    @staticmethod
    def _extract_uuid_seed(uuid: str, *suffixes: str) -> Optional[int]:
        for suffix in suffixes:
            if uuid.endswith(suffix):
                prefix = uuid[:-len(suffix)]
                try:
                    return int(prefix, 16)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _production_uuid(seed: int, suffix: str) -> str:
        return f"{seed & 0xFFFFFFFF:08x}{suffix}"

    def _sub_model_parent_id(self, model_path: str) -> Optional[int]:
        for obj in self.objects:
            for comp in obj.components:
                if comp.path == model_path:
                    return obj.id
        return None

    def _ensure_production_identifiers(self):
        if not self.build_uuid:
            self.build_uuid = BUILD_UUID

        for obj in self.objects:
            if obj.backup_id is None:
                obj.backup_id = self._extract_uuid_seed(obj.uuid, OBJECT_UUID_SUFFIX, OBJECT_UUID_SUFFIX2)
            if obj.backup_id is None:
                obj.backup_id = obj.id
            if not obj.uuid:
                uses_shared_mesh = obj.uuid.endswith(OBJECT_UUID_SUFFIX2) or any(part.mesh_shared for part in obj.parts)
                suffix = OBJECT_UUID_SUFFIX2 if uses_shared_mesh else OBJECT_UUID_SUFFIX
                obj.uuid = self._production_uuid(obj.backup_id, suffix)

            for index, comp in enumerate(obj.components):
                if not comp.uuid:
                    comp.uuid = self._production_uuid(index + (obj.backup_id << 16), COMPONENT_UUID_SUFFIX)

        for bi in self.build_items:
            if not bi.uuid:
                obj = self._find_object(bi.objectid)
                seed = obj.backup_id if obj and obj.backup_id is not None else bi.objectid
                bi.uuid = self._production_uuid(seed, BUILD_UUID_SUFFIX)

        for model_path, leaf_objects in self.sub_models.items():
            parent_id = self._sub_model_parent_id(model_path)
            parent_obj = self._find_object(parent_id) if parent_id is not None else None
            parent_backup_id = parent_obj.backup_id if parent_obj and parent_obj.backup_id is not None else parent_id
            for index, obj in enumerate(leaf_objects):
                if obj.backup_id is None:
                    obj.backup_id = self._extract_uuid_seed(obj.uuid, SUB_OBJECT_UUID_SUFFIX)
                if not obj.uuid:
                    seed = obj.id if parent_backup_id is None else index + (parent_backup_id << 16)
                    obj.uuid = self._production_uuid(seed, SUB_OBJECT_UUID_SUFFIX)

    def _build_main_model(self) -> str:
        """Build 3D/3dmodel.model XML.
        Verified against bbs_3mf.cpp:6805-6810"""
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']

        # v2: xmlns:p is conditional on actual production-extension usage.
        has_production = bool(
            self.sub_models
            or any(o.uuid for o in self.objects)
            or any(
                comp.path or comp.uuid
                for obj in self.objects
                for comp in obj.components
            )
            or any(item.uuid or item.path for item in self.build_items)
        )
        has_materials = bool(self.color_groups)

        if has_production:
            self._ensure_production_identifiers()

        model_attrs = (
            f'<model unit="millimeter" xml:lang="en-US"'
            f' xmlns="{NS_CORE}"'
            f' xmlns:BambuStudio="{NS_BAMBU}"'
        )
        if has_materials:
            model_attrs += f' xmlns:m="{NS_MATERIAL}"'
        if has_production:
            model_attrs += f' xmlns:p="{NS_PRODUCTION}" requiredextensions="p"'
        model_attrs += '>'
        lines.append(model_attrs)

        # Metadata
        for key, value in self.metadata.items():
            escaped = _xml_escape(value)
            lines.append(f' <metadata name="{key}">{escaped}</metadata>')

        # Resources
        lines.append(' <resources>')

        # Color groups
        if self.color_groups:
            for cg in self.color_groups:
                lines.append(f'  <m:colorgroup id="{cg.id}">')
                for c in cg.colors:
                    lines.append(f'   <m:color color="{c.color}"/>')
                lines.append('  </m:colorgroup>')

        # Objects (component assemblies referencing sub-models)
        for obj in self.objects:
            attrs = f'id="{obj.id}"'
            if obj.uuid:
                attrs += f' p:UUID="{obj.uuid}"'
            attrs += f' type="{obj.type}"'
            if obj.name and obj._name_in_main_model:
                attrs += f' name="{_xml_escape_attr(obj.name)}"'
            for k, v in obj.extra_metadata.items():
                attrs += f' {k}="{_xml_escape_attr(v)}"'

            if obj.components:
                lines.append(f'  <object {attrs}>')
                lines.append('   <components>')
                for comp in obj.components:
                    cattrs = f'objectid="{comp.objectid}"'
                    if comp.path:
                        cattrs = f'p:path="{comp.path}" {cattrs}'
                    if comp.uuid:
                        cattrs += f' p:UUID="{comp.uuid}"'
                    cattrs += f' transform="{comp.transform}"'
                    lines.append(f'    <component {cattrs}/>')
                lines.append('   </components>')
                lines.append('  </object>')
            elif obj.mesh:
                lines.append(f'  <object {attrs}>')
                self._write_mesh(lines, obj.mesh, indent=3)
                lines.append('  </object>')
            else:
                lines.append(f'  <object {attrs}/>')

        lines.append(' </resources>')

        # Build
        if has_production:
            lines.append(f' <build p:UUID="{self.build_uuid}">')
        elif not self.build_items:
            lines.append(' <build/>')
        else:
            lines.append(' <build>')

        for bi in self.build_items:
            battrs = f'objectid="{bi.objectid}"'
            if has_production and bi.uuid:
                battrs += f' p:UUID="{bi.uuid}"'
            if bi.path:
                battrs += f' p:path="{_xml_escape_attr(bi.path)}"'
            battrs += f' transform="{bi.transform}"'
            battrs += f' printable="{bi.printable}"'
            lines.append(f'  <item {battrs}/>')

        if self.build_items or has_production:
            lines.append(' </build>')

        lines.append('</model>')
        return "\n".join(lines) + "\n"

    def _build_sub_model(self, model_path: str, leaf_objects: list) -> str:
        """Build an external sub-model file XML."""
        self._ensure_production_identifiers()
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append(
            '<model unit="millimeter" xml:lang="en-US"'
            f' xmlns="{NS_CORE}"'
            f' xmlns:BambuStudio="{NS_BAMBU}"'
            f' xmlns:p="{NS_PRODUCTION}"'
            ' requiredextensions="p">'
        )
        lines.append(f' <metadata name="BambuStudio:3mfVersion">{VERSION_BBS_3MF}</metadata>')
        lines.append(' <resources>')

        for obj in leaf_objects:
            attrs = f'id="{obj.id}"'
            if obj.uuid:
                attrs += f' p:UUID="{obj.uuid}"'
            attrs += f' type="{obj.type}"'

            if obj.mesh:
                lines.append(f'  <object {attrs}>')
                self._write_mesh(lines, obj.mesh, indent=3)
                lines.append('  </object>')
            else:
                lines.append(f'  <object {attrs}/>')

        lines.append(' </resources>')
        lines.append(' <build/>')
        lines.append('</model>')
        return "\n".join(lines) + "\n"

    def _write_mesh(self, lines: list, mesh: ObjectMesh, indent: int = 3):
        """Write mesh vertices and triangles to output lines."""
        pad = " " * indent
        lines.append(f'{pad}<mesh>')
        lines.append(f'{pad} <vertices>')
        for v in mesh.vertices:
            lines.append(f'{pad}  <vertex x="{v.x}" y="{v.y}" z="{v.z}"/>')
        lines.append(f'{pad} </vertices>')

        lines.append(f'{pad} <triangles>')
        for t in mesh.triangles:
            tattrs = f'v1="{t.v1}" v2="{t.v2}" v3="{t.v3}"'
            if t.paint_supports:
                tattrs += f' paint_supports="{t.paint_supports}"'
            if t.paint_seam:
                tattrs += f' paint_seam="{t.paint_seam}"'
            if t.paint_color:
                tattrs += f' paint_color="{t.paint_color}"'
            if t.paint_fuzzy_skin:
                tattrs += f' paint_fuzzy_skin="{t.paint_fuzzy_skin}"'
            if t.face_property:
                tattrs += f' face_property="{t.face_property}"'
            if t.pid is not None:
                tattrs += f' pid="{t.pid}"'
            if t.p1 is not None:
                tattrs += f' p1="{t.p1}"'
            if t.p2 is not None:
                tattrs += f' p2="{t.p2}"'
            if t.p3 is not None:
                tattrs += f' p3="{t.p3}"'
            lines.append(f'{pad}  <triangle {tattrs}/>')
        lines.append(f'{pad} </triangles>')
        lines.append(f'{pad}</mesh>')

    def _write_text_info(self, lines: list, text_info: TextInfo, indent: int = 6):
        pad = " " * indent
        attrs = []
        for key, value in [
            ("text", text_info.text),
            ("font_name", text_info.font_name),
            ("font_version", text_info.font_version),
            ("style_name", text_info.style_name),
            ("boldness", text_info.boldness),
            ("skew", text_info.skew),
            ("font_index", text_info.font_index),
            ("font_size", text_info.font_size),
            ("thickness", text_info.thickness),
            ("embeded_depth", text_info.embeded_depth),
            ("rotate_angle", text_info.rotate_angle),
            ("text_gap", text_info.text_gap),
            ("bold", text_info.bold),
            ("italic", text_info.italic),
        ]:
            if value != "":
                attrs.append(f'{key}="{_xml_escape_attr(value)}"')

        try:
            font_version = float(text_info.font_version) if text_info.font_version else 0.0
        except ValueError:
            font_version = 0.0

        if font_version > 2.2:
            if text_info.surface_type != "":
                attrs.append(f'surface_type="{_xml_escape_attr(text_info.surface_type)}"')
        else:
            if text_info.surface_text != "":
                attrs.append(f'surface_text="{_xml_escape_attr(text_info.surface_text)}"')
            if text_info.keep_horizontal != "":
                attrs.append(f'keep_horizontal="{_xml_escape_attr(text_info.keep_horizontal)}"')

        for key, value in [
            ("hit_mesh", text_info.hit_mesh),
            ("hit_position", text_info.hit_position),
            ("hit_normal", text_info.hit_normal),
        ]:
            if value != "":
                attrs.append(f'{key}="{_xml_escape_attr(value)}"')

        attr_text = " ".join(attrs)
        lines.append(f'{pad}<text_info {attr_text}/>' )

    def _write_shape_config(self, lines: list, shape_config: ShapeConfig, indent: int = 6):
        pad = " " * indent
        attrs = []
        for key, value in [
            ("filepath", shape_config.filepath),
            ("filepath3mf", shape_config.filepath3mf),
            ("scale", shape_config.scale),
            ("unhealed", shape_config.unhealed),
            ("depth", shape_config.depth),
            ("use_surface", shape_config.use_surface),
            ("transform", shape_config.transform),
            ("style_name", shape_config.style_name),
            ("font_descriptor", shape_config.font_descriptor),
            ("font_descriptor_type", shape_config.font_descriptor_type),
            ("char_gap", shape_config.char_gap),
            ("line_gap", shape_config.line_gap),
            ("line_height", shape_config.line_height),
            ("boldness", shape_config.boldness),
            ("skew", shape_config.skew),
            ("per_glyph", shape_config.per_glyph),
            ("horizontal", shape_config.horizontal),
            ("vertical", shape_config.vertical),
            ("collection", shape_config.collection),
            ("family", shape_config.family),
            ("face_name", shape_config.face_name),
            ("style", shape_config.style),
            ("weight", shape_config.weight),
        ]:
            if value != "":
                attrs.append(f'{key}="{_xml_escape_attr(value)}"')
        for key, value in shape_config.extra.items():
            attrs.append(f'{key}="{_xml_escape_attr(value)}"')
        attr_text = " ".join(attrs)
        lines.append(f'{pad}<BambuStudioShape {attr_text}/>' )

    def _build_model_settings(self) -> str:
        """Build Metadata/model_settings.config XML."""
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append('<config>')

        # Objects
        for obj in self.objects:
            lines.append(f'  <object id="{obj.id}">')
            if obj.name:
                lines.append(f'    <metadata key="name" value="{_xml_escape_attr(obj.name)}"/>')
            if obj.extruder:
                lines.append(f'    <metadata key="extruder" value="{obj.extruder}"/>')
            for key, value in obj.settings:
                lines.append(f'    <metadata key="{key}" value="{_xml_escape_attr(value)}"/>')
            if obj.face_count is not None:
                lines.append(f'    <metadata face_count="{obj.face_count}"/>')

            for part in obj.parts:
                pattrs = f'id="{part.id}" subtype="{part.subtype}"'
                lines.append(f'    <part {pattrs}>')
                lines.append(f'      <metadata key="name" value="{_xml_escape_attr(part.name)}"/>')
                lines.append(f'      <metadata key="matrix" value="{part.matrix}"/>')
                if part.source_file:
                    lines.append(f'      <metadata key="source_file" value="{_xml_escape_attr(part.source_file)}"/>')
                if part.source_object_id:
                    lines.append(f'      <metadata key="source_object_id" value="{part.source_object_id}"/>')
                if part.source_volume_id:
                    lines.append(f'      <metadata key="source_volume_id" value="{part.source_volume_id}"/>')
                lines.append(f'      <metadata key="source_offset_x" value="{part.source_offset_x}"/>')
                lines.append(f'      <metadata key="source_offset_y" value="{part.source_offset_y}"/>')
                lines.append(f'      <metadata key="source_offset_z" value="{part.source_offset_z}"/>')
                if part.source_in_inches:
                    lines.append(f'      <metadata key="source_in_inches" value="{part.source_in_inches}"/>')
                if part.source_in_meters:
                    lines.append(f'      <metadata key="source_in_meters" value="{part.source_in_meters}"/>')
                if part.extruder:
                    lines.append(f'      <metadata key="extruder" value="{part.extruder}"/>')
                if part.mesh_shared:
                    lines.append(f'      <metadata key="mesh_shared" value="{part.mesh_shared}"/>')
                for key, value in part.extra_metadata.items():
                    lines.append(f'      <metadata key="{key}" value="{_xml_escape_attr(value)}"/>')
                shape_config = part.shape_config or (obj.shape_config if len(obj.parts) == 1 else None)
                if shape_config is not None:
                    self._write_shape_config(lines, shape_config)
                text_info = part.text_info or (obj.text_info if len(obj.parts) == 1 else None)
                if text_info is not None and text_info.text:
                    self._write_text_info(lines, text_info)
                if part.mesh_stat:
                    ms = part.mesh_stat
                    lines.append(
                        f'      <mesh_stat face_count="{ms.face_count}" '
                        f'edges_fixed="{ms.edges_fixed}" '
                        f'degenerate_facets="{ms.degenerate_facets}" '
                        f'facets_removed="{ms.facets_removed}" '
                        f'facets_reversed="{ms.facets_reversed}" '
                        f'backwards_edges="{ms.backwards_edges}"/>'
                    )
                lines.append('    </part>')
            lines.append('  </object>')

        # Plates — v2: write per-plate settings from named fields
        for plate in self.plates:
            lines.append('  <plate>')
            lines.append(f'    <metadata key="plater_id" value="{plate.plater_id}"/>')
            lines.append(f'    <metadata key="plater_name" value="{_xml_escape_attr(plate.plater_name)}"/>')
            lines.append(f'    <metadata key="locked" value="{plate.locked}"/>')
            if plate.thumbnail_file:
                lines.append(f'    <metadata key="thumbnail_file" value="{plate.thumbnail_file}"/>')
            if plate.thumbnail_no_light_file:
                lines.append(f'    <metadata key="thumbnail_no_light_file" value="{plate.thumbnail_no_light_file}"/>')
            if plate.top_file:
                lines.append(f'    <metadata key="top_file" value="{plate.top_file}"/>')
            if plate.pick_file:
                lines.append(f'    <metadata key="pick_file" value="{plate.pick_file}"/>')
            if plate.gcode_file:
                lines.append(f'    <metadata key="gcode_file" value="{plate.gcode_file}"/>')
            if plate.pattern_file:
                lines.append(f'    <metadata key="pattern_file" value="{plate.pattern_file}"/>')
            if plate.pattern_bbox_file:
                lines.append(f'    <metadata key="pattern_bbox_file" value="{plate.pattern_bbox_file}"/>')
            # v2: per-plate print settings
            if plate.bed_type:
                lines.append(f'    <metadata key="bed_type" value="{plate.bed_type}"/>')
            if plate.print_sequence:
                lines.append(f'    <metadata key="print_sequence" value="{plate.print_sequence}"/>')
            if plate.first_layer_print_sequence:
                lines.append(f'    <metadata key="first_layer_print_sequence" value="{plate.first_layer_print_sequence}"/>')
            if plate.other_layers_print_sequence:
                lines.append(f'    <metadata key="other_layers_print_sequence" value="{plate.other_layers_print_sequence}"/>')
            if plate.other_layers_print_sequence_nums:
                lines.append(f'    <metadata key="other_layers_print_sequence_nums" value="{plate.other_layers_print_sequence_nums}"/>')
            if plate.spiral_mode:
                lines.append(f'    <metadata key="spiral_mode" value="{plate.spiral_mode}"/>')
            if plate.filament_map_mode:
                lines.append(f'    <metadata key="filament_map_mode" value="{plate.filament_map_mode}"/>')
            if plate.filament_maps:
                lines.append(f'    <metadata key="filament_maps" value="{plate.filament_maps}"/>')
            if plate.limit_filament_maps:
                lines.append(f'    <metadata key="limit_filament_maps" value="{plate.limit_filament_maps}"/>')
            if plate.filament_volume_maps:
                lines.append(f'    <metadata key="filament_volume_maps" value="{plate.filament_volume_maps}"/>')
            for key, value in plate.extra_metadata.items():
                lines.append(f'    <metadata key="{key}" value="{_xml_escape_attr(value)}"/>')
            for inst in plate.instances:
                lines.append('    <model_instance>')
                lines.append(f'      <metadata key="object_id" value="{inst.object_id}"/>')
                lines.append(f'      <metadata key="instance_id" value="{inst.instance_id}"/>')
                lines.append(f'      <metadata key="identify_id" value="{inst.identify_id}"/>')
                lines.append('    </model_instance>')
            lines.append('  </plate>')

        # Assembly
        if self.assemble_items:
            lines.append('  <assemble>')
            for ai in self.assemble_items:
                lines.append(
                    f'   <assemble_item object_id="{ai.object_id}" '
                    f'instance_id="{ai.instance_id}" '
                    f'transform="{ai.transform}" '
                    f'offset="{ai.offset}" />'
                )
            lines.append('  </assemble>')

        lines.append('</config>')
        return "\n".join(lines) + "\n"

    def _build_slice_info(self) -> str:
        """Build Metadata/slice_info.config XML."""
        si = self.slice_info
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append('<config>')
        lines.append('  <header>')
        lines.append(f'    <header_item key="X-BBL-Client-Type" value="{si.client_type}"/>')
        lines.append(f'    <header_item key="X-BBL-Client-Version" value="{si.client_version}"/>')
        lines.append('  </header>')

        for sp in si.plates:
            lines.append('  <plate>')
            lines.append(f'    <metadata key="index" value="{sp.index}"/>')
            if sp.printer_model_id:
                lines.append(f'    <metadata key="printer_model_id" value="{sp.printer_model_id}"/>')
            if sp.nozzle_diameters:
                lines.append(f'    <metadata key="nozzle_diameters" value="{sp.nozzle_diameters}"/>')
            lines.append(f'    <metadata key="timelapse_type" value="{sp.timelapse_type}"/>')
            if sp.prediction:
                lines.append(f'    <metadata key="prediction" value="{sp.prediction}"/>')
            if sp.weight:
                lines.append(f'    <metadata key="weight" value="{sp.weight}"/>')
            lines.append(f'    <metadata key="outside" value="{sp.outside}"/>')
            lines.append(f'    <metadata key="support_used" value="{sp.support_used}"/>')
            lines.append(f'    <metadata key="label_object_enabled" value="{sp.label_object_enabled}"/>')
            # v2: additional slice plate metadata
            if sp.extruder_type:
                lines.append(f'    <metadata key="extruder_type" value="{sp.extruder_type}"/>')
            if sp.nozzle_volume_type:
                lines.append(f'    <metadata key="nozzle_volume_type" value="{sp.nozzle_volume_type}"/>')
            if sp.nozzle_types:
                lines.append(f'    <metadata key="nozzle_types" value="{sp.nozzle_types}"/>')
            if sp.first_layer_time:
                lines.append(f'    <metadata key="first_layer_time" value="{sp.first_layer_time}"/>')
            if sp.skipped:
                lines.append(f'    <metadata key="skipped" value="{sp.skipped}"/>')
            for key, value in sp.extra.items():
                lines.append(f'    <metadata key="{key}" value="{_xml_escape_attr(value)}"/>')
            for obj_dict in sp.objects:
                attrs = " ".join(f'{k}="{_xml_escape_attr(v)}"' for k, v in obj_dict.items())
                lines.append(f'    <object {attrs} />')
            for sf in sp.filaments:
                fattrs = f'id="{sf.id}" type="{sf.type}" color="{sf.color}" used_m="{sf.used_m}" used_g="{sf.used_g}"'
                # v2: additional filament attributes
                if sf.used_for_support:
                    fattrs += f' used_for_support="{sf.used_for_support}"'
                if sf.used_for_object:
                    fattrs += f' used_for_object="{sf.used_for_object}"'
                if sf.tray_info_idx:
                    fattrs += f' tray_info_idx="{sf.tray_info_idx}"'
                if sf.group_id:
                    fattrs += f' group_id="{sf.group_id}"'
                if sf.nozzle_diameter:
                    fattrs += f' nozzle_diameter="{sf.nozzle_diameter}"'
                if sf.nozzle_volume_type:
                    fattrs += f' volume_type="{sf.nozzle_volume_type}"'
                lines.append(f'    <filament {fattrs} />')
            for warn in sp.warnings:
                attrs = " ".join(f'{k}="{_xml_escape_attr(v)}"' for k, v in warn.items())
                lines.append(f'    <warning {attrs} />')
            if sp.layer_filament_lists:
                lines.append('    <layer_filament_lists>')
                for item in sp.layer_filament_lists:
                    attrs = " ".join(f'{k}="{_xml_escape_attr(v)}"' for k, v in item.items())
                    lines.append(f'      <layer_filament_list {attrs} />')
                lines.append('    </layer_filament_lists>')
            lines.append('  </plate>')

        lines.append('</config>')
        return "\n".join(lines) + "\n"

    def _build_cut_info(self) -> str:
        """Build Metadata/cut_information.xml."""
        lines = ['<?xml version="1.0" encoding="utf-8"?>']
        lines.append('<objects>')
        for co in self.cut_objects:
            lines.append(f' <object id="{co.object_id}">')
            lines.append(
                f'  <cut_id id="{co.cut_id}" check_sum="{co.check_sum}" '
                f'connectors_cnt="{co.connectors_cnt}"/>'
            )
            if co.connectors:
                lines.append('  <connectors>')
                for cc in co.connectors:
                    lines.append(
                        f'   <connector volume_id="{cc.volume_id}" type="{cc.type}" '
                        f'radius="{cc.radius}" height="{cc.height}" '
                        f'r_tolerance="{cc.r_tolerance}" h_tolerance="{cc.h_tolerance}"/>'
                    )
                lines.append('  </connectors>')
            lines.append(' </object>')
        lines.append('</objects>')
        return "\n".join(lines) + "\n"

    # ─── HELPERS ─────────────────────────────────────────────────────────────

    def _rebuild_obj_index(self):
        """Rebuild the object ID → ModelObject index."""
        self._obj_index = {obj.id: obj for obj in self.objects}

    def _find_object(self, obj_id: int) -> Optional[ModelObject]:
        """O(1) lookup via dict index, with fallback."""
        obj = self._obj_index.get(obj_id)
        if obj is not None:
            return obj
        # Fallback: linear scan (covers race during initial load)
        for obj in self.objects:
            if obj.id == obj_id:
                self._obj_index[obj_id] = obj
                return obj
        return None

    def get_object_by_name(self, name: str) -> Optional[ModelObject]:
        """Find an object by its display name."""
        for obj in self.objects:
            if obj.name == name:
                return obj
        return None

    def get_plate(self, plate_id: int) -> Optional[Plate]:
        """Get a plate by its plater_id."""
        for p in self.plates:
            if p.plater_id == plate_id:
                return p
        return None

    def get_setting(self, key: str, default=None):
        """Get a project-level setting by key."""
        return self.project_settings.get(key, default)

    def set_setting(self, key: str, value):
        """Set a project-level setting. Invalidates raw cache for clean re-serialization."""
        self.project_settings[key] = value
        self._project_settings_raw = None  # force re-serialization on save

    def mark_modified(self, *sections):
        """Mark sections as modified so save() regenerates them instead of using raw bytes.

        Valid section names:
            main_model, model_settings, slice_info, cut_info,
            or a sub-model path like '3D/Objects/object_1.model'
        """
        self._modified_sections.update(sections)

    def get_filament_colors(self) -> list:
        """Return filament color list from project settings."""
        return self.project_settings.get("filament_colour", [])

    def get_filament_types(self) -> list:
        """Return filament type list from project settings."""
        return self.project_settings.get("filament_type", [])

    def summary(self) -> str:
        """Return a human-readable summary of the project."""
        lines = []
        app = self.metadata.get("Application", "unknown")
        lines.append(f"Application: {app}")
        lines.append(f"Objects: {len(self.objects)}")
        lines.append(f"Plates: {len(self.plates)}")
        lines.append(f"Sub-models: {len(self.sub_models)}")

        total_verts = 0
        total_tris = 0
        for leaf_objects in self.sub_models.values():
            for obj in leaf_objects:
                if obj.mesh:
                    total_verts += len(obj.mesh.vertices)
                    total_tris += len(obj.mesh.triangles)
        for obj in self.objects:
            if obj.mesh:
                total_verts += len(obj.mesh.vertices)
                total_tris += len(obj.mesh.triangles)
        lines.append(f"Total vertices: {total_verts:,}")
        lines.append(f"Total triangles: {total_tris:,}")

        if self.project_settings and "_raw" not in self.project_settings:
            colors = self.get_filament_colors()
            types = self.get_filament_types()
            if colors:
                lines.append(f"Filaments: {len(colors)}")
                for i, (c, t) in enumerate(zip(colors, types or [""] * len(colors))):
                    lines.append(f"  Slot {i+1}: {t} {c}")

        for plate in self.plates:
            plate_info = f"Plate {plate.plater_id}: {plate.plater_name or '(unnamed)'} — {len(plate.instances)} object(s)"
            if plate.bed_type:
                plate_info += f" [{plate.bed_type}]"
            lines.append(plate_info)

        has_gcode = bool(self.gcode_files)
        lines.append(f"Sliced (has gcode): {has_gcode}")

        return "\n".join(lines)


# ─── XML helpers ─────────────────────────────────────────────────────────────

def _xml_escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def _xml_escape_attr(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point."""
    import sys as _sys
    if len(_sys.argv) < 2:
        print("Usage: bambu3mf <file.3mf> [command]")
        print("Commands: summary (default), dump-settings, list-objects, round-trip <output.3mf>")
        _sys.exit(1)

    path = _sys.argv[1]
    cmd = _sys.argv[2] if len(_sys.argv) > 2 else "summary"

    proj = Bambu3MF.load(path)

    if cmd == "summary":
        print(proj.summary())
    elif cmd == "dump-settings":
        if proj.project_settings and "_raw" not in proj.project_settings:
            print(json.dumps(proj.project_settings, indent=2))
        else:
            print(proj.project_settings.get("_raw", "No settings found"))
    elif cmd == "list-objects":
        for obj in proj.objects:
            print(f"Object {obj.id}: {obj.name} (type={obj.type}, extruder={obj.extruder})")
            for part in obj.parts:
                print(f"  Part {part.id}: {part.name} (subtype={part.subtype}, extruder={part.extruder})")
    elif cmd == "round-trip":
        out = _sys.argv[3] if len(_sys.argv) > 3 else path.replace(".3mf", "_roundtrip.3mf")
        proj.save(out)
        print(f"Saved to {out}")
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
