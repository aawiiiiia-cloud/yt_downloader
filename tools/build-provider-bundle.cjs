'use strict';
// Build-time only. All upstream sources/resources remain available beside the
// bundle; runtime never downloads a bundler or compiles dependencies.
const fs = require('node:fs'), path = require('node:path');
const crypto = require('node:crypto');
const ESBUILD_VERSION = '0.28.2';
async function buildBundle(provider, esbuildModule) {
  provider = path.resolve(provider);
  const esbuild = require(path.resolve(esbuildModule));
  if (esbuild.version !== ESBUILD_VERSION) throw new Error('Expected esbuild ' + ESBUILD_VERSION);
  const server = path.join(provider, 'server'), build = path.join(server, 'build');
  const entry = path.join(build, 'main.js'), output = path.join(build, 'media-dl-provider.mjs');
  const packageInfo = JSON.parse(fs.readFileSync(path.join(server, 'package.json'), 'utf8'));
  if (packageInfo.version !== '1.3.2') throw new Error('Unsupported provider version');
  const rootManifest = JSON.parse(fs.readFileSync(path.join(provider, '.provider-manifest.json'), 'utf8'));
  const banner = `import {createRequire as __mediaDlCreateRequire} from 'node:module';
import {fileURLToPath as __mediaDlFileURLToPath} from 'node:url';
const __mediaDlServerURL=new URL('../',import.meta.url);
const __mediaDlAssetURL=relative=>new URL(relative,__mediaDlServerURL).href;
const __mediaDlAssetPath=relative=>__mediaDlFileURLToPath(new URL(relative,__mediaDlServerURL));
const require=__mediaDlCreateRequire(new URL('build/main.js',__mediaDlServerURL));`;
  const result = await esbuild.build({entryPoints:[entry],outfile:output,bundle:true,platform:'node',target:'node22',
    format:'esm',external:['canvas'],metafile:true,legalComments:'linked',logLevel:'warning',banner:{js:banner},
    plugins:[{name:'portable-node-resources',setup(builder){builder.onLoad({filter:/\.[cm]?js$/},async args=>{
      const contents=fs.readFileSync(args.path,'utf8');
      const relative=path.relative(server,args.path).split(path.sep).join('/');
      if(relative.startsWith('../')||path.isAbsolute(relative))throw new Error('Dependency outside provider');
      const define={},declarations=[];
      if(contents.includes('import.meta.url')||contents.includes('require.resolve')){
        declarations.push(`const __mediaDlModuleURL=__mediaDlAssetURL(${JSON.stringify(relative)});`);
        define['import.meta.url']='__mediaDlModuleURL';
      }
      if(contents.includes('__dirname')){
        declarations.push(`const __mediaDlModuleDir=__mediaDlAssetPath(${JSON.stringify(path.posix.dirname(relative)+'/')});`);
        define.__dirname='__mediaDlModuleDir';
      }
      if(contents.includes('__filename')){
        declarations.push(`const __mediaDlModuleFile=__mediaDlAssetPath(${JSON.stringify(relative)});`);
        define.__filename='__mediaDlModuleFile';
      }
      if(contents.includes('require.resolve')){
        declarations.push('const __mediaDlResolve=__mediaDlCreateRequire(__mediaDlModuleURL).resolve;');
        define['require.resolve']='__mediaDlResolve';
      }
      if(!declarations.length)return;
      const transformed=await esbuild.transform(contents,{loader:'js',target:'node22',define});
      return{contents:declarations.join('\n')+'\n'+transformed.code,loader:'js',resolveDir:path.dirname(args.path)};
    });}}]});
  const digest=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const artifacts=['server/build/media-dl-provider.mjs'];
  if(fs.existsSync(output+'.LEGAL.txt'))artifacts.push('server/build/media-dl-provider.mjs.LEGAL.txt');
  const record={format:1,providerVersion:packageInfo.version,providerCommit:rootManifest.upstream_commit,
    esbuildVersion:esbuild.version,inputFiles:Object.keys(result.metafile.inputs).length,
    inputs:Object.fromEntries(Object.keys(result.metafile.inputs).map(name=>{
      const file=path.resolve(name),relative=path.relative(provider,file).split(path.sep).join('/');
      if(relative.startsWith('../')||path.isAbsolute(relative))throw new Error('Input outside provider');
      return[relative,digest(file)];
    })),
    artifacts:Object.fromEntries(artifacts.map(name=>[name,digest(path.join(provider,name))]))};
  fs.writeFileSync(path.join(build,'media-dl-bundle.json'),JSON.stringify(record,null,2)+'\n');
  const source=path.join(provider,'corresponding-source','collector-bundle');fs.mkdirSync(source,{recursive:true});
  fs.copyFileSync(__filename,path.join(source,'build-provider-bundle.cjs'));
  fs.writeFileSync(path.join(source,'README.md'),'# Collector runtime bundle\n\n'+
    'Built with esbuild '+ESBUILD_VERSION+'. Original JavaScript, native canvas, source and resource files are retained.\n'+
    'Rebuild: node build-provider-bundle.cjs PROVIDER_ROOT ESBUILD_MODULE_PATH; then regenerate the provider SHA-256 manifest.\n'+
    'Portable resource paths are transformed by the esbuild JavaScript parser. No upstream source is removed.\n');
  const notice=path.join(provider,'SOURCE_AND_MODIFICATIONS.md');
  const marker='\n## Collector startup optimization\n';
  fs.writeFileSync(notice,fs.readFileSync(notice,'utf8').split(marker)[0]+marker+'\n'+
    'An additional portable runtime entry is built with esbuild '+ESBUILD_VERSION+'. Original modules, native binaries and resources remain unchanged.\n'+
    'The complete build script and reconstruction instructions are in corresponding-source/collector-bundle/.\n'+
    'media-dl-bundle.json records input and artifact SHA-256 digests. Regenerate .provider-manifest.json after rebuilding.\n');
  return record;
}
module.exports={buildBundle};
if(require.main===module)buildBundle(process.argv[2],process.argv[3]).then(row=>console.log(JSON.stringify(row)))
  .catch(error=>{console.error(error.message);process.exitCode=1;});
