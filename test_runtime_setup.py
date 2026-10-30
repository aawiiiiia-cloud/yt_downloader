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
from distribution import launcher
from distribution.build_source_bundle import command_file


def make_windows_python_fixture(directory: Path, *, system_site_packages: bool) -> None:
    import sys
    import venv
    # A base python.exe needs its adjacent DLLs; copying only that executable
    # works accidentally when the test runner is already a venv redirector.
    # Use the standard-library redirector in both source and portable runtimes.
    scripts = Path(venv.__file__).parent / 'scripts' / 'nt'
    redirector = next(path for path in (scripts / 'venvlauncher.exe', scripts / 'python.exe') if path.is_file())
    shutil.copy2(redirector, directory / 'python.exe')
    (directory / 'pyvenv.cfg').write_text(
        f'home = {sys.base_prefix}\ninclude-system-site-packages = {str(system_site_packages).lower()}\n')


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
            self.assertIn('distribution\\start-runtime.cmd',result)
            self.assertIn(':missing',result)
            self.assertTrue(result.isascii())
            self.assertNotIn('\n',result.replace('\r\n',''))

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
    def test_direct_python_gui_routes_before_importing_business_modules(self):
        with tempfile.TemporaryDirectory(prefix='media dl ') as name:
            root=Path(name);(root/'distribution').mkdir()
            shutil.copy2(Path(__file__).with_name('yt_dlp_gui.py'),root/'yt_dlp_gui.py')
            (root/'distribution/launcher.py').write_text("import sys\nassert sys.argv[1:]==['--legacy']\nprint('project-runtime-selected')",encoding='utf-8')
            env=os.environ.copy();env.pop('MEDIA_DL_RUNTIME_PREPARED',None)
            import sys
            task=subprocess.run([sys.executable,'-B',str(root/'yt_dlp_gui.py')],env=env,capture_output=True,timeout=25)
            self.assertEqual(task.returncode,0,task.stderr)
            self.assertIn(b'project-runtime-selected',task.stdout)

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

    def make_cached_fixture(self, root):
        code=root/'代码';tools=root/'依赖工具';code.mkdir();tools.mkdir()
        for leaf in ('python','node','ffmpeg','bgutil-provider','node_modules'):
            (tools/leaf).mkdir();(tools/leaf/'runtime.bin').write_bytes(b'healthy')
        for relative in ('package.json','package-lock.json','bootstrap.py','pot_provider.py','provider_setup.py',
                'distribution/runtime-lock.json','distribution/runtime_setup.py','distribution/launcher.py',
                'distribution/start-runtime.cmd'):
            file=code/relative;file.parent.mkdir(exist_ok=True)
            file.write_text(json.dumps({'pythonPackages':{}}) if relative.endswith('runtime-lock.json') else 'input')
        return code,tools

    def test_cache_detects_missing_data_same_size_changes_relocation_and_expiry(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);code,tools=self.make_cached_fixture(root)
            runtime.save_runtime_cache(code,tools,True)
            self.assertTrue(runtime.cached_runtime(code,tools,True))
            with mock.patch.dict(os.environ,{'COMPUTERNAME':'different-recipient-machine'}):
                self.assertFalse(runtime.cached_runtime(code,tools,True))
            self.assertFalse(runtime.cached_runtime(code,tools,False))
            file=tools/'python/runtime.bin';old=file.read_bytes();timestamp=file.stat().st_mtime_ns
            file.write_bytes(b'changed');os.utime(file,ns=(timestamp,timestamp+2_000_000_000))
            self.assertFalse(runtime.cached_runtime(code,tools,True))
            file.write_bytes(old);runtime.save_runtime_cache(code,tools,True)
            file.unlink();self.assertFalse(runtime.cached_runtime(code,tools,True))
            file.write_bytes(old);runtime.save_runtime_cache(code,tools,True)
            moved=root/'new';shutil.copytree(code,moved/'代码');shutil.copytree(tools,moved/'依赖工具')
            self.assertFalse(runtime.cached_runtime(moved/'代码',moved/'依赖工具',True))
            with mock.patch.object(runtime.time,'time',return_value=runtime.time.time()+8*86400):
                self.assertFalse(runtime.cached_runtime(code,tools,True))

    def test_cache_never_hides_dependency_changes_or_explicit_full_check(self):
        with tempfile.TemporaryDirectory() as name:
            code,tools=self.make_cached_fixture(Path(name))
            runtime.save_runtime_cache(code,tools,True)
            with mock.patch.dict(os.environ,{},clear=False),mock.patch.object(runtime,'ensure_packages') as packages,\
                    mock.patch.object(runtime,'ensure_node',return_value=tools/'node'),\
                    mock.patch.object(runtime,'ensure_ffmpeg',return_value=tools/'ffmpeg'),\
                    mock.patch.object(runtime,'ensure_electron',return_value=tools/'node_modules'),\
                    mock.patch.object(runtime.pot_provider,'provider_bundle_runtime_valid',return_value=True),\
                    mock.patch.object(runtime,'ensure_provider') as provider:
                runtime.prepare(code,tools,bundled=True)
                packages.assert_not_called();provider.assert_not_called()
                (tools/'python/runtime.bin').unlink()
                runtime.prepare(code,tools,bundled=True)
                self.assertEqual(packages.call_count,1)
                provider.assert_called_once_with(tools,strict=False)
                self.assertTrue(runtime.cached_runtime(code,tools,True))
                (tools/'python/runtime.bin').write_bytes(b'healthy')
                runtime.save_runtime_cache(code,tools,True)
                runtime.prepare(code,tools,bundled=True,full_check=True)
                self.assertEqual(packages.call_count,2);self.assertEqual(provider.call_count,2)
                self.assertEqual(provider.call_args,mock.call(tools,strict=True))

    def test_normal_cache_misses_never_hash_the_entire_provider(self):
        # Exercise the real prepare -> ensure_provider path, not only its flag.
        for scenario in ('first', 'corrupt', 'expired', 'different-machine', 'relocated', 'dependency-change'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as name:
                root=Path(name);code,tools=self.make_cached_fixture(root)
                if scenario!='first': runtime.save_runtime_cache(code,tools,True)
                if scenario=='corrupt': runtime.cache_path(tools,True).write_text('interrupted write')
                if scenario=='expired':
                    record=json.loads(runtime.cache_path(tools,True).read_text())
                    record['checkedAt']-=8*86400;runtime.cache_path(tools,True).write_text(json.dumps(record))
                if scenario=='relocated':
                    moved=root/'media dl (新位置)';shutil.copytree(code,moved/'代码');shutil.copytree(tools,moved/'依赖工具')
                    code,tools=moved/'代码',moved/'依赖工具'
                if scenario=='dependency-change': (tools/'python/runtime.bin').write_bytes(b'changed dependency')
                env={'COMPUTERNAME':'fixture-new-company-machine'} if scenario=='different-machine' else {}
                with mock.patch.dict(os.environ,env),mock.patch.object(runtime,'ensure_packages') as packages,\
                        mock.patch.object(runtime,'ensure_node',return_value=tools/'node'),\
                        mock.patch.object(runtime,'ensure_ffmpeg',return_value=tools/'ffmpeg'),\
                        mock.patch.object(runtime,'ensure_electron',return_value=tools/'node_modules'),\
                        mock.patch.object(runtime.pot_provider,'provider_bundle_runtime_valid',return_value=True),\
                        mock.patch.object(runtime.pot_provider,'provider_bundle_valid',side_effect=AssertionError('normal startup attempted full hashing')) as full:
                    runtime.prepare(code,tools,bundled=True)
                    self.assertEqual(packages.call_count,1);full.assert_not_called()
                    self.assertTrue(runtime.cached_runtime(code,tools,True))
                    runtime.prepare(code,tools,bundled=True)
                    self.assertEqual(packages.call_count,1)

    def test_explicit_full_check_hashes_once_even_with_a_valid_cache_and_announces_it(self):
        with tempfile.TemporaryDirectory() as name:
            code,tools=self.make_cached_fixture(Path(name));runtime.save_runtime_cache(code,tools,True)
            events=[]
            with mock.patch.object(runtime,'ensure_packages'),\
                    mock.patch.object(runtime,'ensure_node',return_value=tools/'node'),\
                    mock.patch.object(runtime,'ensure_ffmpeg',return_value=tools/'ffmpeg'),\
                    mock.patch.object(runtime,'ensure_electron',return_value=tools/'node_modules'),\
                    mock.patch.object(runtime.pot_provider,'provider_bundle_runtime_valid',return_value=True),\
                    mock.patch.object(runtime.pot_provider,'provider_bundle_valid',side_effect=lambda _path:events.append('full-hash') or True) as full,\
                    mock.patch.object(runtime,'notice',side_effect=events.append):
                runtime.prepare(code,tools,bundled=True,full_check=True)
            full.assert_called_once_with(tools/'bgutil-provider')
            self.assertLess(events.index('完整核对 YouTube 辅助组件（文件较多，请稍候）…'),events.index('full-hash'))
            self.assertLess(events.index('检查 Node / npm…'),events.index('检查 Electron…'))

    def test_a_failed_quick_provider_repair_still_needs_full_validation_before_replacing_files(self):
        with tempfile.TemporaryDirectory() as name:
            tools=Path(name);target=tools/'bgutil-provider';target.mkdir();(target/'keep').write_bytes(b'previous bundle')
            def incomplete(stage,*_args): stage.mkdir();(stage/'partial').write_bytes(b'incomplete dependency')
            with mock.patch.object(runtime.pot_provider,'provider_bundle_runtime_valid',return_value=False),\
                    mock.patch.object(runtime.pot_provider,'provider_bundle_valid',return_value=False) as full,\
                    mock.patch.object(runtime.provider_setup,'ensure_provider',side_effect=incomplete),\
                    self.assertRaisesRegex(RuntimeError,'校验失败'):
                runtime.ensure_provider(tools,strict=False)
            self.assertEqual((target/'keep').read_bytes(),b'previous bundle')
            self.assertTrue(any('.stage-' in call.args[0].name for call in full.call_args_list))
            self.assertEqual(list(tools.iterdir()),[target])

    def test_bad_or_unwritable_cache_does_not_block_healthy_startup(self):
        with tempfile.TemporaryDirectory() as name:
            code,tools=self.make_cached_fixture(Path(name))
            runtime.cache_path(tools,True).write_text('broken json')
            self.assertFalse(runtime.cached_runtime(code,tools,True))
            with mock.patch.object(Path,'write_text',side_effect=PermissionError('protected')),\
                    mock.patch.object(runtime,'notice') as notice:
                runtime.save_runtime_cache(code,tools,True)
            self.assertIn('无法保存启动缓存',notice.call_args.args[0])
            self.assertFalse(runtime.cached_runtime(code,tools,True))

    def test_layout_supports_copied_and_source_launchers_without_duplicate_code_folder(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name).resolve();(root/'启动程序').mkdir();(root/'启动程序/启动.py').touch()
            for script in (root/'启动程序/启动.py',root/'代码/distribution/launcher.py'):
                self.assertEqual(launcher.layout(script),(root,root/'代码',root/'依赖工具'))

    def test_wrong_python_association_prefers_bundle_before_powershell(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);code=root/'代码';tools=root/'依赖工具'
            with mock.patch.object(launcher,'layout',return_value=(root,code,tools)),\
                    mock.patch.object(Path,'is_file',return_value=True),\
                    mock.patch.object(launcher,'python_ready',return_value=True),\
                    mock.patch.object(launcher.sys,'argv',['启动.py','--check']),\
                    mock.patch.object(launcher.subprocess,'call',return_value=0) as execute:
                self.assertEqual(launcher.main(),0)
            command=execute.call_args.args[0]
            self.assertEqual(command[0],str(tools/'python/python.exe'))
            self.assertIn(str(code/'distribution/launcher.py'),command)
            self.assertNotIn('powershell.exe',' '.join(command).lower())

    @unittest.skipUnless(os.name=='nt','Windows cmd launcher')
    def test_cmd_and_double_click_entries_work_under_constrained_language_environment(self):
        import sys
        with tempfile.TemporaryDirectory(prefix='media dl 通用 (测试) ') as name:
            root=Path(name);code=root/'代码';scripts=code/'distribution';scripts.mkdir(parents=True)
            startup=root/'启动程序';startup.mkdir();tools=root/'依赖工具';python=tools/'python';python.mkdir(parents=True)
            make_windows_python_fixture(python, system_site_packages=False)
            source=Path(__file__).resolve().parent
            shutil.copy2(source/'distribution/start-runtime.cmd',scripts/'start-runtime.cmd')
            stub="import json,sys\nprint(json.dumps({'python':sys.executable,'args':sys.argv[1:]},ensure_ascii=False))\n"
            (scripts/'launcher.py').write_text(stub,encoding='utf-8')
            (scripts/'ensure-runtime.ps1').write_text("Write-Output 'unexpected-powershell';exit 91",encoding='utf-8-sig')
            command=startup/'启动media-dl.cmd';command.write_bytes(command_file().encode('ascii'))
            env=os.environ.copy();env['__PSLockdownPolicy']='4';env.pop('MEDIA_DL_RUNTIME_PREPARED',None)
            task=subprocess.run([os.environ.get('COMSPEC','cmd.exe'),'/d','/c',str(command),'--check'],
                cwd=root,env=env,capture_output=True,timeout=30)
            self.assertEqual(task.returncode,0,task.stderr+task.stdout)
            text=task.stdout.decode('utf-8',errors='replace')
            self.assertNotIn('unexpected-powershell',text)
            self.assertIn('python.exe',text);self.assertIn('--check',text)

    @unittest.skipUnless(os.name=='nt','Windows PowerShell policy compatibility')
    def test_both_python_associations_relay_to_bundle_before_legacy_imports(self):
        import sys
        with tempfile.TemporaryDirectory(prefix='media dl 双击 ') as name:
            root=Path(name);code=root/'代码';scripts=code/'distribution';scripts.mkdir(parents=True)
            startup=root/'启动程序';startup.mkdir();python=root/'依赖工具/python';python.mkdir(parents=True)
            make_windows_python_fixture(python, system_site_packages=True)
            source=Path(__file__).resolve().parent
            for file in (scripts/'launcher.py',startup/'启动.py'):
                shutil.copy2(source/'distribution/launcher.py',file)
            (scripts/'ensure-runtime.ps1').write_text("Write-Output 'unexpected-powershell';exit 91",encoding='utf-8-sig')
            (scripts/'runtime_setup.py').write_text("import os\ndef prepare(code,tools,**kwargs):\n return tools,tools/'node_modules',tools/'node',os.environ.copy()\n")
            prelude=(source/'yt_dlp_gui.py').read_text(encoding='utf-8').split('import tkinter as tk')[0]
            (code/'yt_dlp_gui.py').write_text(prelude+"\nprint('legacy-project-runtime='+sys.executable)\n",encoding='utf-8')
            env={**os.environ,'__PSLockdownPolicy':'4'};env.pop('MEDIA_DL_RUNTIME_PREPARED',None)
            for script,args in ((startup/'启动.py',['--legacy']),(code/'yt_dlp_gui.py',[])):
                task=subprocess.run([sys.executable,'-B','-X','utf8',str(script),*args],env=env,capture_output=True,timeout=30)
                self.assertEqual(task.returncode,0,task.stdout+task.stderr)
                text=task.stdout.decode('utf-8')
                self.assertIn('legacy-project-runtime=',text)
                self.assertIn(str(python.resolve()),text)
                self.assertNotIn('unexpected-powershell',text)

    @unittest.skipUnless(os.name=='nt','Windows PowerShell policy compatibility')
    def test_constrained_recovery_fails_with_actionable_message_without_policy_changes(self):
        powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        env={**os.environ,'__PSLockdownPolicy':'4'}
        script=Path(__file__).resolve().parent/'distribution/ensure-runtime.ps1'
        command="[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); $script=Get-Content -Raw -Encoding UTF8 -LiteralPath '"+str(script).replace("'","''")+"'; $ExecutionContext.SessionState.LanguageMode='ConstrainedLanguage'; Invoke-Expression $script"
        task=subprocess.run([str(powershell),'-NoProfile','-ExecutionPolicy','Bypass','-Command',command],
            env=env,capture_output=True,timeout=20)
        self.assertNotEqual(task.returncode,0)
        self.assertIn('安全策略限制',task.stdout.decode('utf-8',errors='replace'))


if __name__=='__main__':unittest.main()
