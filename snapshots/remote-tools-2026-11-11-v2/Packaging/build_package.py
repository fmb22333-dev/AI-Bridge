"""Build a clean, relocatable snapshot from a selected Runtime, never from user state."""
import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

SKIP={'__pycache__','.pytest_cache','.git','.svn','.venv','Binaries','Intermediate','Saved','backups'}


def copy_tree(source,target):
    shutil.copytree(source,target,ignore=lambda directory,names:[name for name in names
        if name in SKIP or name.endswith(('.pyc','.pyo','.bak','.dpapi')) or name.startswith('.ai_bridge_')])


def build(runtime, bridge_root, plugin, output):
    runtime=Path(runtime).resolve();bridge_root=Path(bridge_root).resolve();output=Path(output).resolve()
    if output.exists(): raise RuntimeError('Output already exists; choose a fresh destination')
    output.mkdir(parents=True)
    package=output/'AI_Bridge';package.mkdir()
    current=package/'Runtime/Current';current.mkdir(parents=True)
    here=Path(__file__).resolve().parent
    for name in ('src','houdini_adapter','blender_adapter'):
        copy_tree(runtime/name,current/name)
    unreal=current/'unreal_adapter';unreal.mkdir()
    shutil.copy2(runtime/'unreal_adapter/install_plugin.py',unreal/'install_plugin.py')
    target_plugin=unreal/'AIBridgeUE';target_plugin.mkdir()
    for name in ('Source','Config','Resources'):
        if (Path(plugin)/name).is_dir():copy_tree(Path(plugin)/name,target_plugin/name)
    shutil.copy2(Path(plugin)/'AIBridgeUE.uplugin',target_plugin/'AIBridgeUE.uplugin')
    for name in ('pyproject.toml','launch_bridge.py','install_houdini_adapter.py'):
        shutil.copy2(runtime/name,current/name)
    # Use the project's existing distribution knowledge filter, rather than
    # shipping personal recipe history and project-only knowledge.
    sys.path.insert(0,str(runtime/'src'))
    from ai_bridge.deployment.knowledge_pack import build_distribution_knowledge,validate_distribution
    knowledge=current/'houdini_adapter/python/ai_bridge_houdini/knowledge'
    source_knowledge=runtime/'houdini_adapter/python/ai_bridge_houdini/knowledge'
    manifest=build_distribution_knowledge(knowledge,source_root=source_knowledge)
    clean_manifest={k:v for k,v in manifest.items() if k not in ('source_root','source_gate')}
    (knowledge/'distribution_manifest.json').write_text(json.dumps(clean_manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    assert validate_distribution(knowledge)['ok']
    # Keep the packaged self-development tests useful, without shipping personal
    # fixture paths/history copied into unrelated tests.
    tests=current/'tests';tests.mkdir()
    for name in ('test_remote_controls.py','test_supabase_fallback_transport.py','test_supabase_primary_multi_ai.py','test_local_direct_client.py','test_transport_activity_heartbeat.py','test_transport_predecessor_p1.py','test_remote_presence_transport_state_semantics.py','test_work_hub_r4.py','test_workspace_batch_ops.py','test_workspace_worker_files.py','test_workspace_fast_safety_r6.py','test_workspace_project_configure.py'):
        shutil.copy2(runtime/'tests'/name,tests/name)
    system=package/'_System';system.mkdir()
    shutil.copy2(bridge_root/'_System/bootstrap.bat',system/'bootstrap.bat')
    supervisor=(bridge_root/'_System/supervisor.py').read_text(encoding='utf-8')
    old='if config and bool(config.get("auto_update", True)) and now >= next_update_check:'
    new='if os.environ.get("AI_BRIDGE_ALLOW_AUTOMATIC_UPDATES") == "1" and config and bool(config.get("auto_update", True)) and now >= next_update_check:'
    if old not in supervisor:raise RuntimeError('Supervisor update gate changed; inspect before packaging')
    (system/'supervisor.py').write_text(supervisor.replace(old,new,1),encoding='utf-8')
    routes=current/'src/ai_bridge/web/routes.py'
    routes.write_text(routes.read_text(encoding='utf-8').replace('fmb22333-dev/ai-bridge-bus','YOUR_OWNER/YOUR_RUNTIME_REPOSITORY'),encoding='utf-8')
    for name in ('AI_Bridge.bat','START_AI_BRIDGE.bat','OPEN_AI_BRIDGE.bat','STOP_AI_BRIDGE.bat'):
        shutil.copy2(bridge_root/name,package/name)
    for name in ('INSTALL_AI_BRIDGE.bat','README.md','AI_INSTALL_GUIDE.md'):
        shutil.copy2(here/name,package/name)
    tools=package/'Tools';tools.mkdir()
    shutil.copy2(here/'register_codex.py',tools/'register_codex.py')
    integration=package/'Integrations/CodexBridge';integration.mkdir(parents=True)
    shutil.copy2(bridge_root/'Integrations/CodexBridge/bridge_mcp.py',integration/'bridge_mcp.py')
    shutil.copy2(bridge_root/'Integrations/CodexBridge/WORKSPACE.md',package/'WORKSPACE.md')
    remote=(bridge_root/'Integrations/CodexBridge/REMOTE.md').read_text(encoding='utf-8')
    remote=remote.split('Live verification evidence:')[0].rstrip()+'\n'
    (package/'REMOTE.md').write_text(remote,encoding='utf-8')
    forbidden={'connection.json','auth.json','remote.json','fallback_transport.json','workspaces.json','bridge.db','active_runtime.json'}
    files={};findings=[]
    pattern=re.compile(rb'(?:github_pat_|ghp_|sb_secret_)[A-Za-z0-9_\-]{24,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----')
    for file in sorted(package.rglob('*')):
        if not file.is_file():continue
        rel=file.relative_to(package).as_posix()
        if file.name in forbidden or file.suffix in {'.db','.dpapi','.dll','.pdb','.exe','.uasset','.umap','.hip','.blend'}:
            findings.append(rel)
        raw=file.read_bytes()
        # Older embedded setup/docs have a private-source fallback too.
        # Keep the distribution independent of the sender's repository.
        if b'fmb22333-dev/ai-bridge-bus' in raw:
            raw=raw.replace(b'fmb22333-dev/ai-bridge-bus',b'YOUR_OWNER/YOUR_RUNTIME_REPOSITORY')
            file.write_bytes(raw)
        if pattern.search(raw):findings.append(rel+': credential-like literal')
        files[rel]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    if findings:raise RuntimeError('Package audit failed: '+str(findings))
    version=json.loads((target_plugin/'AIBridgeUE.uplugin').read_text(encoding='utf-8'))['VersionName']
    report={'package':'AI_Bridge_RemoteTools_2026-11-11_v2','runtime_base':'0.2.6.96','remote_controls_revision':'2026-11-11.2','global_mcp_version':'1.1.0',
            'ue_plugin_version':version,'platform':'Windows','automatic_runtime_updates':'opt-in',
            'personal_state_included':False,'credential_pattern_scan':'passed','knowledge_validation':'passed','files':files}
    (package/'FILE_MANIFEST.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=output/'AI_Bridge_RemoteTools_2026-11-11_v2.zip'
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for file in sorted(package.rglob('*')):
            if file.is_file():
                info=zipfile.ZipInfo('AI_Bridge/'+file.relative_to(package).as_posix(),date_time=(2026,11,11,0,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED
                info.external_attr=0o100644 << 16
                z.writestr(info,file.read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/(archive.name+'.sha256')).write_text(digest+'  '+archive.name+'\n',encoding='ascii')
    return {'archive':str(archive),'bytes':archive.stat().st_size,'sha256':digest,'file_count':len(files)+1,'ue_plugin_version':version}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('runtime','bridge-root','plugin','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.runtime,args.bridge_root,args.plugin,args.output),ensure_ascii=False,indent=2))
