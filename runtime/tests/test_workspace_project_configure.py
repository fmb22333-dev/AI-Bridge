from ai_bridge.app import build_runtime
from ai_bridge.adapters.workspace_worker import WorkspaceWorkerExecutor
from ai_bridge.core.worker_supervisor import WorkerSupervisor
from ai_bridge.core.workspace import WorkspaceRegistry
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionStatus


def _command(command_id, operation, arguments=None):
    return CommandEnvelope(
        command_id=command_id,
        workspace='Dev',
        adapter='workspace',
        operation=operation,
        arguments=arguments or {},
    )


def test_runtime_exposes_structured_workspace_project_configure(tmp_path):
    app, service, _, _ = build_runtime(data_dir=tmp_path / 'data', port=18765)
    try:
        capabilities = {item.name: item for item in service.adapter_registry.get('workspace').capabilities}
        assert 'workspace.project.configure' in capabilities
        capability = capabilities['workspace.project.configure']
        assert capability.write is True
        assert capability.host_mutation is False
        assert capability.risk.value == 'L2'
    finally:
        app.state.worker_supervisor.close()


def test_project_configure_creates_valid_control_manifest(tmp_path):
    registry = WorkspaceRegistry()
    registry.register('Dev', tmp_path)
    supervisor = WorkerSupervisor(max_log_lines=32)
    executor = WorkspaceWorkerExecutor(workspaces=registry, supervisor=supervisor)
    manifest = {
        'schema_version': '1.0',
        'project': 'Configured Project',
        'commands': {
            'smoke': {
                'argv': ['python', '-c', "print('ok')"],
                'cwd': '.',
                'timeout_seconds': 30
            }
        },
        'services': {}
    }
    try:
        configured = executor.execute(
            _command('configure-1', 'workspace.project.configure', {'manifest': manifest})
        )
        assert configured.status == ExecutionStatus.SUCCESS
        assert configured.result['created'] is True
        assert configured.result['verified'] is True
        assert configured.result['path'] == '.ai-bridge/project.json'

        inspected = executor.execute(_command('inspect-1', 'workspace.project.inspect'))
        assert inspected.status == ExecutionStatus.SUCCESS
        assert inspected.result['project'] == 'Configured Project'
        assert inspected.result['commands'] == ['smoke']
    finally:
        supervisor.close()
