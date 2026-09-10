from ai_bridge.core.service import DuplicateCommandError
from ai_bridge.persistence.db import BridgeDB
from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult, ExecutionStatus
from ai_bridge.transport.runner import TransportRunner


def _command(command_id="cmd-runner-multi"):
    return CommandEnvelope.model_validate(
        {
            "protocol": "bridge/1",
            "command_id": command_id,
            "workspace": "Bridge",
            "adapter": "bridge_admin",
            "operation": "bridge.update.status",
            "arguments": {},
            "execution": {
                "verify": True,
                "checkpoint": "none",
                "dry_run": False,
            },
            "risk": "L1",
        }
    )


class _ClaimService:
    def __init__(self, db):
        self.db = db
        self.execute_calls = 0

    def execute(self, command):
        claim = self.db.claim_transport_command(command)
        if claim != "claimed":
            raise DuplicateCommandError(command.command_id)
        self.execute_calls += 1
        return ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={"ok": True},
        )


class _ReceiptTransport:
    key = "github:owner/repo:main:bridge-test"

    def __init__(self, command, *, fail_once_receipt=None):
        self.command = command
        self.acked = []
        self.published = []
        self.fail_once_receipt = fail_once_receipt
        self.failed_receipts = set()
        self.receipts = [
            {
                "receipt_id": "issue:201:chat-a:gen-a",
                "ingress_kind": "issue_comment",
                "routing": {"comment_id": 201},
            },
            {
                "receipt_id": f"contents:{command.command_id}",
                "ingress_kind": "contents",
                "routing": {"result_path": f"results/{command.command_id}.json"},
            },
        ]

    def fetch_commands(self):
        return [self.command]

    def requires_durable_ack(self, command):
        return True

    def ack_command(self, command):
        self.acked.append(command.command_id)

    def command_receipts(self, command_id):
        return list(self.receipts) if command_id == self.command.command_id else []

    def publish_result_for_receipt(self, result, receipt):
        receipt_id = receipt["receipt_id"]
        if (
            receipt_id == self.fail_once_receipt
            and receipt_id not in self.failed_receipts
        ):
            self.failed_receipts.add(receipt_id)
            raise RuntimeError("synthetic publication failure")
        self.published.append((receipt_id, result.command_id, result.status.value))

    def publish_result(self, result):
        raise AssertionError("receipt-aware runner must publish per receipt")


def test_runner_executes_once_and_publishes_same_terminal_result_to_both_receipts(tmp_path):
    command = _command()
    db = BridgeDB(tmp_path / "bridge.db")
    service = _ClaimService(db)
    transport = _ReceiptTransport(command)

    assert TransportRunner(service, transport).poll_once() == 1

    assert service.execute_calls == 1
    assert transport.acked == [command.command_id]
    assert [item[0] for item in transport.published] == [
        "issue:201:chat-a:gen-a",
        f"contents:{command.command_id}",
    ]
    receipts = db.list_ingress_receipts(transport.key, command.command_id)
    assert all(item["published_at"] for item in receipts)


def test_runner_retries_failed_receipt_publication_without_reexecuting_host(tmp_path):
    command = _command("cmd-publish-retry")
    db = BridgeDB(tmp_path / "bridge.db")
    service = _ClaimService(db)
    contents_receipt = f"contents:{command.command_id}"
    transport = _ReceiptTransport(command, fail_once_receipt=contents_receipt)
    runner = TransportRunner(service, transport)

    assert runner.poll_once() == 1
    assert service.execute_calls == 1
    first_receipts = db.list_ingress_receipts(transport.key, command.command_id)
    by_id = {item["receipt_id"]: item for item in first_receipts}
    assert by_id["issue:201:chat-a:gen-a"]["published_at"] is not None
    assert by_id[contents_receipt]["published_at"] is None

    assert runner.poll_once() == 0
    assert service.execute_calls == 1
    second_receipts = db.list_ingress_receipts(transport.key, command.command_id)
    assert all(item["published_at"] for item in second_receipts)
    assert contents_receipt in [item[0] for item in transport.published]


def test_runner_replays_terminal_row_to_pending_receipts_without_host_execution(tmp_path):
    command = _command("cmd-terminal-fanout")
    db = BridgeDB(tmp_path / "bridge.db")
    db.accept_transport_command(command)
    assert db.claim_transport_command(command) == "claimed"
    db.save_result(
        ExecutionResult(
            command_id=command.command_id,
            status=ExecutionStatus.SUCCESS,
            result={"value": 42},
        )
    )
    service = _ClaimService(db)
    transport = _ReceiptTransport(command)

    assert TransportRunner(service, transport).poll_once() == 1

    assert service.execute_calls == 0
    assert len(transport.published) == 2
    assert {item[1] for item in transport.published} == {command.command_id}
