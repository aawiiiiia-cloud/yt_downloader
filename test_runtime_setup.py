from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from distribution import runtime_setup as runtime
from distribution.build_source_bundle import command_file


class RuntimeSetupTests(unittest.TestCase):
    def test_working_packages_are_never_reinstalled_or_upgraded(self):
        with mock.patch.object(runtime, 'package_state', return_value={}), mock.patch.object(runtime.subprocess, 'run') as install:
            runtime.ensure_packages({'Pillow':['11.3.0','PIL.Image']})
        install.assert_not_called()

    def test_broken_package_keeps_its_version_and_uses_alternate_index(self):
        damaged={'Pillow':['11.3.0','无法导入']}
        with mock.patch.object(runtime,'package_state',side_effect=[damaged,{}]), mock.patch.object(
            runtime.subprocess,'run',side_effect=[subprocess.CompletedProcess([],1),subprocess.CompletedProcess([],0)]) as install:
            runtime.ensure_packages({'Pillow':['11.3.0','PIL.Image'],'yt-dlp':['2026.8.19','yt_dlp']})
        first,second=[call.args[0] for call in install.call_args_list]
        self.assertIn('Pillow==11.3.0',first)
        self.assertNotIn('yt-dlp==2026.8.19',first)
        self.assertNotIn('-U',first)
        self.assertIn('https://pypi.org/simple',first)
        self.assertIn('https://pypi.tuna.tsinghua.edu.cn/simple',second)

    def test_failed_install_never_changes_the_previous_component(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); target=root/'node';target.mkdir();(target/'keep').write_text('old')
            def build(stage):stage.mkdir();(stage/'incomplete').write_text('bad')
            with self.assertRaises(RuntimeError):runtime.publish(target,build,lambda _path:False,(root,))
            self.assertEqual((target/'keep').read_text(),'old')
            self.assertEqual(list(root.iterdir()),[target])

    def test_publish_failure_restores_backup(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);target=root/'node';target.mkdir();(target/'keep').write_text('old')
            rename=Path.rename
            def fail_stage(path,destination):
                if '.stage-' in path.name:raise PermissionError('locked')
                return rename(path,destination)
            with mock.patch.object(Path,'rename',fail_stage),self.assertRaises(PermissionError):
                runtime.publish(target,lambda stage:stage.mkdir(),lambda _path:True,(root,))
            self.assertEqual((target/'keep').read_text(),'old')
            self.assertEqual(list(root.iterdir()),[target])

    def test_cleanup_rejects_parent_and_external_directory(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);managed=root/'tools';managed.mkdir();outside=root/'outside';outside.mkdir()
            for path in (managed,outside):
                with self.assertRaises(RuntimeError):runtime.remove_managed(path,(managed,))
            self.assertTrue(outside.is_dir())

    def test_incomplete_electron_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'package-lock.json').write_text(json.dumps({'packages':{'node_modules/electron':{'version':'45.0.0'}}}))
            modules=root/'node_modules';(modules/'electron').mkdir(parents=True)
            (modules/'electron/package.json').write_text('{"version":"45.0.0"}')
            with mock.patch.object(runtime,'run') as execute:self.assertFalse(runtime.electron_ready(modules,root))
            execute.assert_not_called()

    def test_distribution_buttons_use_bootstrap_even_when_python_is_missing(self):
        for mode in ('','--legacy','--check'):
            result=command_file(mode)
            self.assertIn('..\\代码\\start',result)
            self.assertNotIn('media_python',result)

    def test_importable_package_with_missing_javascript_is_repaired(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);package=root/'fake_ejs';package.mkdir();(package/'__init__.py').touch()
            info=root/'fake_ejs-0.8.0.dist-info';info.mkdir()
            (info/'METADATA').write_text('Metadata-Version: 2.1\nName: fake-ejs\nVersion: 0.8.0\n')
            (info/'RECORD').write_text('fake_ejs/core.min.js,,\n')
            with mock.patch.dict(os.environ,{'PYTHONPATH':str(root)}):
                bad=runtime.package_state({'fake-ejs':['0.8.0','fake_ejs']})
            self.assertIn('fake-ejs',bad)

    def test_electron_stage_is_validated_under_its_own_directory(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);modules=root/'.node_modules.stage-test';electron=modules/'electron';dist=electron/'dist'
            dist.mkdir(parents=True)
            (root/'package-lock.json').write_text(json.dumps({'packages':{'node_modules/electron':{'version':'45.0.0'}}}))
            (electron/'package.json').write_text('{"version":"45.0.0"}')
            for leaf in ('index.js','cli.js','checksums.json'):(electron/leaf).touch()
            (electron/'path.txt').write_text('electron.exe')
            (dist/'version').write_text('45.0.0')
            for leaf in ('electron.exe','icudtl.dat','resources.pak','locales/en-US.pak','resources/default_app.asar'):
                file=dist/leaf;file.parent.mkdir(parents=True,exist_ok=True);file.touch()
            with mock.patch.object(runtime,'run',return_value=subprocess.CompletedProcess([],0,stdout='45.0.0\n')):
                self.assertTrue(runtime.electron_ready(modules,root))

    @unittest.skipUnless(os.name=='nt','Windows PowerShell bootstrap')
    def test_python_recovery_rejects_archive_path_traversal(self):
        with tempfile.TemporaryDirectory(prefix='media dl 测试 ') as name:
            root=Path(name);code=root/'代码';scripts=code/'distribution';scripts.mkdir(parents=True)
            startup=root/'启动程序';startup.mkdir();(startup/'启动.py').touch()
            tools=root/'依赖工具';cache=tools/'repair-cache';cache.mkdir(parents=True)
            source=Path(__file__).resolve().parent
            shutil.copy2(source/'distribution/ensure-runtime.ps1',scripts/'ensure-runtime.ps1')
            archive=cache/'python.zip'
            with zipfile.ZipFile(archive,'w') as target:target.writestr('../escape.txt','do not extract')
            (cache/'python.json').write_text(json.dumps({'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}))
            powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
            task=subprocess.run([str(powershell),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(scripts/'ensure-runtime.ps1'),'-CheckOnly'],
                capture_output=True,timeout=25)
            self.assertNotEqual(task.returncode,0)
            self.assertFalse((tools/'escape.txt').exists())
            self.assertFalse((root/'escape.txt').exists())
            self.assertIn('越界路径',task.stdout.decode('utf-8',errors='replace'))


if __name__=='__main__':unittest.main()
