"""Global Bridge MCP: no project root is captured at server startup."""
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
import ntpath


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('LOCAL_BRIDGE_REDIRECT_DENIED')


class Bridge:
    def request(self, path, body=None, timeout=8):
        cfg = json.loads((Path(os.environ.get('AI_BRIDGE_DATA_DIR', str(Path.home()/'.ai_bridge'))) / 'connection.json').read_text(encoding='utf-8'))
        url = urlsplit(cfg['url'])
        if url.scheme != 'http' or url.hostname not in ('127.0.0.1','localhost','::1') or not url.port or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
            raise ValueError('LOCAL_BRIDGE_URL_REQUIRED')
        request = Request(cfg['url'].rstrip('/') + path, data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer '+cfg['token'], 'Content-Type': 'application/json'})
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
                data = response.read(8_000_001)
        except HTTPError as exc:
            if exc.code == 404:
                raise ValueError('ENDPOINT_OR_COMMAND_NOT_FOUND: staged async/trace support requires Runtime activation') from None
            raise ValueError('BRIDGE_HTTP_'+str(exc.code)) from None
        if len(data)>8_000_000:
            raise ValueError('RESPONSE_TOO_LARGE: query fewer events')
        return json.loads(data)

    def context(self, operation=None):
        context = self.request('/client/context')
        advertised = {c['name'] for d in context.get('local_adapters', [])
                      if d['adapter'] == 'workspace' for c in d.get('capabilities', [])}
        context['workspace_access'] = {
            'operations': sorted(advertised.intersection(WORKSPACE_OPERATIONS)),
            'schema_query': 'ta_bridge_context(operation="workspace.patchset.apply")',
            'workflow': 'Read UTF-8 text and sha256 with workspace.file.read or workspace.files.read, then submit changes with expected_hash. Resolve conflict by reading again, not by blindly replaying.',
            'paths': 'Relative to the explicitly selected registered workspace; generic file writes to .ai-bridge are denied. Register commands via workspace.project.configure with a full preserved manifest and its previous SHA.',
            'limits': {'batch_read_files': 64, 'batch_read_total_chars': 500000,
                       'patchset_changes': 64, 'file_write_bytes': 2000000, 'patchset_total_bytes': 4000000},
        }
        if operation is not None:
            if operation not in WORKSPACE_OPERATIONS:
                raise ValueError('WORKSPACE_OPERATION_NOT_EXPOSED')
            if operation not in advertised:
                raise ValueError('CAPABILITY_NOT_ADVERTISED: inspect current context')
            context['workspace_access']['operation'] = operation
            context['workspace_access']['arguments_schema'] = WORKSPACE_OPERATIONS[operation]
        return context

    def submit(self, command_id, workspace, adapter, operation, arguments=None, session_id=None, project_file=None):
        context = self.context()
        if workspace not in context['workspaces']:
            raise ValueError('WORKSPACE_NOT_REGISTERED')
        if adapter in ('unreal','houdini'):
            if not session_id or not project_file:
                raise ValueError('EXPLICIT_SESSION_AND_PROJECT_REQUIRED')
            matches = [s for s in context['sessions'] if s['session_id']==session_id and s['adapter']==adapter
                       and ntpath.normcase(ntpath.normpath(s['project_file']))==ntpath.normcase(ntpath.normpath(project_file))]
            if len(matches)!=1:
                raise ValueError('CURRENT_SESSION_PROJECT_MISMATCH')
            target = matches[0]
            roots = [w['root'] for w in context.get('workspace_details',[]) if w['workspace_id']==workspace]
            if len(roots)!=1 or ntpath.commonpath([ntpath.normcase(roots[0]),ntpath.normcase(project_file)]) != ntpath.normpath(ntpath.normcase(roots[0])):
                raise ValueError('PROJECT_OUTSIDE_WORKSPACE')
        elif adapter=='workspace':
            if session_id or project_file:
                raise ValueError('WORKSPACE_COMMAND_HAS_NO_HOST_SESSION')
            if operation not in WORKSPACE_OPERATIONS:
                raise ValueError('WORKSPACE_OPERATION_NOT_EXPOSED')
            target = next((d for d in context.get('local_adapters',[]) if d['adapter']==adapter), {})
        else:
            raise ValueError('ONLY_UE_HOUDINI_AND_EXPOSED_WORKSPACE_OPERATIONS')
        capability = next((c for c in target.get('capabilities',[]) if c['name']==operation), None)
        if capability is None:
            raise ValueError('CAPABILITY_NOT_ADVERTISED: inspect current context')
        command = {'protocol':'bridge/1','command_id':command_id,'workspace':workspace,'adapter':adapter,
            'operation':operation,'arguments':arguments or {},'risk':capability['risk'],
            'execution':{'verify':True,'checkpoint':'auto','dry_run':False}}
        if session_id:
            command.update(session=session_id,project_file=project_file)
        try:
            return self.request('/client/jobs',command)
        except (TimeoutError, URLError):
            return {'command_id':command_id,'status':'acceptance_unknown','do_not_replay':True,
                    'next':'Query ta_bridge_status using this same command_id before any retry.'}

    def status(self, command_id, after=0, limit=50, log_offset=None):
        from urllib.parse import quote
        path='/client/jobs/'+quote(command_id,safe='')+'?after='+str(max(0,int(after)))+'&limit='+str(max(1,min(200,int(limit))))
        if log_offset is not None: path+='&log_offset='+str(max(0,int(log_offset)))
        return self.request(path)

    def result(self, command_id):
        from urllib.parse import quote
        return self.request('/results/'+quote(command_id,safe=''))

    def cancel(self, command_id, close_host=False):
        if type(close_host) is not bool: raise ValueError('CLOSE_HOST_MUST_BE_BOOLEAN')
        from urllib.parse import quote
        return self.request('/client/jobs/'+quote(command_id,safe='')+'/cancel', {'close_host':close_host})


def schema(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}


_PATH = {'type': 'string', 'minLength': 1, 'description': 'Workspace-relative path; no absolute paths or escapes.'}
_TEXT = {'type': 'string'}
_HASH = {'type': 'string', 'pattern': '^[0-9a-f]{64}$', 'description': 'sha256 returned by a previous file read.'}
_EDIT = schema({'old_text': {'type': 'string', 'minLength': 1}, 'new_text': _TEXT}, ['old_text', 'new_text'])
_CHANGE = {'oneOf': [
    schema({'op': {'const': 'create'}, 'path': _PATH, 'text': _TEXT, 'create_parents': {'type': 'boolean', 'default': False}}, ['op', 'path', 'text']),
    schema({'op': {'const': 'patch'}, 'path': _PATH, 'expected_hash': _HASH, **_EDIT['properties']}, ['op', 'path', 'expected_hash', 'old_text', 'new_text']),
    schema({'op': {'const': 'replace'}, 'path': _PATH, 'expected_hash': _HASH, 'text': _TEXT}, ['op', 'path', 'expected_hash', 'text']),
    schema({'op': {'const': 'delete'}, 'path': _PATH, 'expected_hash': _HASH}, ['op', 'path', 'expected_hash']),
]}
_PROCESS_PROPERTIES = {
    'argv': {'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'minLength': 1}, 'description': 'Explicit executable and argument list. Entries must not be blank or contain NUL.'},
    'cwd': {'type': 'string', 'default': '.', 'description': 'Workspace-relative directory; no parent traversal.'},
    'env': {'type': 'object', 'additionalProperties': {'type': 'string'}, 'description': 'Environment overrides; names must be nonempty, without = or NUL.'},
}
_COMMAND_SPEC = schema({**_PROCESS_PROPERTIES, 'timeout_seconds': {'type': 'number', 'minimum': 1, 'maximum': 7200, 'default': 300}}, ['argv'])
_SERVICE_SPEC = schema({**_PROCESS_PROPERTIES, 'reload': {'enum': ['native', 'restart', 'manual'], 'default': 'restart'}, 'stop_timeout_seconds': {'type': 'number', 'minimum': .1, 'maximum': 60, 'default': 10}}, ['argv'])
WORKSPACE_OPERATIONS = {
    'workspace.project.inspect': schema({}),
    'workspace.project.configure': {**schema({'manifest': schema({'schema_version': {'const': '1.0', 'default': '1.0'}, 'project': {'type': 'string', 'minLength': 1, 'maxLength': 128}, 'commands': {'type': 'object', 'additionalProperties': _COMMAND_SPEC}, 'services': {'type': 'object', 'additionalProperties': _SERVICE_SPEC}}, ['project']), 'expected_hash': _HASH}, ['manifest']),
                                    'description': 'Register or update named execution commands by replacing the structured project manifest. Read .ai-bridge/project.json first, preserve unrelated commands/services and supply its sha256 as expected_hash. New manifests omit expected_hash. Existing manifests require it. Does not execute commands or start services; registering executable arguments requires user-authorized scope.'},
    'workspace.command.run': schema({'command': {'type': 'string', 'description': 'Existing project manifest command name; not shell text.'}, 'timeout_seconds': {'type': 'number', 'exclusiveMinimum': 0}}, ['command']),
    'workspace.directory.list': schema({'path': {**_PATH, 'default': '.'}, 'max_entries': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'default': 200}}),
    'workspace.file.read': schema({'path': _PATH, 'max_chars': {'type': 'integer', 'minimum': 1000, 'maximum': 500000, 'default': 100000}}, ['path']),
    'workspace.files.read': schema({'paths': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': _PATH}, 'max_chars_per_file': {'type': 'integer', 'minimum': 1, 'maximum': 500000, 'default': 100000}, 'max_total_chars': {'type': 'integer', 'minimum': 1, 'maximum': 500000, 'default': 500000}}, ['paths']),
    'workspace.file.create': schema({'path': _PATH, 'text': _TEXT, 'create_parents': {'type': 'boolean', 'default': False}}, ['path', 'text']),
    'workspace.file.patch': {**schema({'path': _PATH, 'expected_hash': _HASH, **_EDIT['properties'], 'edits': {'type': 'array', 'minItems': 1, 'maxItems': 32, 'items': _EDIT}}, ['path']),
                           'oneOf': [{'required': ['old_text', 'new_text'], 'not': {'required': ['edits']}}, {'required': ['edits'], 'not': {'anyOf': [{'required': ['old_text']}, {'required': ['new_text']}]}}],
                           'description': 'Unique text replacement, or sequential edits. Supply expected_hash to reject stale reads; otherwise backend uses a fresh-read byte check. Single-file matching accepts equivalent LF/CRLF.'},
    'workspace.patchset.apply': {**schema({'changes': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': _CHANGE}, 'post_commands': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Optional existing manifest command names run after writes; failure attempts to restore changed files. Command side effects are not rolled back.'}}, ['changes']),
                               'description': 'Prevalidates all changes before writing. Exact unique old_text required for patch; SHA required for every existing file. Duplicate paths denied. Best-effort file restoration on failure; not a transaction against external editors or process termination.'},
}


TOOLS = [
    ('ta_bridge_cancel','Cancel the process tree owned by command_id. close_host defaults false; set true only when the user also requests normal closure of that command-bound UE/Houdini session. Unsaved-work prompts require the user; never force-kills the Host or selects a different project. Check status for actual exit.',schema({'command_id':{'type':'string'},'close_host':{'type':'boolean','default':False}},['command_id']),False,'cancel'),
    ('ta_bridge_context','Discover live UE/Houdini sessions, projects, workspaces and capabilities. Also lists exposed workspace file operations; supply operation (for example workspace.patchset.apply) to read its exact arguments schema. Global scope; never guesses a project.',schema({'operation': {'type': 'string', 'enum': sorted(WORKSPACE_OPERATIONS)}}),True,'context'),
    ('ta_bridge_submit','Submit an authorized UE/Houdini operation, workspace UTF-8 single/batch read/write, workspace.project.configure to register named commands, or workspace.command.run to execute one. Discover arguments via ta_bridge_context(operation=...). Read sha256 before modifying existing files/manifests; batch edits and existing manifest updates require expected_hash. Supply a stable command_id; query it on timeout, never generate a new ID to retry. Host calls require explicit session/project/workspace; workspace calls have no Host session.',schema({
        'command_id':{'type':'string'},'workspace':{'type':'string'},'adapter':{'enum':['unreal','houdini','workspace']},
        'operation':{'type':'string'},'arguments':{'type':'object'},'session_id':{'type':'string'},'project_file':{'type':'string'}},['command_id','workspace','adapter','operation']),False,'submit'),
    ('ta_bridge_status','Read command state and incremental lifecycle/process progress using after cursor; log_offset reads up to 16KB of the full process log. Silence does not prove a stall; inspect process_running and output_idle_seconds. Historical commands may have no trace.',schema({'command_id':{'type':'string'},'after':{'type':'integer','default':0},'limit':{'type':'integer','default':50},'log_offset':{'type':'integer'}},['command_id']),True,'status'),
    ('ta_bridge_result','Read original command and final receipt/evidence reference. Missing terminal result means unknown or still running; never replay a mutation to find its result.',schema({'command_id':{'type':'string'}},['command_id']),True,'result'),
]


def serve():
    bridge = Bridge()
    mapping = {row[0]:row for row in TOOLS}
    for line in sys.stdin:
        rid = None
        try:
            if len(line.encode('utf-8'))>32_000_000: raise ValueError('REQUEST_TOO_LARGE')
            message = json.loads(line)
            rid = message.get('id')
            if rid is None: continue
            method = message['method']
            params = message.get('params') or {}
            if method=='initialize':
                result = {'protocolVersion':'2025-06-18','serverInfo':{'name':'global-ai-bridge','version':'1.1.0'},'capabilities':{'tools':{}},
                    'instructions':'Use context to select explicit current project/session. Submit once with a stable command_id; poll status with its cursor. Never interpret timeout/silent output as permission to replay. Capabilities describe execution facts; only user-authorized actions may be submitted.'}
            elif method=='tools/list':
                result = {'tools':[{'name':n,'description':d,'inputSchema':s,'annotations':{'readOnlyHint':ro,'openWorldHint':False}} for n,d,s,ro,fn in TOOLS]}
            elif method=='tools/call':
                try:
                    tool = mapping[params['name']]
                    args = params.get('arguments') or {}
                    if not isinstance(args,dict) or set(args)-set(tool[2]['properties']) or set(tool[2]['required'])-set(args):
                        raise ValueError('INVALID_ARGUMENTS')
                    data = getattr(bridge,tool[4])(**args)
                    result = {'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}],'isError':False}
                except Exception as exc:
                    result = {'content':[{'type':'text','text':json.dumps({'error':type(exc).__name__,'detail':str(exc)[:400]},ensure_ascii=False)}],'isError':True}
            elif method=='ping': result={}
            else:
                raise ValueError('METHOD_NOT_SUPPORTED')
            answer={'jsonrpc':'2.0','id':rid,'result':result}
        except Exception as exc:
            answer={'jsonrpc':'2.0','id':rid,'error':{'code':-32600,'message':type(exc).__name__}}
        print(json.dumps(answer,ensure_ascii=False),flush=True)


if __name__=='__main__':
    serve()
