import json
import httpx

import ai_bridge.transport.github_bus as gb
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus


def _command():
    return {
        'protocol':'bridge/1','command_id':'cmd-v4','workspace':'Bridge','adapter':'houdini',
        'operation':'inspect.context','arguments':{},
        'execution':{'verify':True,'checkpoint':'none','dry_run':False},'risk':'L1',
    }


def test_mailbox_v4_constants_exist():
    assert getattr(gb,'COMMAND_MARKER_V4',None)=='AI_BRIDGE_COMMAND_V4'
    assert getattr(gb,'ACK_MARKER_V4',None)=='AI_BRIDGE_ACK_V4'
    assert getattr(gb,'RESULT_MARKER_V4',None)=='AI_BRIDGE_RESULT_V4'


def test_mailbox_v4_acks_generation_before_returning_command():
    command=_command()
    generation='gen-001'
    body='AI_BRIDGE_COMMAND_V4\n'+json.dumps({'bridge_id':'bridge-test','generation':generation,'command':command})
    calls=[]
    def handler(request:httpx.Request)->httpx.Response:
        calls.append((request.method,request.url.path,request.content.decode('utf-8') if request.content else ''))
        headers={'X-RateLimit-Limit':'5000','X-RateLimit-Remaining':'4900','X-RateLimit-Reset':'2000000000'}
        if request.method=='GET' and request.url.path.endswith('/issues/comments'):
            return httpx.Response(200,headers=headers,json=[{'id':321,'issue_url':'https://api.github.com/repos/owner/repo/issues/1','body':'AI_BRIDGE_MAILBOX_V3\n'+json.dumps({'bridge_id':'bridge-test','state':'idle'})}])
        if request.method=='GET' and request.url.path.endswith('/issues/comments/321'):
            return httpx.Response(200,headers=headers,json={'id':321,'body':body})
        if request.method=='PATCH' and request.url.path.endswith('/issues/comments/321'):
            patched=json.loads(request.content.decode('utf-8'))['body']
            assert patched.startswith('AI_BRIDGE_ACK_V4\n')
            payload=json.loads(patched.split('\n',1)[1])
            assert payload['generation']==generation
            assert payload['command_id']=='cmd-v4'
            return httpx.Response(200,headers=headers,json={'id':321,'body':patched})
        raise AssertionError(f'unexpected {request.method} {request.url}')
    transport=gb.GitHubBusTransport(gb.GitHubBusConfig(repository='owner/repo',token='x',bridge_id='bridge-test'),client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert transport.initialize_message_mode()=='issue_channel_v5'
    commands=transport.fetch_commands()
    assert [c.command_id for c in commands]==['cmd-v4']
    assert any(method=='PATCH' for method,_,_ in calls)


def test_mailbox_v4_result_preserves_generation():
    command=_command(); generation='gen-002'
    body='AI_BRIDGE_COMMAND_V4\n'+json.dumps({'bridge_id':'bridge-test','generation':generation,'command':command})
    patched=[]
    def handler(request:httpx.Request)->httpx.Response:
        headers={'X-RateLimit-Limit':'5000','X-RateLimit-Remaining':'4900','X-RateLimit-Reset':'2000000000'}
        if request.method=='GET' and request.url.path.endswith('/issues/comments'):
            return httpx.Response(200,headers=headers,json=[{'id':321,'issue_url':'https://api.github.com/repos/owner/repo/issues/1','body':'AI_BRIDGE_MAILBOX_V3\n'+json.dumps({'bridge_id':'bridge-test','state':'idle'})}])
        if request.method=='GET' and request.url.path.endswith('/issues/comments/321'):
            return httpx.Response(200,headers=headers,json={'id':321,'body':body})
        if request.method=='PATCH' and request.url.path.endswith('/issues/comments/321'):
            value=json.loads(request.content.decode('utf-8'))['body']; patched.append(value)
            return httpx.Response(200,headers=headers,json={'id':321,'body':value})
        raise AssertionError
    transport=gb.GitHubBusTransport(gb.GitHubBusConfig(repository='owner/repo',token='x',bridge_id='bridge-test'),client=httpx.Client(transport=httpx.MockTransport(handler)))
    transport.initialize_message_mode(); transport.fetch_commands()
    transport.publish_result(ExecutionResult(command_id='cmd-v4',status=ExecutionStatus.SUCCESS))
    assert patched[-1].startswith('AI_BRIDGE_RESULT_V4\n')
    payload=json.loads(patched[-1].split('\n',1)[1])
    assert payload['generation']==generation
    assert payload['command_id']=='cmd-v4'
