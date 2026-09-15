from __future__ import annotations

import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import media_batch
from media_batch import MediaRecord, ScanOptions


class TestMediaDiscovery(unittest.TestCase):
    def test_discover_media_classifies_and_sorts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "b.mp4").write_bytes(b"video")
            (root / "a.JPG").write_bytes(b"image")
            (root / "ignore.txt").write_text("x")
            nested = root / "nested"
            nested.mkdir()
            (nested / "c.webp").write_bytes(b"image")
            flat = media_batch.discover_media(root, recursive=False)
            deep = media_batch.discover_media(root, recursive=True)
        self.assertEqual([kind for _path, kind in flat], ["图片", "视频"])
        self.assertEqual(len(deep), 3)

    def test_discover_media_excludes_output_folder(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "keep.jpg").write_bytes(b"x")
            output = root / "疑似问题文件"
            output.mkdir()
            (output / "old.jpg").write_bytes(b"x")
            found = media_batch.discover_media(root, True, (output,))
        self.assertEqual([path.name for path, _kind in found], ["keep.jpg"])


class TestVideoMetadata(unittest.TestCase):
    def test_parse_probe_falls_back_to_container_bitrate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "clip.mp4"
            path.write_bytes(b"x" * 100)
            record = media_batch._parse_probe(path, {
                "streams": [{
                    "codec_type": "video", "width": 1920, "height": 1080,
                    "codec_name": "h264", "avg_frame_rate": "30000/1001",
                }],
                "format": {"duration": "10.5", "bit_rate": "2500000"},
            })
        self.assertEqual(record.resolution, "1920×1080")
        self.assertAlmostEqual(record.fps, 29.97, places=2)
        self.assertEqual(record.bitrate_kbps, 2500)
        self.assertAlmostEqual(record.bitrate_density, 0.0402, places=3)

    def test_auto_bitrate_quality_flags_low_density_and_low_fps(self) -> None:
        probe_json = (
            '{"streams":[{"codec_type":"video","width":3840,"height":2160,'
            '"codec_name":"h264","avg_frame_rate":"15/1","bit_rate":"1000000"}],'
            '"format":{"duration":"10"}}'
        )
        completed = media_batch.subprocess.CompletedProcess(["ffprobe"], 0, probe_json, "")
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "clip.mp4"
            path.write_bytes(b"x")
            with mock.patch.object(media_batch, "_run_process", return_value=completed):
                record = media_batch.inspect_video(
                    path, ScanOptions(check_video_bitrate=True, detect_black_bars=False)
                )
        self.assertTrue(any("过度压缩" in item for item in record.review_issues))
        self.assertTrue(any("可能不流畅" in item for item in record.review_issues))

    def test_parse_probe_marks_missing_video_stream(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "audio.mp4"
            path.write_bytes(b"x")
            record = media_batch._parse_probe(path, {"streams": []})
        self.assertTrue(record.suspicious)
        self.assertEqual(record.issues, ["没有可识别的视频轨道"])

    def test_video_checks_are_independent_and_black_bar_status_is_explicit(self) -> None:
        probe_json = (
            '{"streams":[{"codec_type":"video","width":640,"height":360,'
            '"bit_rate":"80000"}],"format":{"duration":"10"}}'
        )
        completed = media_batch.subprocess.CompletedProcess(
            ["ffprobe"], 0, probe_json, ""
        )
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "clip.mp4"
            path.write_bytes(b"x")
            disabled = ScanOptions(
                check_video_resolution=False, video_min_width=3840,
                video_min_height=2160, check_video_bitrate=False,
                video_min_bitrate_kbps=9999, detect_black_bars=False,
            )
            with mock.patch.object(media_batch, "_run_process", return_value=completed):
                record = media_batch.inspect_video(path, disabled)
            self.assertFalse(record.issues)
            self.assertEqual(record.black_bar_status, "未检测")

            enabled = ScanOptions(detect_black_bars=True)
            with (
                mock.patch.object(media_batch, "_run_process", return_value=completed),
                mock.patch.object(
                    media_batch, "detect_black_bars", return_value="crop=640:320:0:20"
                ),
            ):
                record = media_batch.inspect_video(path, enabled)
            self.assertEqual(record.black_bar_status, "上 20 · 下 20 · 左 0 · 右 0 px")
            self.assertEqual(record.crop_suggestion, "crop=640:320:0:20")

    def test_duplicate_only_video_skips_ffprobe_and_computes_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "clip.mp4"
            path.write_bytes(b"same video")
            options = ScanOptions(
                check_image_resolution=False,
                check_video_resolution=False,
                check_video_bitrate=False,
                detect_black_bars=False,
                detect_duplicates=True,
            )
            with mock.patch.object(media_batch, "_run_process") as run:
                record = media_batch.inspect_video(path, options)
        run.assert_not_called()
        self.assertTrue(record.sha256)
        self.assertEqual(record.duplicate_status, "未发现")


class TestImageDedupe(unittest.TestCase):
    def test_exact_and_near_duplicates_keep_better_image(self) -> None:
        records = [
            MediaRecord(Path("small.jpg"), "图片", size=10, width=100, height=100,
                        sha256="same", perceptual_hash=0),
            MediaRecord(Path("large.jpg"), "图片", size=20, width=200, height=200,
                        sha256="same", perceptual_hash=0),
            MediaRecord(Path("near.jpg"), "图片", size=8, width=100, height=100,
                        sha256="other", perceptual_hash=1),
        ]
        fake_dhash = types.SimpleNamespace(
            get_num_bits_different=lambda left, right: (left ^ right).bit_count()
        )
        with mock.patch.dict(sys.modules, {"dhash": fake_dhash}):
            media_batch.mark_image_duplicates(records, distance=1)
        self.assertIn("完全重复", records[0].issues[0])
        self.assertIn("完全重复", records[0].duplicate_status)
        self.assertIn("保留项", records[1].duplicate_status)
        self.assertFalse(records[1].issues)
        self.assertTrue(any("疑似相似图" in issue for issue in records[2].review_issues))

    def test_near_duplicate_compares_adjacent_aspect_buckets(self) -> None:
        records = [
            MediaRecord(Path("square.jpg"), "图片", width=100, height=100,
                        sha256="a", perceptual_hash=0),
            MediaRecord(Path("slightly-wide.jpg"), "图片", width=104, height=100,
                        sha256="b", perceptual_hash=1),
        ]
        fake_dhash = types.SimpleNamespace(
            get_num_bits_different=lambda left, right: (left ^ right).bit_count()
        )
        with mock.patch.dict(sys.modules, {"dhash": fake_dhash}):
            media_batch.mark_image_duplicates(records, distance=1)
        self.assertEqual(sum(bool(item.review_issues) for item in records), 1)

    def test_exact_only_preset_skips_visual_similarity(self) -> None:
        records = [
            MediaRecord(Path("a.jpg"), "图片", width=100, height=100,
                        sha256="a", perceptual_hash=0, duplicate_status="未发现"),
            MediaRecord(Path("b.jpg"), "图片", width=100, height=100,
                        sha256="b", perceptual_hash=0, duplicate_status="未发现"),
        ]
        fake_dhash = types.SimpleNamespace(
            get_num_bits_different=lambda left, right: (left ^ right).bit_count()
        )
        with mock.patch.dict(sys.modules, {"dhash": fake_dhash}):
            media_batch.mark_image_duplicates(records, distance=-1)
        self.assertFalse(any(item.review_issues for item in records))

    def test_exact_duplicate_detection_also_covers_videos(self) -> None:
        records = [
            MediaRecord(Path("a.mp4"), "视频", size=10, sha256="same",
                        duplicate_status="未发现"),
            MediaRecord(Path("b.mp4"), "视频", size=10, sha256="same",
                        duplicate_status="未发现"),
        ]
        media_batch.mark_image_duplicates(records, distance=-1)
        self.assertEqual(sum(bool(item.issues) for item in records), 1)
        self.assertTrue(all("完全重复" in item.duplicate_status for item in records))


class TestScanScope(unittest.TestCase):
    def test_scan_skips_category_without_applicable_checks(self) -> None:
        options = ScanOptions(
            check_image_resolution=True,
            check_video_resolution=False,
            check_video_bitrate=False,
            detect_black_bars=False,
            detect_duplicates=False,
        )
        fake_files = [(Path("a.jpg"), "图片"), (Path("b.mp4"), "视频")]
        image_record = MediaRecord(Path("a.jpg"), "图片")
        with (
            mock.patch.object(media_batch, "discover_media", return_value=fake_files),
            mock.patch.object(media_batch, "inspect_image", return_value=image_record) as image,
            mock.patch.object(media_batch, "inspect_video") as video,
            tempfile.TemporaryDirectory() as raw,
        ):
            records = media_batch.scan_media(Path(raw), options)
        image.assert_called_once()
        video.assert_not_called()
        self.assertEqual(records, [image_record])

    def test_scan_rejects_when_every_check_is_off(self) -> None:
        options = ScanOptions(
            check_image_resolution=False,
            check_video_resolution=False,
            check_video_bitrate=False,
            detect_black_bars=False,
            detect_duplicates=False,
        )
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ValueError, "至少选择"):
                media_batch.scan_media(Path(raw), options)


