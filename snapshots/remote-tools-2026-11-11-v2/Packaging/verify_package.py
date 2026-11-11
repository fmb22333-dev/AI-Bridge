import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
import zipfile
from pathlib import Path


def verify(archive):
    archive=Path(archive).resolve()
    base=Path(tempfile.mkdtemp(prefix='bridge-package-'))/'安装验证 with spaces'
    base.mkdir()
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in z.namelist())
        z.extractall(base)
    package=base/'AI_Bridge';runtime=package/'Runtime/Current'
    manifest=json.loads((package/'FILE_MANIFEST.json').read_text(encoding='utf-8'))
    for relative,item in manifest['files'].items():
        file=package/relative
        assert file.stat().st_size==item['bytes'],relative
        assert hashlib.sha256(file.read_bytes()).hexdigest()==item['sha256'],relative
    report={'archive':archive.name,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
            'manifest_files_verified':len(manifest['files']),'ue_plugin_version':manifest['ue_plugin_version'],
            'extraction_path_test':'Unicode and spaces','checks':{}}
    for path in package.rglob('*.py'):
        compile(path.read_text(encoding='utf-8-sig'),str(path),'exec')
    report['checks']['python_compile']=True
    # Generate only a fixture placeholder, never install into this machine's Codex.
    python=package/'_System/.venv/Scripts/python.exe';python.parent.mkdir(parents=True);python.touch()
    spec=importlib.util.spec_from_file_location('portable_registration',package/'Tools/register_codex.py')
    registration=importlib.util.module_from_spec(spec);spec.loader.exec_module(registration)
    config=base/'test_profile/config.toml';config.parent.mkdir()
    original='model = "fixture-model"\n[features]\nguardian_approval = false\n[mcp_servers.existing]\ncommand = "unchanged"\n'
    config.write_text(original,encoding='utf-8')
    dry=registration.registration(package,config,dry_run=True)
    assert dry['dry_run'] and config.read_text()==original
    changed=registration.registration(package,config)
    assert Path(changed['backup']).read_text()==original
    parsed=tomllib.loads(config.read_text())
    assert parsed['model']=='fixture-model'
    assert parsed['mcp_servers']['existing']['command']=='unchanged'
    assert parsed['mcp_servers']['ta_bridge']['command']==str(python).replace('\\','/')
    assert not registration.registration(package,config)['changed']
    prior=config.read_text();config.write_text(prior.replace(json.dumps(str(python).replace('\\','/')),json.dumps('D:/Other/python.exe')))
    conflict=config.read_text()
    try:registration.registration(package,config)
    except RuntimeError:pass
    else:raise AssertionError('Conflicting registration was not rejected')
    assert config.read_text()==conflict
    report['checks']['registration_preserves_config_backup_idempotency_and_conflicts']=True
    env={**os.environ,'PYTHONPATH':str(runtime/'src'),'PYTHONIOENCODING':'utf-8'}
    test=subprocess.run([sys.executable,'-m','pytest','-q'],cwd=runtime,env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
    report['packaged_tests']=test.stdout.strip()
    assert test.returncode==0,test.stdout+'\n'+test.stderr
    state=base/'isolated_state';state.mkdir()
    workspace=base/'fixture_workspace';workspace.mkdir()
    (state/'workspaces.json').write_text(json.dumps([{'workspace_id':'MCPFixture','root':str(workspace)}]),encoding='utf-8')
    env.update(AI_BRIDGE_DATA_DIR=str(state),AI_BRIDGE_SUPERVISED='1',AI_BRIDGE_ROOT=str(package),
        AI_BRIDGE_RUNTIME_DIR=str(runtime),AI_BRIDGE_AUTO_OPEN_UI='0',AI_BRIDGE_PORT='18980',
        AI_BRIDGE_HOUDINI_USER_DIR=str(base/'houdini_fixture'))
    process=subprocess.Popen([sys.executable,str(runtime/'launch_bridge.py')],cwd=package,env=env,
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    helper=None
    try:
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError(process.stderr.read().decode('utf-8',errors='replace'))
            if (state/'connection.json').is_file():
                connection=json.loads((state/'connection.json').read_text())
                try:
                    req=urllib.request.Request(connection['url']+'/health',headers={'Authorization':'Bearer '+connection['token']})
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=1) as response:
                        health=json.load(response)
                    break
                except Exception:pass
            time.sleep(.2)
        else:raise TimeoutError('Isolated packaged Runtime startup timed out')
        assert health['status']=='ok'
        assert not (state/'remote.json').exists() and not (state/'fallback_transport.json').exists()
        helper=subprocess.Popen([sys.executable,str(package/'Integrations/CodexBridge/bridge_mcp.py')],env=env,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        def call(i,method,params,allow_error=False):
            helper.stdin.write(json.dumps({'jsonrpc':'2.0','id':i,'method':method,'params':params})+'\n');helper.stdin.flush()
            result=json.loads(helper.stdout.readline())['result'];assert allow_error or not result.get('isError'),result
            return result
        call(1,'initialize',{'protocolVersion':'2025-06-18'})
        tools=call(2,'tools/list',{})['tools'];assert len(tools)==5
        context=json.loads(call(3,'tools/call',{'name':'ta_bridge_context','arguments':{}})['content'][0]['text'])
        assert not context['sessions'] and context['workspaces']==['MCPFixture']
        assert context['endpoints']['progress'].startswith('GET /client/jobs/')
        report['global_tools']=[t['name'] for t in tools]
        report['checks']['isolated_runtime_and_actual_mcp_context']=True
        report['checks']['no_remote_credentials_and_only_explicit_fixture_workspace']=True
        verify_workspace_mcp(call,workspace,report)
    finally:
        if helper:
            helper.stdin.close();helper.wait(timeout=5)
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    destination=archive.parent/'PACKAGE_VERIFICATION.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


def verify_workspace_mcp(call,root,report):
    sequence=10
    def tool(name,arguments,error=False):
        nonlocal sequence
        sequence+=1
        response=call(sequence,'tools/call',{'name':name,'arguments':arguments},allow_error=error)
        if error:assert response['isError'],response
        return json.loads(response['content'][0]['text'])
    def execute(operation,arguments):
        cid='workspace-mcp-'+str(sequence+1)
        accepted=tool('ta_bridge_submit',{'command_id':cid,'workspace':'MCPFixture','adapter':'workspace','operation':operation,'arguments':arguments})
        assert accepted['command_id']==cid,accepted
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            status=tool('ta_bridge_status',{'command_id':cid})
            if status['terminal']:
                receipt=tool('ta_bridge_result',{'command_id':cid})['result']
                return receipt
            time.sleep(.03)
        raise TimeoutError(cid)
    def success(op,args):
        result=execute(op,args)
        assert result['status']=='success',result
        return result['result']
    schema=tool('ta_bridge_context',{'operation':'workspace.patchset.apply'})['workspace_access']
    assert 'workspace.file.read' in schema['operations']
    assert schema['arguments_schema']['properties']['changes']['maxItems']==64
    assert 'oneOf' in schema['arguments_schema']['properties']['changes']['items']
    configuration=tool('ta_bridge_context',{'operation':'workspace.project.configure'})['workspace_access']['arguments_schema']
    assert configuration['properties']['manifest']['properties']['commands']['additionalProperties']['required']==['argv']
    report['checks']['workspace_schema_discovery']=True
    assert 'WORKSPACE_OPERATION_NOT_EXPOSED' in tool('ta_bridge_submit',{'command_id':'admin-denied','workspace':'MCPFixture','adapter':'workspace','operation':'workspace.service.stop'},True)['detail']
    success('workspace.file.create',{'path':'a.txt','text':'alpha old'})
    success('workspace.file.create',{'path':'delete.txt','text':'remove me'})
    # Cross the former 1 MB MCP request ceiling using normal UTF-8 source content.
    large='测'*400000
    success('workspace.file.create',{'path':'large.txt','text':large})
    assert success('workspace.file.read',{'path':'large.txt','max_chars':500000})['text']==large
    items=success('workspace.files.read',{'paths':['a.txt','delete.txt','missing.txt']})['items']
    assert items[2]['error']['code']=='WORKSPACE_FILE_NOT_FOUND'
    changes=[{'op':'patch','path':'a.txt','expected_hash':items[0]['sha256'],'old_text':'old','new_text':'new'},
             {'op':'create','path':'nested/b.txt','text':'before','create_parents':True},
             {'op':'delete','path':'delete.txt','expected_hash':items[1]['sha256']}]
    batch=success('workspace.patchset.apply',{'changes':changes})
    assert len(batch['changed_files'])==3
    assert (root/'a.txt').read_text()=='alpha new' and not (root/'delete.txt').exists()
    read=success('workspace.file.read',{'path':'nested/b.txt'})
    success('workspace.file.patch',{'path':'nested/b.txt','expected_hash':read['sha256'],'old_text':'before','new_text':'after'})
    result=execute('workspace.patchset.apply',{'changes':[
        {'op':'create','path':'must-not-exist.txt','text':'no'},
        {'op':'replace','path':'a.txt','expected_hash':items[0]['sha256'],'text':'stale'}]})
    assert result['status']=='conflict' and result['failure']['code']=='EXPECTED_HASH_MISMATCH',result
    assert not (root/'must-not-exist.txt').exists() and (root/'a.txt').read_text()=='alpha new'
    for args,code in [({'changes':[{'op':'create','path':'../outside.txt','text':'no'}]},'WORKSPACE_PATH_ESCAPE'),
                      ({'changes':[{'op':'create','path':'.ai-bridge/project.json','text':'{}'}]},'WORKSPACE_CONTROL_FILE_PROTECTED'),
                      ({'changes':[{'op':'replace','path':'a.txt','text':'no'}]},'EXPECTED_HASH_REQUIRED'),
                      ({'changes':[{'op':'create','path':f'x{i}.txt','text':''} for i in range(65)]},'PATCHSET_TOO_MANY_CHANGES')]:
        result=execute('workspace.patchset.apply',args)
        assert result['failure']['code']==code,result
    assert not (root.parent/'outside.txt').exists()
    manifest={'schema_version':'1.0','project':'MCP fixture','commands':{'original':{'argv':[sys.executable,'-S','-c',"print('registered-command-ok')"],'timeout_seconds':10}},'services':{}}
    success('workspace.project.configure',{'manifest':manifest})
    original=success('workspace.file.read',{'path':'.ai-bridge/project.json'})
    manifest=json.loads(original['text'])
    manifest['commands']['added']={'argv':[sys.executable,'-S','-c',"print('added-command-ok')"],'timeout_seconds':10}
    success('workspace.project.configure',{'manifest':manifest,'expected_hash':original['sha256']})
    configured=success('workspace.file.read',{'path':'.ai-bridge/project.json'})
    assert set(json.loads(configured['text'])['commands'])=={'original','added'}
    for args,code in [({'manifest':manifest},'EXPECTED_HASH_REQUIRED'),
                      ({'manifest':manifest,'expected_hash':original['sha256']},'EXPECTED_HASH_MISMATCH')]:
        rejected=execute('workspace.project.configure',args)
        assert rejected['failure']['code']==code,rejected
    assert success('workspace.file.read',{'path':'.ai-bridge/project.json'})['sha256']==configured['sha256']
    output=success('workspace.command.run',{'command':'added'})
    assert 'added-command-ok' in output['stdout'],output
    listing=success('workspace.directory.list',{'path':'.'})
    assert any(item['name']=='a.txt' for item in listing['entries'])
    report['checks']['actual_mcp_workspace_read_create_patch_batch_and_conflict']=True
    report['checks']['actual_mcp_workspace_bounds_control_hash_and_count_limits']=True
    report['checks']['actual_mcp_large_unicode_request_and_response']=True
    report['checks']['actual_mcp_register_update_preserve_and_execute_command']=True
    report['checks']['actual_mcp_manifest_missing_and_stale_hash_rejected']=True


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('archive');args=parser.parse_args()
    verify(args.archive)
