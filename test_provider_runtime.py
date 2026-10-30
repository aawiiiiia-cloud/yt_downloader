from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import pot_provider as pot


def fixture(root: Path, bundled=True):
    content={'.provider-version':pot.PROVIDER_VERSION,'LICENSE':'test license','SOURCE_AND_MODIFICATIONS.md':'test source',
        'server/package.json':'{}','server/package-lock.json':'{}','server/build/main.js':'original',
        'server/node_modules/test/index.js':'dependency','corresponding-source/test.ts':'source'}
    for name,value in content.items():
        file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(value,encoding='utf-8')
    if bundled:
        artifacts={}
        for name in ('server/build/media-dl-provider.mjs','server/build/media-dl-provider.mjs.LEGAL.txt'):
            file=root/name;file.write_text('bundled test resource',encoding='utf-8')
            artifacts[name]=hashlib.sha256(file.read_bytes()).hexdigest()
        (root/'server/build/media-dl-bundle.json').write_text(json.dumps({'format':1,'providerVersion':pot.PROVIDER_VERSION,
            'providerCommit':pot.PROVIDER_COMMIT,'artifacts':artifacts,
            'inputs':{'server/build/main.js':hashlib.sha256(b'original').hexdigest(),
                      'server/node_modules/test/index.js':hashlib.sha256(b'dependency').hexdigest()}}),encoding='utf-8')
    pot.write_provider_manifest(root)


class ProviderRuntimeTests(unittest.TestCase):
    def test_original_provider_still_works_without_an_optimized_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture(root,False)
            self.assertEqual(pot.provider_runtime_entry(root),root/'server/build/main.js')

    def test_verified_bundle_survives_a_directory_move(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);root=base/'before';root.mkdir();fixture(root)
            moved=base/'新路径 (with spaces)';root.rename(moved)
            self.assertEqual(pot.provider_runtime_entry(moved),moved/'server/build/media-dl-provider.mjs')

    def test_tampered_bundle_or_license_falls_back_without_rewriting_original(self):
        for name in ('media-dl-provider.mjs','media-dl-provider.mjs.LEGAL.txt','media-dl-bundle.json'):
            with self.subTest(file=name),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);fixture(root);(root/'server/build'/name).write_bytes(b'tampered')
                logs=[];self.assertEqual(pot.provider_runtime_entry(root,logs.append),root/'server/build/main.js')
                self.assertEqual((root/'server/build/main.js').read_text(),'original');self.assertTrue(logs)

    def test_invalid_metadata_and_outside_artifact_paths_are_rejected(self):
        for metadata in ([],{'format':9},{'format':1,'providerVersion':pot.PROVIDER_VERSION,
                'providerCommit':pot.PROVIDER_COMMIT,'artifacts':{'../../outside.mjs':'0'*64}}):
            with self.subTest(metadata=metadata),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);fixture(root)
                (root/'server/build/media-dl-bundle.json').write_text(json.dumps(metadata),encoding='utf-8')
                pot.write_provider_manifest(root)
                self.assertEqual(pot.provider_runtime_entry(root,lambda _:None),root/'server/build/main.js')

    def test_updated_dependency_manifest_cannot_reuse_a_stale_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture(root)
            (root/'server/node_modules/test/index.js').write_text('updated dependency',encoding='utf-8')
            pot.write_provider_manifest(root)
            self.assertEqual(pot.provider_runtime_entry(root,lambda _:None),root/'server/build/main.js')

    def test_runtime_timing_never_forwards_request_or_token_fields(self):
        row=pot._runtime_timing('MEDIA_DL_PROVIDER_TIMING '+json.dumps({'stage':'token','seconds':1.25,'status':200,
            'cookie':'fixture-secret','poToken':'fixture-token','url':'fixture-private'}))
        self.assertEqual(row,{'stage':'token','seconds':1.25,'status':200})
        for value in ({'stage':'token','seconds':-1,'status':200},{'stage':'token','seconds':float('nan'),'status':200},
                {'stage':'unknown','seconds':1},{'stage':'token','seconds':1,'status':'200'},None):
            self.assertIsNone(pot._runtime_timing('MEDIA_DL_PROVIDER_TIMING '+json.dumps(value)))

    def test_pipe_metrics_are_delivered_before_the_service_exits(self):
        read_fd,write_fd=os.pipe();ready=threading.Event();rows=[]
        writer=os.fdopen(write_fd,'wb')
        capture=pot._BoundedOutput(on_line=lambda line:(rows.append(line),ready.set()))
        capture.start(os.fdopen(read_fd,'rb'))
        try:
            writer.write(b'MEDIA_DL_PROVIDER_');writer.flush()
            self.assertFalse(ready.wait(.03))
            writer.write(b'TIMING {"stage":"token","seconds":1,"status":200}\n');writer.flush()
            self.assertTrue(ready.wait(2));self.assertEqual(len(rows),1)
        finally:writer.close();capture.finish()

    def test_a_failed_bundle_falls_back_with_a_full_first_original_start_budget(self):
        class Process:
            stdout=None
            def __init__(self,broken):self.returncode=1 if broken else None
            def poll(self):return self.returncode
            def terminate(self):self.returncode=0
            def wait(self,timeout):return self.returncode
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture(root);clock=[0.0];calls=[];starts=[]
            def spawn(command,**kwargs):
                calls.append(command);starts.append(clock[0]);return Process(len(calls)==1)
            def ping(_url):
                return {'version':pot.PROVIDER_VERSION} if len(calls)==2 and clock[0]-starts[1]>=42 else None
            timings=[];manager=pot.PotProviderManager(root,timing=timings.append)
            with mock.patch.object(pot.shutil,'which',return_value='node.exe'),\
                    mock.patch.object(pot.subprocess,'Popen',side_effect=spawn),\
                    mock.patch.object(manager,'_ping',side_effect=ping),\
                    mock.patch.object(pot.time,'monotonic',side_effect=lambda:clock[0]),\
                    mock.patch.object(pot.time,'sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)):
                self.assertTrue(manager.ensure_started(lambda _:None,timeout=80));manager.stop()
            self.assertEqual(len(calls),2)
            self.assertIn(str(root.resolve()/'server/build/media-dl-provider.mjs'),calls[0])
            self.assertIn(str(root.resolve()/'server/build/main.js'),calls[1])
            self.assertTrue(any(row.get('stage')=='service-ready' and not row['optimized'] for row in timings))


if __name__=='__main__':unittest.main()
