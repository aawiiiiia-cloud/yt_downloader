"""Merge image manifests across workers without serializing media transfers."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


@contextmanager
def summary_lock(target: Path, check_cancel=None, timeout: float = 30):
    """A short cross-process lock; Windows releases it even if a worker dies."""
    check_cancel = check_cancel or (lambda: None)
    deadline = time.monotonic() + timeout
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.ReleaseMutex.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        key = hashlib.sha256(str(target.resolve()).casefold().encode('utf-8')).hexdigest()
        handle = kernel.CreateMutexW(None, False, 'Local\\media-dl-summary-' + key)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            while True:
                check_cancel()
                result = kernel.WaitForSingleObject(handle, 50)
                if result in (0, 0x80):  # WAIT_OBJECT_0 or an abandoned owner.
                    acquired = True
                    break
                if result != 258:
                    raise ctypes.WinError(ctypes.get_last_error())
                if time.monotonic() >= deadline:
                    raise TimeoutError('等待素材清单写入超时，请重试')
            check_cancel()
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        import fcntl
        # Keep the inode: unlinking a lock while a neighbour owns it can create
        # two independent locks. Windows uses a named mutex and needs no file.
        with (target.parent / ('.' + target.name + '.lock')).open('a+b') as handle:
            while True:
                check_cancel()
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('等待素材清单写入超时，请重试')
                    time.sleep(.05)
            try:
                check_cancel()
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def write_image_summary(target: Path, metadata: dict, images: list[dict], identity: tuple[str, ...], check_cancel=None):
    """Merge completed image records; publish only a complete, private-free JSON."""
    target = Path(target)
    check_cancel = check_cancel or (lambda: None)
    with summary_lock(target, check_cancel):
        previous = {}
        try:
            if target.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('已有素材清单过大，未覆盖原文件')
            previous = json.loads(target.read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, UnicodeError) as error:
            raise ValueError('已有素材清单损坏，未覆盖原文件') from error
        if not isinstance(previous, dict) or (previous and any(previous.get(key) != metadata.get(key) for key in identity)):
            raise ValueError('保存目录中的素材清单属于其他作品，未覆盖原文件')
        rows = {}
        old_images = previous.get('images', [])
        if not isinstance(old_images, list):
            raise ValueError('已有素材清单图片记录无效，未覆盖原文件')
        # Older XHS/Behance summaries had only a count. Recover their saved
        # numeric image names when introducing per-image records.
        if previous and 'images' not in previous and 'image_count' in previous:
            extensions = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.heic', '.heif', '.bmp', '.tif', '.tiff'}
            for file in target.parent.iterdir():
                if file.is_file() and file.suffix.lower() in extensions and file.stem.isascii() and file.stem.isdigit():
                    index = int(file.stem)
                    if 0 < index < 1000000:
                        rows[index] = {'index': index}
        for row in [*old_images, *images]:
            if not isinstance(row, dict) or type(row.get('index')) is not int or not 0 < row['index'] < 1000000:
                raise ValueError('素材清单图片编号无效')
            existing = rows.get(row['index'], {})
            merged = {**existing, **row}
            if 'names' in existing and 'names' in row:
                merged['names'] = list(dict.fromkeys([*existing['names'], *row['names']]))
            rows[row['index']] = merged
        payload = {**previous, **metadata, 'image_count': len(rows), 'images': [rows[index] for index in sorted(rows)]}
        check_cancel()
        descriptor, name = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            check_cancel()
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
