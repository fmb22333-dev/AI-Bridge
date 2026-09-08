from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from ai_bridge.protocol.command import CommandEnvelope
from ai_bridge.protocol.result import ExecutionResult
from .base import TransportHealth


COMMAND_MARKER = "AI_BRIDGE_COMMAND_V2"
RESULT_MARKER = "AI_BRIDGE_RESULT_V2"
MAILBOX_MARKER_V3 = "AI_BRIDGE_MAILBOX_V3"
COMMAND_MARKER_V3 = "AI_BRIDGE_COMMAND_V3"
RESULT_MARKER_V3 = "AI_BRIDGE_RESULT_V3"


@dataclass(frozen=True)
class GitHubBusConfig:
    repository: str
    token: str
    bridge_id: str
    branch: str = "main"
    api_base: str = "https://api.github.com"
    path_prefix: str = ".ai-bridge"
    issue_number: int = 1
    message_mode: str = "auto"


class GitHubBusTransport:
    """GitHub transport with three progressively slower paths.

    V3 fast path:
      one fixed Issue Comment acts as a mailbox. ChatGPT UPDATEs that comment to
      AI_BRIDGE_COMMAND_V3; Bridge polls only that specific comment and PATCHes it
      in place to AI_BRIDGE_RESULT_V3.

    V2 fallback:
      individual Issue Comments are commands and are PATCHed in place as results.

    Contents fallback:
      command/result JSON files in the repository. This remains the durable
      compatibility path when Issues API access is unavailable.
    """

    def __init__(self, config: GitHubBusConfig, *, client: httpx.Client | None = None) -> None:
        if config.repository.count("/") != 1:
            raise ValueError("repository must be owner/name")
        if not config.token:
            raise ValueError("GitHub token required")
        self.config = config
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
            http2=False,
        )
        self._commands_etag: str | None = None
        self._known_command_shas: dict[str, str] = {}
        self._command_index_initialized = False
        self._presence_sha: str | None = None

        self._message_mode = "auto" if config.message_mode == "auto" else config.message_mode
        self._message_mode_detail = "not probed"
        self._comments_etag: str | None = None
        self._cached_comment_items: list[dict] | None = None
        self._comment_refs: dict[str, dict] = {}
        self._comment_write_ok: bool | None = None

        self._mailbox_comment_id: int | None = None
        self._mailbox_etag: str | None = None

        self._rate_limit: dict[str, int | float | str | None] = {
            "limit": None,
            "remaining": None,
            "reset": None,
            "last_status": None,
            "polls": 0,
            "not_modified": 0,
        }

    @property
    def key(self) -> str:
        return f"github:{self.config.repository}:{self.config.branch}:{self.config.bridge_id}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _observe_response(self, response: httpx.Response, *, poll: bool = False) -> None:
        headers = response.headers

        def _int(name: str):
            raw = headers.get(name)
            try:
                return int(raw) if raw is not None else None
            except (TypeError, ValueError):
                return None

        limit = _int("X-RateLimit-Limit")
        remaining = _int("X-RateLimit-Remaining")
        reset = _int("X-RateLimit-Reset")
        if limit is not None:
            self._rate_limit["limit"] = limit
        if remaining is not None:
            self._rate_limit["remaining"] = remaining
        if reset is not None:
            self._rate_limit["reset"] = reset
        self._rate_limit["last_status"] = response.status_code
        if poll:
            self._rate_limit["polls"] = int(self._rate_limit.get("polls") or 0) + 1
            if response.status_code == 304:
                self._rate_limit["not_modified"] = int(self._rate_limit.get("not_modified") or 0) + 1

    def rate_snapshot(self) -> dict:
        data = dict(self._rate_limit)
        polls = int(data.get("polls") or 0)
        not_modified = int(data.get("not_modified") or 0)
        data["not_modified_ratio"] = round(not_modified / polls, 4) if polls else 0.0
        data["message_mode"] = self._message_mode
        data["message_mode_detail"] = self._message_mode_detail
        data["comment_write_ok"] = self._comment_write_ok
        data["issue_number"] = self.config.issue_number
        data["mailbox_comment_id"] = self._mailbox_comment_id
        return data

    def message_state(self) -> dict:
        return {
            "mode": self._message_mode,
            "detail": self._message_mode_detail,
            "issue_number": self.config.issue_number,
            "comment_write_ok": self._comment_write_ok,
            "mailbox_comment_id": self._mailbox_comment_id,
        }

    @staticmethod
    def _response_detail(response: httpx.Response) -> str:
        message = ""
        try:
            payload = response.json()
            message = str(payload.get("message") or "").strip()
        except Exception:
            message = response.text.strip()[:300]
        suffix = f": {message}" if message else ""
        return f"HTTP {response.status_code}{suffix}"

    def _bus_path(self, path: str) -> str:
        prefix = self.config.path_prefix.strip("/")
        return f"{prefix}/{path.lstrip('/')}" if prefix else path.lstrip("/")

    def _contents_url(self, path: str) -> str:
        encoded = "/".join(quote(part, safe="") for part in path.split("/"))
        return f"{self.config.api_base}/repos/{self.config.repository}/contents/{encoded}"

    def _repo_comments_url(self) -> str:
        return f"{self.config.api_base}/repos/{self.config.repository}/issues/comments"

    def _issue_comments_url(self) -> str:
        return (
            f"{self.config.api_base}/repos/{self.config.repository}/issues/"
            f"{self.config.issue_number}/comments"
        )

    def _comment_url(self, comment_id: int) -> str:
        return f"{self.config.api_base}/repos/{self.config.repository}/issues/comments/{comment_id}"

    def _get_json(self, url: str, **kwargs):
        response = self.client.get(url, headers=self.headers, **kwargs)
        self._observe_response(response)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RuntimeError(self._response_detail(response))
        return response.json()

    def _read_file(self, path: str) -> bytes | None:
        data = self._get_json(self._contents_url(path), params={"ref": self.config.branch})
        if data is None:
            return None
        content = data.get("content", "").replace("\n", "")
        return base64.b64decode(content) if content else b""

    def _result_names(self) -> set[str]:
        directory = self._bus_path(f"results/{self.config.bridge_id}")
        response = self.client.get(
            self._contents_url(directory),
            headers=self.headers,
            params={"ref": self.config.branch},
        )
        self._observe_response(response)
        if response.status_code == 404:
            return set()
        if response.status_code >= 400:
            raise RuntimeError(self._response_detail(response))
        payload = response.json()
        if not isinstance(payload, list):
            return set()
        return {
            str(item.get("name"))
            for item in payload
            if item.get("type") == "file" and str(item.get("name", "")).endswith(".json")
        }

    def _request_comment_page(self, *, conditional: bool) -> httpx.Response:
        headers = dict(self.headers)
        if conditional and self._comments_etag:
            headers["If-None-Match"] = self._comments_etag
        return self.client.get(
            self._repo_comments_url(),
            headers=headers,
            params={"sort": "updated", "direction": "desc", "per_page": 100},
        )

    def _mailbox_payload(self, state: str = "idle") -> str:
        return MAILBOX_MARKER_V3 + "\n" + json.dumps(
            {
                "bridge_id": self.config.bridge_id,
                "protocol": "bridge/1",
                "state": state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _find_mailbox(self, items: list[dict]) -> int | None:
        for item in items:
            body = str(item.get("body") or "")
            if not body.startswith(MAILBOX_MARKER_V3 + "\n"):
                continue
            try:
                payload = json.loads(body.split("\n", 1)[1])
                if str(payload.get("bridge_id") or "") != self.config.bridge_id:
                    continue
                return int(item["id"])
            except Exception:
                continue
        return None

    def _create_mailbox(self) -> int | None:
        response = self.client.post(
            self._issue_comments_url(),
            headers=self.headers,
            json={"body": self._mailbox_payload()},
        )
        self._observe_response(response)
        if response.status_code in (401, 403, 404, 410):
            return None
        if response.status_code >= 400:
            raise RuntimeError(self._response_detail(response))
        payload = response.json()
        return int(payload["id"])

    def initialize_message_mode(self) -> str:
        if self._message_mode != "auto":
            return self._message_mode
        try:
            response = self._request_comment_page(conditional=False)
            self._observe_response(response)
            if response.status_code == 200:
                payload = response.json()
                items = payload if isinstance(payload, list) else []
                self._cached_comment_items = items
                self._comments_etag = response.headers.get("ETag") or self._comments_etag

                mailbox_id = self._find_mailbox(items)
                if mailbox_id is None:
                    mailbox_id = self._create_mailbox()
                if mailbox_id is not None:
                    self._mailbox_comment_id = mailbox_id
                    self._mailbox_etag = None
                    self._message_mode = "issue_mailbox_v3"
                    self._message_mode_detail = "Fixed Issue Comment mailbox fast path available"
                    return self._message_mode

                self._message_mode = "issue_comment_v2"
                self._message_mode_detail = "Issue Comment V2 available; mailbox creation unavailable"
                return self._message_mode

            if response.status_code in (401, 403, 404, 410):
                self._message_mode = "contents"
                self._message_mode_detail = (
                    f"Issue Comment unavailable ({response.status_code}); Contents fallback active"
                )
                return self._message_mode
            raise RuntimeError(self._response_detail(response))
        except Exception as exc:
            self._message_mode = "contents"
            self._message_mode_detail = (
                f"Issue Comment probe failed; Contents fallback: {type(exc).__name__}: {exc}"
            )
            return self._message_mode

    def _parse_command_body(self, body: str, marker: str, *, ref: dict) -> CommandEnvelope | None:
        if not body.startswith(marker + "\n"):
            return None
        try:
            payload = json.loads(body.split("\n", 1)[1])
            if str(payload.get("bridge_id") or "") != self.config.bridge_id:
                return None
            command_payload = payload.get("command")
            if not isinstance(command_payload, dict):
                return None
            command = CommandEnvelope.model_validate(command_payload)
        except Exception:
            return None
        self._comment_refs[command.command_id] = {
            **ref,
            "command": command_payload,
        }
        return command

    def _comments_to_commands(self, items: list[dict]) -> list[CommandEnvelope]:
        commands: list[CommandEnvelope] = []
        issue_suffix = f"/issues/{self.config.issue_number}"
        for item in items:
            if not str(item.get("issue_url") or "").endswith(issue_suffix):
                continue
            try:
                comment_id = int(item["id"])
            except Exception:
                continue
            command = self._parse_command_body(
                str(item.get("body") or ""),
                COMMAND_MARKER,
                ref={"comment_id": comment_id, "mode": "issue_comment_v2"},
            )
            if command is not None:
                commands.append(command)
        return commands

    def _fetch_mailbox_commands(self) -> list[CommandEnvelope] | None:
        if self._mailbox_comment_id is None:
            self._message_mode = "issue_comment_v2"
            self._message_mode_detail = "Mailbox ID missing; Issue Comment V2 fallback active"
            return None
        headers = dict(self.headers)
        if self._mailbox_etag:
            headers["If-None-Match"] = self._mailbox_etag
        response = self.client.get(
            self._comment_url(self._mailbox_comment_id),
            headers=headers,
        )
        self._observe_response(response, poll=True)
        if response.status_code == 304:
            return []
        if response.status_code in (401, 403, 404, 410):
            self._message_mode = "issue_comment_v2"
            self._message_mode_detail = (
                f"Mailbox unavailable ({response.status_code}); Issue Comment V2 fallback active"
            )
            self._mailbox_etag = None
            return None
        if response.status_code >= 400:
            raise RuntimeError(self._response_detail(response))
        self._mailbox_etag = response.headers.get("ETag") or self._mailbox_etag
        payload = response.json()
        command = self._parse_command_body(
            str(payload.get("body") or ""),
            COMMAND_MARKER_V3,
            ref={"comment_id": self._mailbox_comment_id, "mode": "issue_mailbox_v3"},
        )
        return [command] if command is not None else []

    def _fetch_comment_commands(self) -> list[CommandEnvelope] | None:
        if self._cached_comment_items is not None:
            items = self._cached_comment_items
            self._cached_comment_items = None
            return self._comments_to_commands(items)
        response = self._request_comment_page(conditional=True)
        self._observe_response(response, poll=True)
        if response.status_code == 304:
            return []
        if response.status_code in (401, 403, 404, 410):
            self._message_mode = "contents"
            self._message_mode_detail = (
                f"Issue Comment became unavailable ({response.status_code}); Contents fallback active"
            )
            return None
        if response.status_code >= 400:
            raise RuntimeError(self._response_detail(response))
        self._comments_etag = response.headers.get("ETag") or self._comments_etag
        payload = response.json()
        return self._comments_to_commands(payload if isinstance(payload, list) else [])

    def _fetch_contents_commands(self) -> list[CommandEnvelope]:
        directory = self._bus_path(f"commands/{self.config.bridge_id}")
        headers = dict(self.headers)
        if self._commands_etag:
            headers["If-None-Match"] = self._commands_etag
        response = self.client.get(
            self._contents_url(directory),
            headers=headers,
            params={"ref": self.config.branch},
        )
        self._observe_response(response, poll=True)
        if response.status_code in (304, 404):
            return []
        if response.status_code >= 400:
            raise RuntimeError(self._response_detail(response))

        self._commands_etag = response.headers.get("ETag") or self._commands_etag
        items = response.json()
        if not isinstance(items, list):
            return []

        completed_names = set()
        if not self._command_index_initialized:
            completed_names = self._result_names()

        candidates: list[dict] = []
        next_known: dict[str, str] = {}
        for item in sorted(items, key=lambda value: value.get("name", "")):
            name = str(item.get("name", ""))
            if item.get("type") != "file" or not name.endswith(".json"):
                continue
            sha = str(item.get("sha") or "")
            next_known[name] = sha
            if not self._command_index_initialized:
                if name in completed_names:
                    continue
                candidates.append(item)
                continue
            if self._known_command_shas.get(name) != sha:
                candidates.append(item)

        self._known_command_shas = next_known
        self._command_index_initialized = True

        commands: list[CommandEnvelope] = []
        for item in candidates:
            raw = self._read_file(f"{directory}/{item['name']}")
            if raw is not None:
                commands.append(CommandEnvelope.model_validate_json(raw))
        return commands

    def fetch_commands(self) -> list[CommandEnvelope]:
        if self._message_mode == "auto":
            self.initialize_message_mode()
        if self._message_mode == "issue_mailbox_v3":
            commands = self._fetch_mailbox_commands()
            if commands is not None:
                return commands
        if self._message_mode == "issue_comment_v2":
            commands = self._fetch_comment_commands()
            if commands is not None:
                return commands
        return self._fetch_contents_commands()

    def publish_result(self, result: ExecutionResult) -> None:
        ref = self._comment_refs.get(result.command_id)
        if ref is not None and ref.get("mode") in {"issue_mailbox_v3", "issue_comment_v2"}:
            is_v3 = ref.get("mode") == "issue_mailbox_v3"
            envelope = {
                "bridge_id": self.config.bridge_id,
                "command_id": result.command_id,
                "command": ref["command"],
                "result": json.loads(result.model_dump_json()),
            }
            marker = RESULT_MARKER_V3 if is_v3 else RESULT_MARKER
            body = marker + "\n" + json.dumps(
                envelope, ensure_ascii=False, separators=(",", ":")
            )
            response = self.client.patch(
                self._comment_url(int(ref["comment_id"])),
                headers=self.headers,
                json={"body": body},
            )
            self._observe_response(response)
            if response.status_code < 400:
                self._comment_write_ok = True
                if is_v3:
                    self._mailbox_etag = response.headers.get("ETag") or None
                else:
                    self._comments_etag = None
                return
            self._comment_write_ok = False
            self._message_mode_detail = (
                f"Comment result PATCH failed ({response.status_code}); result file fallback active"
            )

        self._publish_json(
            self._bus_path(f"results/{self.config.bridge_id}/{result.command_id}.json"),
            json.loads(result.model_dump_json()),
            message=f"bridge result {result.command_id}",
            create_first=True,
        )

    def _publish_json(
        self,
        path: str,
        payload: dict,
        *,
        message: str,
        create_first: bool = False,
        known_sha: str | None = None,
    ) -> str | None:
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        request = {
            "message": message,
            "content": base64.b64encode(body).decode("ascii"),
            "branch": self.config.branch,
        }

        if known_sha:
            request["sha"] = known_sha
        elif not create_first:
            existing = self._get_json(self._contents_url(path), params={"ref": self.config.branch})
            if existing is not None and existing.get("sha"):
                request["sha"] = existing["sha"]

        response = self.client.put(self._contents_url(path), headers=self.headers, json=request)
        self._observe_response(response)

        if create_first and response.status_code in (409, 422):
            existing = self._get_json(self._contents_url(path), params={"ref": self.config.branch})
            if existing is None or not existing.get("sha"):
                raise RuntimeError(self._response_detail(response))
            request["sha"] = existing["sha"]
            response = self.client.put(self._contents_url(path), headers=self.headers, json=request)
            self._observe_response(response)

        if response.status_code >= 400:
            raise RuntimeError(self._response_detail(response))
        try:
            data = response.json()
            return (data.get("content") or {}).get("sha")
        except Exception:
            return None

    def publish_presence(self, payload: dict) -> None:
        self._presence_sha = self._publish_json(
            self._bus_path(f"status/{self.config.bridge_id}.json"),
            payload,
            message=f"bridge status {self.config.bridge_id}",
            known_sha=self._presence_sha,
        ) or self._presence_sha

    def health(self) -> TransportHealth:
        url = (
            f"{self.config.api_base}/repos/{self.config.repository}/branches/"
            f"{quote(self.config.branch, safe='')}"
        )
        try:
            response = self.client.get(url, headers=self.headers)
            self._observe_response(response)
            if response.status_code == 200:
                return TransportHealth(True, "connected")
            if response.status_code == 401:
                return TransportHealth(False, "HTTP 401: token invalid or expired")
            if response.status_code == 403:
                return TransportHealth(False, "HTTP 403: token lacks repository access")
            if response.status_code == 404:
                return TransportHealth(False, "HTTP 404: repository or branch not found / not accessible")
            return TransportHealth(False, self._response_detail(response))
        except Exception as exc:
            return TransportHealth(False, f"{type(exc).__name__}: {exc}")


# AI_BRIDGE_P0_MAILBOX_V4
COMMAND_MARKER_V4 = 'AI_BRIDGE_COMMAND_V4'
ACK_MARKER_V4 = 'AI_BRIDGE_ACK_V4'
RESULT_MARKER_V4 = 'AI_BRIDGE_RESULT_V4'
_p0_fetch_mailbox_commands_v3 = GitHubBusTransport._fetch_mailbox_commands
_p0_publish_result_v3 = GitHubBusTransport.publish_result


def _p0_fetch_mailbox_commands(self):
    if self._mailbox_comment_id is None:
        return _p0_fetch_mailbox_commands_v3(self)
    headers = dict(self.headers)
    if self._mailbox_etag:
        headers['If-None-Match'] = self._mailbox_etag
    response = self.client.get(self._comment_url(self._mailbox_comment_id), headers=headers)
    self._observe_response(response, poll=True)
    if response.status_code == 304:
        return []
    if response.status_code in (401,403,404,410):
        self._message_mode = 'issue_comment_v2'
        self._message_mode_detail = f'Mailbox unavailable ({response.status_code}); Issue Comment V2 fallback active'
        self._mailbox_etag = None
        return None
    if response.status_code >= 400:
        raise RuntimeError(self._response_detail(response))
    self._mailbox_etag = response.headers.get('ETag') or self._mailbox_etag
    payload = response.json()
    body = str(payload.get('body') or '')
    if not body.startswith(COMMAND_MARKER_V4 + '\n'):
        command = self._parse_command_body(
            body, COMMAND_MARKER_V3,
            ref={'comment_id':self._mailbox_comment_id,'mode':'issue_mailbox_v3'},
        )
        return [command] if command is not None else []
    try:
        envelope = json.loads(body.split('\n',1)[1])
        if str(envelope.get('bridge_id') or '') != self.config.bridge_id:
            return []
        generation = str(envelope.get('generation') or '').strip()
        if not generation:
            return []
        command_payload = envelope.get('command')
        if not isinstance(command_payload,dict):
            return []
        command = CommandEnvelope.model_validate(command_payload)
    except Exception:
        return []
    ack = ACK_MARKER_V4 + '\n' + json.dumps({
        'bridge_id':self.config.bridge_id,
        'generation':generation,
        'command_id':command.command_id,
        'state':'accepted',
    },ensure_ascii=False,separators=(',',':'))
    ack_response = self.client.patch(
        self._comment_url(self._mailbox_comment_id),
        headers=self.headers,
        json={'body':ack},
    )
    self._observe_response(ack_response)
    if ack_response.status_code >= 400:
        raise RuntimeError('Mailbox V4 ACK failed: ' + self._response_detail(ack_response))
    self._mailbox_etag = ack_response.headers.get('ETag') or None
    self._comment_refs[command.command_id] = {
        'comment_id':self._mailbox_comment_id,
        'mode':'issue_mailbox_v4',
        'generation':generation,
        'command':command_payload,
    }
    return [command]


def _p0_publish_result(self, result):
    ref = self._comment_refs.get(result.command_id)
    if ref is not None and ref.get('mode') == 'issue_mailbox_v4':
        envelope = {
            'bridge_id':self.config.bridge_id,
            'generation':ref['generation'],
            'command_id':result.command_id,
            'command':ref['command'],
            'result':json.loads(result.model_dump_json()),
        }
        body = RESULT_MARKER_V4 + '\n' + json.dumps(envelope,ensure_ascii=False,separators=(',',':'))
        response = self.client.patch(
            self._comment_url(int(ref['comment_id'])),
            headers=self.headers,
            json={'body':body},
        )
        self._observe_response(response)
        if response.status_code < 400:
            self._comment_write_ok = True
            self._mailbox_etag = response.headers.get('ETag') or None
            return
        self._comment_write_ok = False
    return _p0_publish_result_v3(self,result)


GitHubBusTransport._fetch_mailbox_commands = _p0_fetch_mailbox_commands
GitHubBusTransport.publish_result = _p0_publish_result


# AI_BRIDGE_MULTI_CHANNEL_V5
CHANNEL_COMMAND_MARKER_V5 = "AI_BRIDGE_COMMAND_V5"
CHANNEL_ACK_MARKER_V5 = "AI_BRIDGE_ACK_V5"
CHANNEL_RESULT_MARKER_V5 = "AI_BRIDGE_RESULT_V5"

_v5_initialize_message_mode_base = GitHubBusTransport.initialize_message_mode
_v5_fetch_commands_base = GitHubBusTransport.fetch_commands
_v5_publish_result_base = GitHubBusTransport.publish_result
_v5_message_state_base = GitHubBusTransport.message_state


def _v5_decode_envelope(body: str, markers: tuple[str, ...]) -> tuple[str, dict] | None:
    for marker in markers:
        if not body.startswith(marker + "\n"):
            continue
        try:
            payload = json.loads(body.split("\n", 1)[1])
        except Exception:
            return None
        return marker, payload if isinstance(payload, dict) else {}
    return None


def _v5_find_mailbox(self, items: list[dict]) -> int | None:
    markers = (
        MAILBOX_MARKER_V3,
        COMMAND_MARKER_V3,
        RESULT_MARKER_V3,
        COMMAND_MARKER_V4,
        ACK_MARKER_V4,
        RESULT_MARKER_V4,
    )
    for item in items:
        body = str(item.get("body") or "")
        decoded = _v5_decode_envelope(body, markers)
        if decoded is None:
            continue
        _, payload = decoded
        if str(payload.get("bridge_id") or "") != self.config.bridge_id:
            continue
        try:
            return int(item["id"])
        except Exception:
            continue
    return None


def _v5_initialize_message_mode(self) -> str:
    mode = _v5_initialize_message_mode_base(self)
    if mode == "issue_mailbox_v3":
        self._message_mode = "issue_channel_v5"
        self._message_mode_detail = (
            "Dynamic Issue Comment channels V5 active; fixed V4 mailbox remains compatible"
        )
    return self._message_mode


def _v5_message_state(self) -> dict:
    state = _v5_message_state_base(self)
    if self._message_mode == "issue_channel_v5":
        state["channel_protocol"] = "v5"
        state["multi_channel"] = True
        state["channel_discovery"] = "recent_issue_comments"
        state["channel_window"] = 100
    return state


def _v5_seen(self, key: str) -> bool:
    seen = getattr(self, "_v5_seen_command_keys", None)
    if seen is None:
        seen = set()
        self._v5_seen_command_keys = seen
    if key in seen:
        return True
    if len(seen) >= 2048:
        seen.clear()
    seen.add(key)
    return False


def _v5_ack_channel_item(self, item: dict, body: str) -> CommandEnvelope | None:
    try:
        envelope = json.loads(body.split("\n", 1)[1])
        if str(envelope.get("bridge_id") or "") != self.config.bridge_id:
            return None
        channel_id = str(envelope.get("channel_id") or "").strip()
        generation = str(envelope.get("generation") or "").strip()
        if not channel_id or len(channel_id) > 128:
            return None
        if not generation or len(generation) > 160:
            return None
        command_payload = envelope.get("command")
        if not isinstance(command_payload, dict):
            return None
        command = CommandEnvelope.model_validate(command_payload)
        comment_id = int(item["id"])
    except Exception:
        return None

    key = f"{comment_id}:{channel_id}:{generation}:{command.command_id}"
    if _v5_seen(self, key):
        return None

    ack = CHANNEL_ACK_MARKER_V5 + "\n" + json.dumps(
        {
            "bridge_id": self.config.bridge_id,
            "channel_id": channel_id,
            "generation": generation,
            "command_id": command.command_id,
            "state": "accepted",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    response = self.client.patch(
        self._comment_url(comment_id),
        headers=self.headers,
        json={"body": ack},
    )
    self._observe_response(response)
    if response.status_code >= 400:
        raise RuntimeError("Channel V5 ACK failed: " + self._response_detail(response))
    self._comments_etag = None
    self._comment_refs[command.command_id] = {
        "comment_id": comment_id,
        "mode": "issue_channel_v5",
        "channel_id": channel_id,
        "generation": generation,
        "command": command_payload,
    }
    return command


def _v5_ack_legacy_v4_item(self, item: dict, body: str) -> CommandEnvelope | None:
    try:
        envelope = json.loads(body.split("\n", 1)[1])
        if str(envelope.get("bridge_id") or "") != self.config.bridge_id:
            return None
        generation = str(envelope.get("generation") or "").strip()
        command_payload = envelope.get("command")
        if not generation or not isinstance(command_payload, dict):
            return None
        command = CommandEnvelope.model_validate(command_payload)
        comment_id = int(item["id"])
        if self._mailbox_comment_id is not None and comment_id != int(self._mailbox_comment_id):
            return None
    except Exception:
        return None

    key = f"legacy-v4:{comment_id}:{generation}:{command.command_id}"
    if _v5_seen(self, key):
        return None

    ack = ACK_MARKER_V4 + "\n" + json.dumps(
        {
            "bridge_id": self.config.bridge_id,
            "generation": generation,
            "command_id": command.command_id,
            "state": "accepted",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    response = self.client.patch(
        self._comment_url(comment_id),
        headers=self.headers,
        json={"body": ack},
    )
    self._observe_response(response)
    if response.status_code >= 400:
        raise RuntimeError("Mailbox V4 ACK failed: " + self._response_detail(response))
    self._comments_etag = None
    self._mailbox_etag = response.headers.get("ETag") or None
    self._comment_refs[command.command_id] = {
        "comment_id": comment_id,
        "mode": "issue_mailbox_v4",
        "generation": generation,
        "command": command_payload,
    }
    return command


def _v5_comments_to_commands(self, items: list[dict]) -> list[CommandEnvelope]:
    commands: list[CommandEnvelope] = []
    issue_suffix = f"/issues/{self.config.issue_number}"
    for item in items:
        if not str(item.get("issue_url") or "").endswith(issue_suffix):
            continue
        try:
            comment_id = int(item["id"])
        except Exception:
            continue
        body = str(item.get("body") or "")

        if body.startswith(CHANNEL_COMMAND_MARKER_V5 + "\n"):
            command = _v5_ack_channel_item(self, item, body)
            if command is not None:
                commands.append(command)
            continue

        if body.startswith(COMMAND_MARKER_V4 + "\n"):
            command = _v5_ack_legacy_v4_item(self, item, body)
            if command is not None:
                commands.append(command)
            continue

        if (
            self._mailbox_comment_id is not None
            and comment_id == int(self._mailbox_comment_id)
            and body.startswith(COMMAND_MARKER_V3 + "\n")
        ):
            command = self._parse_command_body(
                body,
                COMMAND_MARKER_V3,
                ref={"comment_id": comment_id, "mode": "issue_mailbox_v3"},
            )
            if command is not None:
                commands.append(command)
            continue

        command = self._parse_command_body(
            body,
            COMMAND_MARKER,
            ref={"comment_id": comment_id, "mode": "issue_comment_v2"},
        )
        if command is not None:
            commands.append(command)
    return commands


def _v5_fetch_channel_commands(self) -> list[CommandEnvelope] | None:
    used_cache = self._cached_comment_items is not None
    if used_cache:
        items = self._cached_comment_items
        self._cached_comment_items = None
    else:
        response = self._request_comment_page(conditional=True)
        self._observe_response(response, poll=True)
        if response.status_code == 304:
            return []
        if response.status_code in (401, 403, 404, 410):
            self._message_mode = "contents"
            self._message_mode_detail = (
                f"Dynamic channel discovery unavailable ({response.status_code}); Contents fallback active"
            )
            return None
        if response.status_code >= 400:
            raise RuntimeError(self._response_detail(response))
        self._comments_etag = response.headers.get("ETag") or self._comments_etag
        payload = response.json()
        items = payload if isinstance(payload, list) else []

    commands = _v5_comments_to_commands(self, items)
    if used_cache and not commands and self._mailbox_comment_id is not None:
        legacy = _p0_fetch_mailbox_commands(self)
        if legacy is not None:
            return legacy
    return commands


def _v5_fetch_commands(self) -> list[CommandEnvelope]:
    if self._message_mode == "auto":
        self.initialize_message_mode()
    if self._message_mode == "issue_channel_v5":
        commands = _v5_fetch_channel_commands(self)
        if commands is not None:
            return commands
        return self._fetch_contents_commands()
    return _v5_fetch_commands_base(self)


def _v5_command_id_from_payload(payload: dict) -> str:
    direct = str(payload.get("command_id") or "").strip()
    if direct:
        return direct
    command = payload.get("command")
    if isinstance(command, dict):
        return str(command.get("command_id") or "").strip()
    return ""


def _v5_result_owner_state(self, ref: dict, command_id: str) -> str:
    response = self.client.get(
        self._comment_url(int(ref["comment_id"])),
        headers=self.headers,
    )
    self._observe_response(response)
    if response.status_code >= 400:
        return "changed"
    body = str((response.json() or {}).get("body") or "")

    if ref.get("mode") == "issue_channel_v5":
        decoded = _v5_decode_envelope(
            body,
            (CHANNEL_COMMAND_MARKER_V5, CHANNEL_ACK_MARKER_V5, CHANNEL_RESULT_MARKER_V5),
        )
        if decoded is None:
            return "changed"
        marker, payload = decoded
        if str(payload.get("bridge_id") or "") != self.config.bridge_id:
            return "changed"
        if str(payload.get("channel_id") or "") != str(ref.get("channel_id") or ""):
            return "changed"
        if str(payload.get("generation") or "") != str(ref.get("generation") or ""):
            return "changed"
        if _v5_command_id_from_payload(payload) != command_id:
            return "changed"
        return "already_result" if marker == CHANNEL_RESULT_MARKER_V5 else "owned"

    if ref.get("mode") == "issue_mailbox_v4":
        decoded = _v5_decode_envelope(
            body,
            (COMMAND_MARKER_V4, ACK_MARKER_V4, RESULT_MARKER_V4),
        )
        if decoded is None:
            return "changed"
        marker, payload = decoded
        if str(payload.get("bridge_id") or "") != self.config.bridge_id:
            return "changed"
        if str(payload.get("generation") or "") != str(ref.get("generation") or ""):
            return "changed"
        if _v5_command_id_from_payload(payload) != command_id:
            return "changed"
        return "already_result" if marker == RESULT_MARKER_V4 else "owned"

    return "changed"


def _v5_safe_component(value: str, fallback: str) -> str:
    raw = str(value or "").strip()
    cooked = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in raw)
    return cooked[:128] or fallback


def _v5_publish_durable_result(self, ref: dict, result: ExecutionResult, reason: str) -> None:
    channel = (
        _v5_safe_component(str(ref.get("channel_id") or ""), "unknown")
        if ref.get("mode") == "issue_channel_v5"
        else "legacy-v4"
    )
    command_component = _v5_safe_component(result.command_id, "command")
    envelope = {
        "bridge_id": self.config.bridge_id,
        "channel_id": ref.get("channel_id"),
        "generation": ref.get("generation"),
        "command_id": result.command_id,
        "command": ref.get("command"),
        "result": json.loads(result.model_dump_json()),
        "fallback_reason": reason,
    }
    self._publish_json(
        self._bus_path(
            f"results/{self.config.bridge_id}/channels/{channel}/{command_component}.json"
        ),
        envelope,
        message=f"bridge channel result {result.command_id}",
        create_first=True,
    )


def _v5_publish_result(self, result: ExecutionResult) -> None:
    ref = self._comment_refs.get(result.command_id)
    if ref is None or ref.get("mode") not in {"issue_channel_v5", "issue_mailbox_v4"}:
        return _v5_publish_result_base(self, result)

    owner_state = _v5_result_owner_state(self, ref, result.command_id)
    if owner_state == "already_result":
        self._comment_write_ok = True
        return
    if owner_state != "owned":
        _v5_publish_durable_result(self, ref, result, "channel_generation_changed")
        return

    if ref.get("mode") == "issue_channel_v5":
        marker = CHANNEL_RESULT_MARKER_V5
        envelope = {
            "bridge_id": self.config.bridge_id,
            "channel_id": ref["channel_id"],
            "generation": ref["generation"],
            "command_id": result.command_id,
            "command": ref["command"],
            "result": json.loads(result.model_dump_json()),
        }
    else:
        marker = RESULT_MARKER_V4
        envelope = {
            "bridge_id": self.config.bridge_id,
            "generation": ref["generation"],
            "command_id": result.command_id,
            "command": ref["command"],
            "result": json.loads(result.model_dump_json()),
        }

    body = marker + "\n" + json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    response = self.client.patch(
        self._comment_url(int(ref["comment_id"])),
        headers=self.headers,
        json={"body": body},
    )
    self._observe_response(response)
    if response.status_code < 400:
        self._comment_write_ok = True
        self._comments_etag = None
        if ref.get("mode") == "issue_mailbox_v4":
            self._mailbox_etag = response.headers.get("ETag") or None
        return

    self._comment_write_ok = False
    _v5_publish_durable_result(
        self,
        ref,
        result,
        f"comment_patch_http_{response.status_code}",
    )


GitHubBusTransport._find_mailbox = _v5_find_mailbox
GitHubBusTransport.initialize_message_mode = _v5_initialize_message_mode
GitHubBusTransport.message_state = _v5_message_state
GitHubBusTransport.fetch_commands = _v5_fetch_commands
GitHubBusTransport.publish_result = _v5_publish_result
