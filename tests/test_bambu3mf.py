import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bambu3mf import Bambu3MF  # noqa: E402
from bambu3mf.bambu3mf import (  # noqa: E402
    BuildItem,
    ColorDef,
    ColorGroup,
    ComponentRef,
    CutConnector,
    CutObject,
    ModelObject,
    ObjectMesh,
    Part,
    ShapeConfig,
    SliceFilament,
    SliceInfo,
    SlicePlate,
    TextInfo,
    Triangle,
    Vertex,
)


class Bambu3MFAdvancedRoundTripTests(unittest.TestCase):
    def test_text_info_shape_and_svg_round_trip(self):
        proj = Bambu3MF.new()
        obj = ModelObject(id=1, name="obj")
        part = Part(id=11, name="part")
        part.text_info = TextInfo(
            text="HELLO",
            font_name="Arial",
            font_version="3.0",
            style_name="Regular",
            boldness="0",
            skew="0",
            font_index="1",
            font_size="12",
            thickness="1.5",
            embeded_depth="0.4",
            rotate_angle="15",
            text_gap="0.2",
            bold="1",
            italic="0",
            surface_type="2",
            hit_mesh="11",
            hit_position="1 2 3",
            hit_normal="0 0 1",
        )
        part.shape_config = ShapeConfig(
            filepath="shape.svg",
            filepath3mf="Metadata/shape.svg",
            scale="1",
            depth="0.8",
            use_surface="1",
            unhealed="1",
            transform="1 0 0 0 1 0 0 0 1 0 0 0",
            svg_file_data=b"<svg viewBox='0 0 10 10'></svg>",
        )
        obj.parts.append(part)
        proj.objects.append(obj)
        proj.mark_modified("model_settings")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.3mf"
            proj.save(str(path))

            with zipfile.ZipFile(path, "r") as archive:
                content_types = archive.read("[Content_Types].xml").decode("utf-8")
                model_settings = archive.read("Metadata/model_settings.config").decode("utf-8")
                self.assertIn('Extension="gcode" ContentType="text/x.gcode"', content_types)
                self.assertIn("<text_info ", model_settings)
                self.assertIn("<BambuStudioShape ", model_settings)
                self.assertEqual(archive.read("Metadata/shape.svg"), b"<svg viewBox='0 0 10 10'></svg>")

            loaded = Bambu3MF.load(str(path))
            loaded_part = loaded.objects[0].parts[0]
            self.assertIsNotNone(loaded_part.text_info)
            self.assertEqual(loaded_part.text_info.text, "HELLO")
            self.assertEqual(loaded_part.text_info.surface_type, "2")
            self.assertIsNotNone(loaded_part.shape_config)
            self.assertEqual(loaded_part.shape_config.filepath3mf, "Metadata/shape.svg")
            self.assertEqual(loaded_part.shape_config.svg_file_data, b"<svg viewBox='0 0 10 10'></svg>")

    def test_production_relationships_and_identifiers_round_trip(self):
        proj = Bambu3MF.new()
        root_obj = ModelObject(id=10, name="assembly")
        root_obj.components.append(ComponentRef(objectid=1, path="3D/Objects/object_7.model"))
        proj.objects.append(root_obj)
        proj.sub_models["3D/Objects/object_7.model"] = [ModelObject(id=1)]
        proj.build_items.append(BuildItem(objectid=10))
        proj.build_items.append(BuildItem(objectid=1, path="3D/Objects/object_7.model"))
        proj.gcode_files["Metadata/plate_1.gcode"] = b"G1 X1 Y1"
        proj.mark_modified("main_model", "3D/Objects/object_7.model", "model_settings")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "production.3mf"
            proj.save(str(path))

            with zipfile.ZipFile(path, "r") as archive:
                main_model = archive.read("3D/3dmodel.model").decode("utf-8")
                sub_model = archive.read("3D/Objects/object_7.model").decode("utf-8")
                model_rels = archive.read("3D/_rels/3dmodel.model.rels").decode("utf-8")
                model_config_rels = archive.read("Metadata/_rels/model_settings.config.rels").decode("utf-8")

                self.assertIn('requiredextensions="p"', main_model)
                self.assertIn('p:UUID="0000000a-61cb-4c03-9d28-80fed5dfa1dc"', main_model)
                self.assertIn('p:path="3D/Objects/object_7.model" objectid="1"', main_model)
                self.assertIn('p:UUID="000a0000-b206-40ff-9872-83e8017abed1"', main_model)
                self.assertIn('p:UUID="0000000a-b1ec-4553-aec9-835e5b724bb4"', main_model)
                self.assertIn('p:path="3D/Objects/object_7.model"', main_model)
                self.assertIn('p:UUID="00000001-b1ec-4553-aec9-835e5b724bb4"', main_model)
                self.assertIn('p:UUID="000a0000-81cb-4c03-9d28-80fed5dfa1dc"', sub_model)
                self.assertIn('Target="/3D/Objects/object_7.model"', model_rels)
                self.assertIn('Target="/Metadata/plate_1.gcode"', model_config_rels)

            loaded = Bambu3MF.load(str(path))
            self.assertEqual(loaded.objects[0].backup_id, 10)
            self.assertEqual(loaded.build_items[0].uuid, "0000000a-b1ec-4553-aec9-835e5b724bb4")
            self.assertEqual(loaded.build_items[1].path, "3D/Objects/object_7.model")

    def test_slice_info_and_cut_info_round_trip(self):
        proj = Bambu3MF.new()
        proj.slice_info = SliceInfo(
            client_type="slicer",
            client_version="9.9.9",
            plates=[
                SlicePlate(
                    index=1,
                    printer_model_id="X1C",
                    nozzle_diameters="0.4",
                    timelapse_type="0",
                    prediction="1h",
                    weight="12g",
                    outside="false",
                    support_used="true",
                    label_object_enabled="false",
                    extruder_type="steel",
                    nozzle_volume_type="standard",
                    nozzle_types="0.4",
                    first_layer_time="30",
                    skipped="false",
                    filaments=[
                        SliceFilament(
                            id=1,
                            type="PLA",
                            color="#FFFFFF",
                            used_m="1.2",
                            used_g="3.4",
                            used_for_support="1",
                            used_for_object="1",
                            tray_info_idx="A1",
                            group_id="1",
                            nozzle_diameter="0.4",
                            nozzle_volume_type="standard",
                        )
                    ],
                    objects=[{"id": "10", "instance_id": "0"}],
                    warnings=[{"msg": "careful", "code": "W1"}],
                    layer_filament_lists=[{"filament_list": "1", "layer_ranges": "1-5"}],
                )
            ],
        )
        proj.cut_objects = [
            CutObject(
                object_id=10,
                cut_id=5,
                check_sum=7,
                connectors_cnt=1,
                connectors=[
                    CutConnector(
                        volume_id=11,
                        type=2,
                        radius=3.0,
                        height=4.0,
                        r_tolerance=0.1,
                        h_tolerance=0.2,
                    )
                ],
            )
        ]
        proj.mark_modified("slice_info", "cut_info")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "metadata.3mf"
            proj.save(str(path))

            with zipfile.ZipFile(path, "r") as archive:
                slice_info = archive.read("Metadata/slice_info.config").decode("utf-8")
                cut_info = archive.read("Metadata/cut_information.xml").decode("utf-8")
                self.assertIn("<layer_filament_lists>", slice_info)
                self.assertIn('layer_ranges="1-5"', slice_info)
                self.assertIn('used_for_support="1"', slice_info)
                self.assertIn('tray_info_idx="A1"', slice_info)
                self.assertIn('connectors_cnt="1"', cut_info)
                self.assertIn('r_tolerance="0.1"', cut_info)
                self.assertIn('h_tolerance="0.2"', cut_info)

            loaded = Bambu3MF.load(str(path))
            self.assertIsNotNone(loaded.slice_info)
            plate = loaded.slice_info.plates[0]
            self.assertEqual(plate.layer_filament_lists[0]["layer_ranges"], "1-5")
            self.assertEqual(plate.filaments[0].tray_info_idx, "A1")
            self.assertEqual(loaded.cut_objects[0].connectors[0].r_tolerance, 0.1)
            self.assertEqual(loaded.cut_objects[0].connectors[0].h_tolerance, 0.2)

    def test_material_color_groups_round_trip(self):
        proj = Bambu3MF.new()
        proj.color_groups = [
            ColorGroup(
                id=5,
                colors=[ColorDef(color="#FF0000FF"), ColorDef(color="#00FF00FF")],
            )
        ]

        obj = ModelObject(id=21, name="painted")
        obj.mesh = ObjectMesh(
            vertices=[
                Vertex(0.0, 0.0, 0.0),
                Vertex(1.0, 0.0, 0.0),
                Vertex(0.0, 1.0, 0.0),
            ],
            triangles=[Triangle(v1=0, v2=1, v3=2, pid=5, p1=0, p2=1, p3=0)],
        )
        proj.objects.append(obj)
        proj.build_items.append(BuildItem(objectid=21))
        proj.mark_modified("main_model")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "materials.3mf"
            proj.save(str(path))

            with zipfile.ZipFile(path, "r") as archive:
                main_model = archive.read("3D/3dmodel.model").decode("utf-8")
                self.assertIn('xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02"', main_model)
                self.assertIn('<m:colorgroup id="5">', main_model)
                self.assertIn('<m:color color="#FF0000FF"/>', main_model)
                self.assertIn('pid="5"', main_model)
                self.assertIn('p1="0"', main_model)
                self.assertIn('p2="1"', main_model)
                self.assertIn('p3="0"', main_model)
                self.assertNotIn('pindex=', main_model)

            loaded = Bambu3MF.load(str(path))
            self.assertEqual(loaded.color_groups[0].id, 5)
            self.assertEqual(loaded.color_groups[0].colors[1].color, "#00FF00FF")
            triangle = loaded.objects[0].mesh.triangles[0]
            self.assertEqual(triangle.pid, 5)
            self.assertEqual(triangle.p1, 0)
            self.assertEqual(triangle.p2, 1)
            self.assertEqual(triangle.p3, 0)
            self.assertIsNone(triangle.pindex)

    def test_thumbnail_relationship_generation(self):
        proj = Bambu3MF.new()
        proj.objects.append(ModelObject(id=1))
        proj.build_items.append(BuildItem(objectid=1))
        proj.thumbnails["Metadata/plate_1.png"] = b"main"
        proj.thumbnails["Metadata/plate_1_small.png"] = b"small"
        proj.mark_modified("main_model")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "thumbs.3mf"
            proj.save(str(path))

            with zipfile.ZipFile(path, "r") as archive:
                rels = archive.read("_rels/.rels").decode("utf-8")
                self.assertIn('Target="/Metadata/plate_1.png" Id="rel-2"', rels)
                self.assertIn('Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"', rels)
                self.assertIn('Target="/Metadata/plate_1.png" Id="rel-4"', rels)
                self.assertIn('Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"', rels)
                self.assertIn('Target="/Metadata/plate_1_small.png" Id="rel-5"', rels)
                self.assertIn('Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-small"', rels)

            loaded = Bambu3MF.load(str(path))
            self.assertEqual(loaded.thumbnails["Metadata/plate_1.png"], b"main")
            self.assertEqual(loaded.thumbnails["Metadata/plate_1_small.png"], b"small")


if __name__ == "__main__":
    unittest.main()