class TestFileOperations(unittest.TestCase):
    def test_safe_move_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "from" / "same.jpg"
            source.parent.mkdir()
            source.write_bytes(b"new")
            target = root / "to"
            target.mkdir()
            (target / "same.jpg").write_bytes(b"old")
            moved = media_batch.safe_move(source, target)
            self.assertEqual((target / "same.jpg").read_bytes(), b"old")
            self.assertEqual(moved.read_bytes(), b"new")
            self.assertEqual(moved.name, "same (1).jpg")

    def test_move_suspicious_splits_image_and_video(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "a.jpg"
            video = root / "b.mp4"
            image.write_bytes(b"a")
            video.write_bytes(b"b")
            records = [
                MediaRecord(image, "图片", issues=["低分辨率"]),
                MediaRecord(video, "视频", issues=[]),
            ]
            moved = media_batch.move_suspicious(records, root / "issues")
            self.assertEqual(len(moved), 1)
            self.assertTrue((root / "issues" / "图片" / "a.jpg").exists())
            self.assertTrue(video.exists())
            self.assertFalse(records[0].suspicious)
            self.assertEqual(media_batch.move_suspicious(records, root / "issues"), [])

    def test_batch_rename_uses_two_phase_plan(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "b.jpg"
            second = root / "a.jpg"
            first.write_bytes(b"b")
            second.write_bytes(b"a")
            records = [MediaRecord(first, "图片"), MediaRecord(second, "图片")]
            changed = media_batch.batch_rename_images(records, "{index:02d}_{name}")
            self.assertEqual(len(changed), 2)
            self.assertTrue((root / "01_b.jpg").exists())
            self.assertTrue((root / "02_a.jpg").exists())

    def test_plain_rename_text_becomes_prefix_with_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            paths = [root / "a.jpg", root / "b.jpg"]
            for path in paths:
                path.write_bytes(b"x")
            records = [MediaRecord(path, "图片") for path in paths]
            media_batch.batch_rename_images(records, "下载_测试")
            self.assertTrue((root / "下载_测试_0001.jpg").exists())
            self.assertTrue((root / "下载_测试_0002.jpg").exists())

    def test_rename_staging_does_not_repeat_long_source_name(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / (("很长的名字" * 35) + ".jpg")
            # Keep the synthetic source portable while still checking the staging plan.
            source = root / (source.name[:180] + ".jpg")
            source.write_bytes(b"x")
            seen_targets: list[str] = []
            real_rename = media_batch.os.rename

            def capture(source_path: Path, target_path: Path) -> None:
                seen_targets.append(Path(target_path).name)
                real_rename(source_path, target_path)

            with mock.patch.object(media_batch.os, "rename", side_effect=capture):
                media_batch.batch_rename_images([MediaRecord(source, "图片")], "新图片")
            self.assertTrue(any(name.startswith(".rename-") for name in seen_targets))
            self.assertFalse(any(len(name) > 240 for name in seen_targets))

    def test_batch_rename_rolls_back_if_commit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "a.jpg"
            second = root / "b.jpg"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            records = [MediaRecord(first, "图片"), MediaRecord(second, "图片")]
            real_rename = media_batch.os.rename
            calls = 0

            def fail_fourth(source: Path, target: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise PermissionError("simulated")
                real_rename(source, target)

            with mock.patch.object(media_batch.os, "rename", side_effect=fail_fourth):
                with self.assertRaises(PermissionError):
                    media_batch.batch_rename_images(records, "new_{index}")
            self.assertEqual(first.read_bytes(), b"a")
            self.assertEqual(second.read_bytes(), b"b")
            self.assertEqual(list(root.glob("*.rename")), [])

    def test_batch_rename_rolls_back_name_exchange_chain(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            paths = [root / name for name in ("1.jpg", "2.jpg", "3.jpg")]
            for index, path in enumerate(paths, 1):
                path.write_bytes(str(index).encode())
            records = [
                MediaRecord(paths[1], "图片"),
                MediaRecord(paths[2], "图片"),
                MediaRecord(paths[0], "图片"),
            ]
            real_rename = media_batch.os.rename
            calls = 0

            def fail_second_commit(source: Path, target: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 5:
                    raise PermissionError("simulated chain failure")
                real_rename(source, target)

            with mock.patch.object(media_batch.os, "rename", side_effect=fail_second_commit):
                with self.assertRaises(PermissionError):
                    media_batch.batch_rename_images(records, "{index}.jpg")
            self.assertEqual([path.read_bytes() for path in paths], [b"1", b"2", b"3"])
            self.assertEqual(list(root.glob("*.rename")), [])

    def test_cancelled_move_does_not_touch_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "a.jpg"
            source.write_bytes(b"a")
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(media_batch.OperationCancelled):
                media_batch.safe_move(source, root / "to", cancel)
            self.assertTrue(source.exists())

    def test_review_item_moves_only_with_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "near.jpg"
            source.write_bytes(b"a")
            record = MediaRecord(source, "图片", review_issues=["疑似相似图"])
            self.assertEqual(media_batch.move_suspicious([record], root / "to"), [])
            moved = media_batch.move_suspicious(
                [record], root / "to", include_review=True
            )
            self.assertEqual(len(moved), 1)


class TestHelpers(unittest.TestCase):
    def test_crop_suggestion_is_presented_as_four_margins(self) -> None:
        record = MediaRecord(Path("video.mp4"), "视频", width=3840, height=2160)
        self.assertEqual(
            media_batch.format_crop_margins(record, "crop=2824:2160:504:0"),
            "上 0 · 下 0 · 左 504 · 右 512 px",
        )

    def test_crop_boxes(self) -> None:
        self.assertEqual(media_batch.center_crop_box(1920, 1080, "1:1"), (420, 0, 1500, 1080))
        self.assertEqual(media_batch.center_crop_box(1000, 1000, "16:9"), (0, 219, 1000, 781))

    def test_render_rename_sanitizes_windows_name(self) -> None:
        record = MediaRecord(Path("bad:name.jpg"), "图片", width=10, height=20)
        rendered = media_batch.render_rename("{index:04d}_{name}_{width}x{height}", record, 3)
        self.assertEqual(rendered, "0003_bad_name_10x20.jpg")

    def test_render_rename_never_changes_real_extension(self) -> None:
        record = MediaRecord(Path("photo.jpg"), "图片")
        self.assertEqual(media_batch.render_rename("export_{index}.png", record, 1), "export_1.jpg")

    def test_default_options_do_not_mark_zero_resolution_image(self) -> None:
        options = ScanOptions()
        self.assertEqual(options.image_min_width, 0)

    def test_resolution_threshold_accepts_equivalent_vertical_media(self) -> None:
        self.assertFalse(media_batch._below_resolution(1080, 1920, 1280, 720))
        self.assertTrue(media_batch._below_resolution(720, 960, 1280, 720))

    def test_probe_ignores_cover_stream_and_applies_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "vertical.mp4"
            path.write_bytes(b"x")
            record = media_batch._parse_probe(path, {
                "streams": [
                    {"codec_type": "video", "width": 600, "height": 600,
                     "disposition": {"attached_pic": 1}},
                    {"codec_type": "video", "width": 1920, "height": 1080,
                     "tags": {"rotate": "90"}},
                ],
                "format": {},
            })
        self.assertEqual((record.width, record.height), (1080, 1920))


if __name__ == "__main__":
    unittest.main()
