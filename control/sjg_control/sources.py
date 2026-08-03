"""Complete, fixed-scope GitHub event source adapters for the host supervisor."""
from __future__ import annotations

from typing import Any
import json
import re
import subprocess

from .delivery import Page, SOURCE_IDS
from .host_config import GitHubHostConfig, read_github_token
from .policy import OrganizationPolicy


class SourceError(RuntimeError):
    pass


class GitHubCLI:
    """A supervisor-owned GitHub CLI transport with no caller-defined endpoints."""

    def __init__(self, config: GitHubHostConfig):
        self.config = config
        self.environment = {
            "GH_TOKEN": read_github_token(config), "HOME": config.home,
            "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
            "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
        }

    def api(self, endpoint: str, *, fields: dict[str, str] | None = None) -> Any:
        if (endpoint.startswith(("/", "http://", "https://")) or ".." in endpoint
                or not re.fullmatch(r"[A-Za-z0-9_./?=&:%+-]+", endpoint)):
            raise SourceError("GitHub source endpoint is outside the fixed API namespace")
        command = [self.config.executable, "api", "-X", "GET", endpoint]
        for key, value in sorted((fields or {}).items()):
            if not re.fullmatch(r"[a-z_]+", key) or "\x00" in value:
                raise SourceError("GitHub source query is invalid")
            command.extend(["-f", f"{key}={value}"])
        try:
            completed = subprocess.run(
                command, text=True, capture_output=True, timeout=60,
                env=self.environment, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SourceError("GitHub source request failed before completion") from exc
        if completed.returncode:
            raise SourceError(completed.stderr.strip() or "GitHub source request failed")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise SourceError("GitHub source returned malformed JSON") from exc


class GitHubSource:
    """One independently checkpointed GitHub source from the 1.0 five-source contract."""

    def __init__(self, source_id: str, policy: OrganizationPolicy, transport: GitHubCLI):
        if source_id not in SOURCE_IDS:
            raise SourceError(f"unknown source id: {source_id}")
        self.source_id = source_id
        self.policy = policy
        self.transport = transport
        alias = "org" if source_id.startswith("org-") else "product"
        self.repository = policy.repository(alias).slug

    def _parent(self, item: dict[str, Any]) -> dict[str, Any]:
        issue_url = str(item.get("issue_url") or "")
        match = re.fullmatch(
            rf"https://api\.github\.com/repos/{re.escape(self.repository)}/issues/([1-9][0-9]*)",
            issue_url,
        )
        if match is None:
            raise SourceError("comment parent URL does not match its commissioned repository")
        parent = self.transport.api(f"repos/{self.repository}/issues/{match.group(1)}")
        if not isinstance(parent, dict):
            raise SourceError("comment parent enrichment is incomplete")
        return parent

    def _pull_request(self, item: dict[str, Any]) -> dict[str, Any] | None:
        pull = item.get("pull_request")
        if not isinstance(pull, dict):
            return None
        url = str(pull.get("url") or "")
        match = re.fullmatch(
            rf"https://api\.github\.com/repos/{re.escape(self.repository)}/pulls/([1-9][0-9]*)",
            url,
        )
        if match is None:
            raise SourceError("pull-request URL does not match its commissioned repository")
        value = self.transport.api(f"repos/{self.repository}/pulls/{match.group(1)}")
        if not isinstance(value, dict):
            raise SourceError("pull-request enrichment is incomplete")
        return value

    def _workflow_path(self, item: dict[str, Any]) -> str:
        existing = item.get("path")
        if isinstance(existing, str) and existing:
            return existing
        workflow_id = item.get("workflow_id")
        if not isinstance(workflow_id, int) or workflow_id <= 0:
            raise SourceError("workflow run has no fixed workflow identity")
        workflow = self.transport.api(
            f"repos/{self.repository}/actions/workflows/{workflow_id}")
        path = workflow.get("path") if isinstance(workflow, dict) else None
        if not isinstance(path, str) or not path:
            raise SourceError("workflow path enrichment is incomplete")
        return path

    def fetch_page(self, *, watermark: str, page_cursor: str | None,
                   etag: str | None) -> Page:
        del etag  # gh CLI does not expose a stable conditional-page contract here.
        if page_cursor is None:
            page_number = 1
        elif re.fullmatch(r"[1-9][0-9]{0,8}", page_cursor):
            page_number = int(page_cursor)
        else:
            raise SourceError("GitHub source cursor is invalid")
        fields = {"per_page": "100", "page": str(page_number)}
        if self.source_id.endswith("issues"):
            fields.update({"state": "all", "since": watermark, "sort": "updated",
                           "direction": "asc"})
            raw = self.transport.api(f"repos/{self.repository}/issues", fields=fields)
            if not isinstance(raw, list) or not all(isinstance(value, dict) for value in raw):
                raise SourceError("GitHub issue page is not a complete object list")
            items = []
            for value in raw:
                item = dict(value)
                pull = self._pull_request(item)
                if pull is not None:
                    item["_sjg_pull_request"] = pull
                items.append(item)
        elif self.source_id.endswith("comments"):
            fields.update({"since": watermark, "sort": "updated", "direction": "asc"})
            raw = self.transport.api(
                f"repos/{self.repository}/issues/comments", fields=fields)
            if not isinstance(raw, list) or not all(isinstance(value, dict) for value in raw):
                raise SourceError("GitHub comment page is not a complete object list")
            items = []
            for value in raw:
                item = dict(value)
                item["_sjg_parent"] = self._parent(item)
                items.append(item)
        else:
            raw_result = self.transport.api(
                f"repos/{self.repository}/actions/runs", fields=fields)
            raw = raw_result.get("workflow_runs") if isinstance(raw_result, dict) else None
            if not isinstance(raw, list) or not all(isinstance(value, dict) for value in raw):
                raise SourceError("GitHub workflow-run page is not a complete object list")
            items = []
            for value in raw:
                updated = str(value.get("updated_at") or value.get("created_at") or "")
                if updated >= watermark:
                    item = dict(value)
                    item["_sjg_workflow_path"] = self._workflow_path(item)
                    items.append(item)
        next_cursor = str(page_number + 1) if len(raw) == 100 else None
        return Page(items, next_cursor)
