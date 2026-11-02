"""Narrow GitHub implementation used only by the installed controller.

No caller-controlled URL, HTTP method, CLI option, repository slug, branch role, or
credential reaches the subprocess boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import base64
import hashlib
import json
import re
import subprocess

from .authz import Facts, Session
from .policy import OrganizationPolicy


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    owner: str
    number: int


class GitHubBackend:
    def __init__(self, policy: OrganizationPolicy, *, executable: str,
                 environment: dict[str, str] | None = None,
                 project: ProjectConfig | None = None,
                 demo_receipt_lookup: Callable[[str], dict[str, Any] | None] | None = None):
        self.policy = policy
        binary = Path(executable)
        if not binary.is_absolute() or not binary.is_file():
            raise GitHubError("GitHub executable must be an existing absolute file")
        self.executable = executable
        supplied = dict(environment or {})
        allowed = {"GH_TOKEN", "HOME", "LANG", "LC_ALL"}
        unknown = set(supplied) - allowed
        if unknown:
            raise GitHubError("GitHub environment contains forbidden overrides: "
                              + ", ".join(sorted(unknown)))
        if not supplied.get("GH_TOKEN"):
            raise GitHubError("controller GitHub token is absent")
        home = Path(supplied.get("HOME") or "/var/empty")
        if not home.is_absolute():
            raise GitHubError("controller GitHub home must be absolute")
        self.environment = {key: value for key, value in supplied.items()
                            if key in {"GH_TOKEN", "LANG", "LC_ALL"}}
        self.environment.update({"HOME": str(home), "PATH": "/usr/bin:/bin",
                                 "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
        self.project = project
        self.demo_receipt_lookup = demo_receipt_lookup

    def _run(self, argv: list[str], *, stdin: str | None = None) -> str:
        process = subprocess.run([self.executable, *argv], input=stdin, text=True,
                                 capture_output=True, timeout=60, env=self.environment)
        if process.returncode:
            raise GitHubError(process.stderr.strip() or f"GitHub command failed: {argv[0]}")
        return process.stdout

    def _api(self, path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
        argv = ["api", "-X", method, path]
        stdin = None
        if body is not None:
            argv.extend(["--input", "-"])
            stdin = json.dumps(body, sort_keys=True)
        raw = self._run(argv, stdin=stdin)
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GitHubError("GitHub returned malformed JSON") from exc

    def _pages(self, path: str) -> list[dict[str, Any]]:
        raw = self._run(["api", path, "--paginate", "--slurp"])
        try:
            pages = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GitHubError("paginated GitHub read returned malformed JSON") from exc
        if not isinstance(pages, list):
            raise GitHubError("paginated GitHub read was not a page list")
        flattened: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, list):
                raise GitHubError("paginated GitHub page was not a list")
            if not all(isinstance(value, dict) for value in page):
                raise GitHubError("paginated GitHub page contained a non-object")
            flattened.extend(page)
        return flattened

    def _repo(self, arguments: dict[str, Any]):
        return self.policy.repository(str(arguments.get("repository") or "org"))

    def _issue(self, arguments: dict[str, Any]) -> dict[str, Any]:
        number = int(arguments.get("id") or arguments.get("issue") or 0)
        if number <= 0:
            raise GitHubError("positive issue id is required")
        issue = self._api(f"repos/{self._repo(arguments).slug}/issues/{number}")
        if not isinstance(issue, dict) or issue.get("pull_request"):
            raise GitHubError("target issue is absent or is a pull request")
        return issue

    @staticmethod
    def _labels(issue: dict[str, Any]) -> set[str]:
        return {str(value.get("name") or "") for value in issue.get("labels") or []
                if isinstance(value, dict)}

    @staticmethod
    def _kind(labels: set[str]) -> str | None:
        kinds = labels & {"objective", "proposal", "epic", "feature", "story"}
        if len(kinds) > 1:
            raise GitHubError("issue has conflicting work-item kind labels")
        return next(iter(kinds), None)

    @staticmethod
    def _metadata(issue: dict[str, Any]) -> dict[str, Any] | None:
        match = re.search(r"(?m)^<!-- sjg-item:(\{[^\n]+\}) -->$", str(issue.get("body") or ""))
        if not match:
            return None
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) and value.get("schema") == "sjg.item/v1" else None

    @staticmethod
    def _body_with_metadata(body: str, *, owner_role: str, kind: str) -> str:
        stripped = re.sub(r"(?m)^<!-- sjg-item:\{[^\n]+\} -->\n?", "", body).lstrip()
        metadata = json.dumps({"schema": "sjg.item/v1", "owner_role": owner_role,
                               "kind": kind}, sort_keys=True, separators=(",", ":"))
        return f"<!-- sjg-item:{metadata} -->\n{stripped or '—'}"

    @staticmethod
    def _idempotent_body(body: str, key: str) -> str:
        marker = f"<!-- sjg-idempotency:{key} -->"
        return body if marker in body else f"{marker}\n{body}"

    def _children(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        issue = self._issue(arguments)
        return self._pages(
            f"repos/{self._repo(arguments).slug}/issues/{int(issue['number'])}/sub_issues?per_page=100")

    def _stage(self, arguments: dict[str, Any]) -> str | None:
        if self.project is None:
            return None
        project_id, _field_id, _options = self._project_metadata()
        repo = self._repo(arguments)
        number = int(arguments.get("id") or arguments.get("issue") or 0)
        owner, name = repo.slug.split("/", 1)
        query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
          repository(owner:$owner,name:$name){issue(number:$number){projectItems(first:100,after:$cursor){
            pageInfo{hasNextPage endCursor} nodes{project{id} fieldValueByName(name:\"Status\"){
              ... on ProjectV2ItemFieldSingleSelectValue{name}}}}}}}}"""
        cursor = None
        while True:
            body = {"query": query, "variables": {"owner": owner, "name": name,
                                                    "number": number, "cursor": cursor}}
            data = self._api("graphql", method="POST", body=body)
            if (data or {}).get("errors"):
                raise GitHubError("project status GraphQL read failed")
            connection = (((data or {}).get("data") or {}).get("repository") or {}).get("issue")
            connection = (connection or {}).get("projectItems") or {}
            for node in connection.get("nodes") or []:
                if ((node.get("project") or {}).get("id") == project_id):
                    return ((node.get("fieldValueByName") or {}).get("name"))
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return None
            cursor = page.get("endCursor")

    def _project_metadata(self) -> tuple[str, str, dict[str, str]]:
        if self.project is None:
            raise GitHubError("Project coordinates are not commissioned")
        query = """query($login:String!,$number:Int!){
          user(login:$login){projectV2(number:$number){id field(name:\"Status\"){
            ... on ProjectV2SingleSelectField{id options{id name}}}}}
          organization(login:$login){projectV2(number:$number){id field(name:\"Status\"){
            ... on ProjectV2SingleSelectField{id options{id name}}}}}}"""
        data = self._api("graphql", method="POST", body={
            "query": query, "variables": {"login": self.project.owner, "number": self.project.number}})
        if (data or {}).get("errors"):
            raise GitHubError("Project coordinate GraphQL read failed")
        roots = (data or {}).get("data") or {}
        project = ((roots.get("user") or {}).get("projectV2")
                   or (roots.get("organization") or {}).get("projectV2"))
        field = (project or {}).get("field") or {}
        if not project or not field.get("id"):
            raise GitHubError("configured Project or Status field was not found")
        return project["id"], field["id"], {
            str(value["name"]): str(value["id"]) for value in field.get("options") or []}

    def _project_item_id(self, arguments: dict[str, Any], project_id: str) -> str | None:
        repo = self._repo(arguments)
        number = int(arguments.get("id") or arguments.get("issue") or 0)
        owner, name = repo.slug.split("/", 1)
        query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
          repository(owner:$owner,name:$name){issue(number:$number){projectItems(first:100,after:$cursor){
            pageInfo{hasNextPage endCursor} nodes{id project{id}}}}}}"""
        cursor = None
        while True:
            data = self._api("graphql", method="POST", body={"query": query, "variables": {
                "owner": owner, "name": name, "number": number, "cursor": cursor}})
            if (data or {}).get("errors"):
                raise GitHubError("Project item GraphQL read failed")
            connection = (((data or {}).get("data") or {}).get("repository") or {}).get("issue")
            connection = (connection or {}).get("projectItems") or {}
            for node in connection.get("nodes") or []:
                if ((node.get("project") or {}).get("id")) == project_id:
                    return str(node["id"])
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return None
            cursor = page.get("endCursor")

    def _move(self, arguments: dict[str, Any], stage: str) -> None:
        project_id, field_id, options = self._project_metadata()
        if stage not in options:
            raise GitHubError(f"unknown configured Project stage: {stage}")
        item_id = self._project_item_id(arguments, project_id)
        if not item_id:
            issue = self._issue(arguments)
            mutation = """mutation($project:ID!,$content:ID!){
              addProjectV2ItemById(input:{projectId:$project,contentId:$content}){item{id}}}"""
            data = self._api("graphql", method="POST", body={"query": mutation, "variables": {
                "project": project_id, "content": issue["node_id"]}})
            if (data or {}).get("errors"):
                raise GitHubError("adding issue to Project failed")
            item_id = (((data or {}).get("data") or {}).get("addProjectV2ItemById") or {}).get("item", {}).get("id")
        mutation = """mutation($project:ID!,$item:ID!,$field:ID!,$option:String!){
          updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field,
            value:{singleSelectOptionId:$option}}){projectV2Item{id}}}"""
        data = self._api("graphql", method="POST", body={"query": mutation, "variables": {
            "project": project_id, "item": item_id, "field": field_id, "option": options[stage]}})
        if (data or {}).get("errors"):
            raise GitHubError("Project move failed")

    @staticmethod
    def _acceptance_lines(body: str) -> set[str]:
        values: set[str] = set()
        lines = body.splitlines()
        collecting = False
        for line in lines:
            match = re.match(r"(?i)^\s*(?:[-*]\s*)?(?:acceptance|done[- ]?when)\s*:\s*(.+?)\s*$", line)
            if match:
                values.add(" ".join(match.group(1).split()).casefold())
                collecting = False
                continue
            if re.match(r"(?i)^\s*#{1,6}\s*(?:acceptance(?: criteria)?(?: \(optional\))?|done[- ]?when)\s*$", line):
                collecting = True
                continue
            if collecting and re.match(r"^\s*#{1,6}\s+", line):
                collecting = False
            if collecting:
                value = re.sub(r"^\s*[-*]\s*", "", line).strip()
                if value and value != "_No response_":
                    values.add(" ".join(value.split()).casefold())
        return values

    @classmethod
    def _acceptance_digest(cls, issue: dict[str, Any]) -> str | None:
        lines = cls._acceptance_lines(str(issue.get("body") or ""))
        return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest() if lines else None

    @staticmethod
    def _evidence_fields(body: str) -> dict[str, str] | None:
        fields: dict[str, str] = {}
        for line in body.splitlines():
            match = re.fullmatch(
                r"\s*(accepted|item|acceptance_digest|pr|head_sha|evidence_run)\s*:\s*(.*?)\s*", line)
            if match:
                if match[1] in fields:
                    return None
                fields[match[1]] = match[2]
        return fields

    def _product_demo(self, fields: dict[str, str]) -> bool:
        repo = self.policy.repository("product").slug
        pr_match = re.fullmatch(re.escape(repo) + r"#([1-9][0-9]*)", fields.get("pr", ""))
        run_match = re.fullmatch(
            r"https://github\.com/" + re.escape(repo) + r"/actions/runs/([1-9][0-9]*)",
            fields.get("evidence_run", ""))
        sha = fields.get("head_sha", "")
        if not pr_match or not run_match or not re.fullmatch(r"[0-9a-f]{40}", sha):
            return False
        pr = self._api(f"repos/{repo}/pulls/{pr_match[1]}")
        if not isinstance(pr, dict) or (pr.get("head") or {}).get("sha") != sha:
            return False
        run = self._api(f"repos/{repo}/actions/runs/{run_match[1]}")
        if not isinstance(run, dict) or not (
                run.get("status") == "completed" and run.get("conclusion") == "success"
                and run.get("event") == "workflow_dispatch"
                and run.get("path") == ".github/workflows/demo-evidence.yml"
                and run.get("head_branch") == self.policy.repository("product").default_branch
                and (run.get("repository") or {}).get("full_name") == repo
                and all((run.get(key) or {}).get("login", "").casefold()
                        == self.policy.principal_github.casefold()
                        for key in ("actor", "triggering_actor"))):
            return False
        # The workflow dispatches on main and checks out the requested PR head. Its
        # run.head_sha therefore identifies main; the protected upload step binds
        # the successful evidence artifact to the PR number and requested head.
        artifacts = self._api(f"repos/{repo}/actions/runs/{run_match[1]}/artifacts?per_page=100")
        if not isinstance(artifacts, dict) or not isinstance(artifacts.get("artifacts"), list):
            return False
        items = artifacts["artifacts"]
        total = artifacts.get("total_count")
        if (type(total) is not int or total != len(items)
                or not all(isinstance(item, dict) for item in items)):
            return False  # incomplete enumeration is UNKNOWN, never positive evidence
        expected = f"demo-evidence-{pr_match[1]}-{sha}"
        matches = [item for item in items if item.get("name") == expected]
        return len(matches) == 1 and matches[0].get("expired") is False

    def _evidence(self, issue: dict[str, Any], arguments: dict[str, Any]) -> bool:
        issue_number = int(issue["number"])
        issue_repo = self.policy.repository("org").slug
        digest = self._acceptance_digest(issue)
        if digest is None:
            return False
        pr_ref = str(arguments.get("pr") or "")
        if pr_ref:
            match = re.fullmatch(r"(?:https://github\.com/)?([^/#]+/[^/#]+)(?:/pull/|#)([1-9][0-9]*)", pr_ref)
            if not match or match[1] != self.policy.repository("product").slug:
                return False
            pr = self._api(f"repos/{match[1]}/pulls/{match[2]}")
            if not isinstance(pr, dict) or pr.get("merged") is not True:
                return False
            body = str(pr.get("body") or "")
            relation = re.compile(
                rf"(?im)^\s*Relates-to\s+(?:{re.escape(issue_repo)}#{issue_number}|"
                rf"https://github\.com/{re.escape(issue_repo)}/issues/{issue_number})\s*$")
            fields = self._evidence_fields(body)
            # This path proves a related merge against current acceptance, not a
            # successful product demonstration. Product features must use --demo.
            return (self._kind(self._labels(issue)) == "story" and fields is not None
                    and fields.get("acceptance_digest") == digest
                    and relation.search(body) is not None)
        demo = str(arguments.get("demo") or "")
        match = re.fullmatch(
            r"https://github\.com/([^/]+/[^/]+)/issues/([1-9][0-9]*)#issuecomment-([1-9][0-9]*)", demo)
        if not match or match[1] != issue_repo or int(match[2]) != issue_number:
            return False
        comment = self._api(f"repos/{issue_repo}/issues/comments/{match[3]}")
        if not isinstance(comment, dict):
            return False
        body = str(comment.get("body") or "")
        marker = re.match(
            r"^<!-- sjg-idempotency:([A-Za-z0-9][A-Za-z0-9._:-]{15,127}) -->\n"
            r"\*\*Commercial Planner —\*\* ", body)
        if marker is None or self.demo_receipt_lookup is None:
            return False
        receipt = self.demo_receipt_lookup(marker[1])
        if not receipt:
            return False
        args, result = receipt["arguments"], receipt["result"]
        expected_body = self._idempotent_body(
            f"**Commercial Planner —** {args.get('body', '—')}", marker[1])
        author = result.get("comment_author_id")
        if not (args.get("repository", "org") == "org"
                and int(args.get("id") or args.get("issue") or 0) == issue_number
                and result.get("comment_id") == int(match[3])
                and comment.get("id") == int(match[3])
                and comment.get("issue_url") == f"https://api.github.com/repos/{issue_repo}/issues/{issue_number}"
                and type(author) is int and author > 0
                and (comment.get("user") or {}).get("id") == author
                and body == expected_body):
            return False
        fields = self._evidence_fields(body)
        if not (fields is not None and fields.get("accepted") == "true"
                and fields.get("item") == f"{issue_repo}#{issue_number}"
                and fields.get("acceptance_digest") == digest
                and re.search(r"(?m)^\s*\[DEMO\]\s*$", body)):
            return False
        product_fields = {"pr", "head_sha", "evidence_run"}
        if self._kind(self._labels(issue)) == "feature" or product_fields & fields.keys():
            return self._product_demo(fields)
        return True

    def facts(self, operation: str, arguments: dict[str, Any]) -> Facts:
        if operation in {"repo.submit", "repo.status", "worker.run"}:
            return Facts()
        if operation == "pm.issue.create":
            parent_kind = None
            if arguments.get("parent"):
                parent = self._issue({**arguments, "id": arguments["parent"]})
                parent_kind = self._kind(self._labels(parent))
            return Facts(complete=True, parent_kind=parent_kind)
        if operation == "read.issue.list":
            return Facts()
        if operation.startswith("pm.") or operation.startswith("read.issue"):
            issue = self._issue(arguments)
            labels = self._labels(issue)
            metadata = self._metadata(issue) or {}
            kind = self._kind(labels)
            children = self._children(arguments) if operation in {
                "pm.issue.done", "pm.issue.drop", "read.issue.children"} else []
            return Facts(complete=True, issue_kind=kind,
                         owner_role=metadata.get("owner_role"),
                         current_stage=self._stage(arguments) if operation == "pm.issue.move" else None,
                         has_open_children=any(str(value.get("state")) != "closed" for value in children),
                         evidence_valid=self._evidence(issue, arguments) if operation == "pm.issue.done" else None)
        if operation in {"pr.create", "read.pr.list", "read.workflow.runs"}:
            return Facts()
        if operation.startswith("pr.") or operation.startswith("read.pr"):
            number = int(arguments.get("id") or arguments.get("pr") or 0)
            pr = self._api(f"repos/{self._repo(arguments).slug}/pulls/{number}")
            if not isinstance(pr, dict):
                raise GitHubError("pull request is absent")
            head = str(((pr.get("head") or {}).get("ref") or ""))
            match = re.fullmatch(r"agent/([^/]+)/[^/]+", head)
            return Facts(complete=True, pr_head_role=match.group(1) if match else None,
                         pr_open=pr.get("state") == "open")
        raise GitHubError(f"unsupported facts operation: {operation}")

    def _comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        return self._api(f"repos/{repo}/issues/{number}/comments", method="POST", body={"body": body})

    def _submit(self, arguments: dict[str, Any], role: str, *, idempotency_key: str) -> dict[str, Any]:
        repo = self._repo(arguments)
        topic = str(arguments["topic"])
        branch = f"agent/{role}/{topic}"
        base_oid = str(arguments["base_oid"])
        matches = self._pages(
            f"repos/{repo.slug}/git/matching-refs/heads/{branch}?per_page=100")
        exact = [value for value in matches if value.get("ref") == f"refs/heads/{branch}"]
        if len(exact) > 1:
            raise GitHubError("GitHub returned duplicate exact branch references")
        existing = exact[0] if exact else None
        if existing:
            remote_base = str(((existing.get("object") or {}).get("sha") or ""))
        else:
            default_ref = self._api(f"repos/{repo.slug}/git/ref/heads/{repo.default_branch}")
            remote_base = str(((default_ref or {}).get("object") or {}).get("sha") or "")
        if not remote_base or remote_base != base_oid:
            raise GitHubError("submission base is stale; rematerialize before retrying")
        commit = self._api(f"repos/{repo.slug}/git/commits/{base_oid}")
        base_tree = str(((commit or {}).get("tree") or {}).get("sha") or "")
        entries = []
        for change in arguments["changes"]:
            if change["operation"] == "delete":
                entries.append({"path": change["path"], "mode": "100644", "type": "blob", "sha": None})
                continue
            data = base64.b64decode(change["content_base64"], validate=True)
            if hashlib.sha256(data).hexdigest() != change["sha256"]:
                raise GitHubError(f"content digest mismatch: {change['path']}")
            blob = self._api(f"repos/{repo.slug}/git/blobs", method="POST",
                             body={"content": change["content_base64"], "encoding": "base64"})
            entries.append({"path": change["path"], "mode": change["mode"], "type": "blob",
                            "sha": blob["sha"]})
        tree = self._api(f"repos/{repo.slug}/git/trees", method="POST",
                         body={"base_tree": base_tree, "tree": entries})
        created = self._api(f"repos/{repo.slug}/git/commits", method="POST", body={
            "message": (str(arguments.get("message") or f"Agent submission: {topic}")
                        + f"\n\nSJG-Idempotency: {idempotency_key}"),
            "tree": tree["sha"], "parents": [base_oid]})
        if existing:
            self._api(f"repos/{repo.slug}/git/refs/heads/{branch}", method="PATCH",
                      body={"sha": created["sha"], "force": False})
        else:
            self._api(f"repos/{repo.slug}/git/refs", method="POST",
                      body={"ref": f"refs/heads/{branch}", "sha": created["sha"]})
        return {"repository": repo.slug, "branch": branch, "sha": created["sha"]}

    def execute(self, operation: str, arguments: dict[str, Any], *, session: Session,
                idempotency_key: str) -> dict[str, Any]:
        role = session.role
        repo = self._repo(arguments)
        if operation == "read.issue.get":
            return self._issue(arguments)
        if operation == "read.issue.children":
            return {"items": self._children(arguments), "complete": True}
        if operation == "read.issue.comments":
            issue = self._issue(arguments)
            return {"items": self._pages(
                f"repos/{repo.slug}/issues/{int(issue['number'])}/comments?per_page=100"),
                    "complete": True}
        if operation == "read.issue.list":
            state = "all" if arguments.get("all") else "open"
            label = f"role:{role}"
            return {"items": self._pages(
                f"repos/{repo.slug}/issues?state={state}&labels={label}&per_page=100"), "complete": True}
        if operation == "read.pr.list":
            return {"items": self._pages(f"repos/{repo.slug}/pulls?state=all&per_page=100"),
                    "complete": True}
        if operation == "read.pr.get":
            number = int(arguments.get("id") or arguments.get("pr") or 0)
            return self._api(f"repos/{repo.slug}/pulls/{number}")
        if operation == "read.pr.files":
            number = int(arguments.get("id") or arguments.get("pr") or 0)
            return {"items": self._pages(f"repos/{repo.slug}/pulls/{number}/files?per_page=100"),
                    "complete": True}
        if operation == "read.pr.checks":
            number = int(arguments.get("id") or arguments.get("pr") or 0)
            pr = self._api(f"repos/{repo.slug}/pulls/{number}")
            return self._api(f"repos/{repo.slug}/commits/{pr['head']['sha']}/check-runs")
        if operation == "read.workflow.runs":
            return {"items": self._pages(f"repos/{repo.slug}/actions/runs?per_page=100"),
                    "complete": True}
        if operation == "pm.issue.create":
            kind = str(arguments["kind"])
            title = str(arguments.get("title") or "")
            prefix = f"[{kind.upper()}] "
            owner = str(arguments.get("target_role") or role)
            labels = [kind, f"role:{owner}"]
            for target in arguments.get("also_role") or []:
                if target in self.policy.roles and target != role:
                    labels.append(f"role:{target}")
            issue_body = self._idempotent_body(self._body_with_metadata(
                str(arguments.get("body") or "—"), owner_role=owner, kind=kind), idempotency_key)
            issue = self._api(f"repos/{repo.slug}/issues", method="POST", body={
                "title": title if title.startswith(prefix) else prefix + title,
                "body": issue_body, "labels": labels})
            if arguments.get("parent"):
                parent = self._issue({**arguments, "id": arguments["parent"]})
                self._api(f"repos/{repo.slug}/issues/{int(parent['number'])}/sub_issues",
                          method="POST", body={"sub_issue_id": issue["id"]})
            if self.project is not None:
                self._move({**arguments, "id": issue["number"]}, "Todo")
            return {"number": issue["number"], "url": issue["html_url"], "owner_role": owner}
        if operation == "pm.issue.comment":
            issue = self._issue(arguments)
            body = self._idempotent_body(
                f"**{role.replace('-', ' ').title()} —** {arguments['body']}", idempotency_key)
            result = self._comment(repo.slug, int(issue["number"]), body)
            return {"comment_id": result["id"],
                    "comment_author_id": (result.get("user") or {}).get("id")}
        if operation == "pm.issue.route":
            issue = self._issue(arguments)
            target = str(arguments["target_role"])
            labels = sorted({label for label in self._labels(issue) if not label.startswith("role:")}
                            | {f"role:{target}"})
            kind = str((self._metadata(issue) or {}).get("kind") or "proposal")
            self._api(f"repos/{repo.slug}/issues/{int(issue['number'])}", method="PATCH",
                      body={"labels": labels, "body": self._body_with_metadata(
                          str(issue.get("body") or ""), owner_role=target, kind=kind)})
            return {"owner_role": target}
        if operation == "pm.issue.move":
            issue = self._issue(arguments)
            self._move(arguments, str(arguments["to"]))
            return {"number": issue["number"], "stage": arguments["to"]}
        if operation == "pm.issue.done":
            issue = self._issue(arguments)
            marker = f"<!-- sjg-idempotency:{idempotency_key} -->"
            self._comment(repo.slug, int(issue["number"]),
                          f"{marker}\n[CLOSE]\n```json\n{json.dumps(arguments, sort_keys=True)}\n```")
            if self.project is not None:
                self._move(arguments, "Done")
            self._api(f"repos/{repo.slug}/issues/{int(issue['number'])}", method="PATCH",
                      body={"state": "closed", "state_reason": "completed"})
            return {"number": issue["number"], "state": "closed"}
        if operation == "pm.issue.drop":
            issue = self._issue(arguments)
            marker = f"<!-- sjg-idempotency:{idempotency_key} -->"
            self._comment(repo.slug, int(issue["number"]),
                          f"{marker}\n[DROP]\nreason: {arguments.get('reason', '')}")
            if self.project is not None:
                self._move(arguments, "Dropped")
            self._api(f"repos/{repo.slug}/issues/{int(issue['number'])}", method="PATCH",
                      body={"state": "closed", "state_reason": "not_planned"})
            return {"number": issue["number"], "state": "closed", "reason": "not_planned"}
        if operation == "pr.create":
            topic = str(arguments["topic"])
            branch = f"agent/{role}/{topic}"
            created = self._api(f"repos/{repo.slug}/pulls", method="POST", body={
                "title": str(arguments.get("title") or topic.replace("-", " ").title()),
                "body": self._idempotent_body(str(arguments.get("body") or ""), idempotency_key),
                "head": branch,
                "base": repo.default_branch, "draft": bool(arguments.get("draft", True))})
            return {"number": created["number"], "url": created["html_url"], "head": branch}
        if operation == "pr.update":
            number = int(arguments.get("id") or arguments.get("pr") or 0)
            body = {key: arguments[key] for key in ("title", "body") if key in arguments}
            updated = self._api(f"repos/{repo.slug}/pulls/{number}", method="PATCH", body=body)
            return {"number": updated["number"], "url": updated["html_url"]}
        if operation == "pr.comment":
            number = int(arguments.get("id") or arguments.get("pr") or 0)
            result = self._comment(repo.slug, number,
                                   self._idempotent_body(
                                       f"**{role.replace('-', ' ').title()} —** {arguments['body']}",
                                       idempotency_key))
            return {"comment_id": result["id"]}
        if operation == "pr.review":
            number = int(arguments.get("id") or arguments.get("pr") or 0)
            event = str(arguments.get("event") or "COMMENT")
            result = self._api(f"repos/{repo.slug}/pulls/{number}/reviews", method="POST",
                               body={"body": self._idempotent_body(
                                   str(arguments.get("body") or ""), idempotency_key),
                                   "event": event})
            return {"review_id": result["id"], "state": result.get("state")}
        if operation == "repo.status":
            return {"repository": repo.slug, "default_branch": repo.default_branch}
        if operation == "repo.submit":
            return self._submit(arguments, role, idempotency_key=idempotency_key)
        raise GitHubError(f"operation implementation is not commissioned: {operation}")
