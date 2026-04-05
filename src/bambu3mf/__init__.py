"""
bambu3mf — Bambu Studio .3mf reader/writer with byte-perfect round-trip fidelity.

Pure Python stdlib. No dependencies.

Usage:
    from bambu3mf import Bambu3MF

    proj = Bambu3MF.load("file.3mf")
    print(proj.summary())

    proj.set_setting("layer_height", "0.12")
    proj.save("modified.3mf")
"""

from .bambu3mf import (
    Bambu3MF,
    ModelObject,
    Part,
    Plate,
    BuildItem,
    ComponentRef,
    AssembleItem,
    CutObject,
    CutConnector,
    ObjectMesh,
    Triangle,
    Vertex,
    ColorGroup,
    ColorDef,
    SliceInfo,
    SlicePlate,
    SliceFilament,
    ShapeConfig,
    MeshStat,
    VERSION_BBS_3MF,
    VERSION_BBS_3MF_COMPATIBLE,
    OBJECT_UUID_SUFFIX,
    OBJECT_UUID_SUFFIX2,
)

__version__ = "2.0.0"
