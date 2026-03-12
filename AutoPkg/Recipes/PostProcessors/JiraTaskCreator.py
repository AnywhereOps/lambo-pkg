# Downloaded from https://github.com/autopkg/mscottblake-recipes/blob/f52cc854837ec404e0ea6d2fe61808233c27151f/SharedPostProcessors/JiraTaskCreator.py
# Commit: f52cc854837ec404e0ea6d2fe61808233c27151f
# Downloaded at: 2026-03-12 03:04:03 UTC

"""
AutoPkg Post-Processor: Create a Jira Task after a successful recipe run.

Environment variables should be provided as AUTOPKG_<NAME> so they are passed to
the processor's input variables. Required inputs:
    JIRA_SERVER            e.g. https://yourcompany.atlassian.net
    JIRA_PROJECT_KEY       e.g. ITSD
    JIRA_TOKEN             Jira API token for Bearer authentication
    JIRA_EPIC_LINK_FIELD   Custom field id used to link issues to an epic
    EPIC_KEY               Jira epic key to attach newly created issues to

Optional inputs:
    JIRA_TRANSITION_ID  Transition id to apply after issue creation (e.g., Done)

Outputs set on success:
    jira_issue_key      e.g. ITSD-1234
    jira_issue_url      Direct URL to the created issue
"""

import json
import ssl
import urllib.error
import urllib.request
from typing import Any

from autopkglib import Processor, ProcessorError

__all__ = ["JiraTaskCreator"]


class JiraTaskCreator(Processor):
    description = "Create a Jira issue via REST API after recipe success."
    input_variables: dict[str, dict[str, Any]] = {
        "JIRA_SERVER": {
            "required": True,
            "description": "Base Jira server URL (e.g. https://yourcompany.atlassian.net).",
        },
        "JIRA_TOKEN": {
            "required": True,
            "description": "Jira API token used for Bearer authentication.",
        },
        "JIRA_PROJECT_KEY": {
            "required": True,
            "description": "Jira project key for the new issue (e.g., ITSD).",
        },
        "JIRA_EPIC_LINK_FIELD": {
            "required": True,
            "description": "Custom field id to associate the issue with an epic (e.g., customfield_12345).",
        },
        "EPIC_KEY": {
            "required": True,
            "description": "Epic issue key to link against (e.g., ITSD-1000).",
        },
        "JIRA_TRANSITION_ID": {
            "required": False,
            "description": "Transition id to apply after issue creation (e.g., Done).",
        },
        "issue_type": {
            "required": False,
            "description": "Jira issue type name (e.g. Task, Story).",
            "default": "Task",
        },
        "summary": {
            "required": False,
            "description": "Issue summary. Defaults to '<NAME> <version> imported'.",
        },
        "description": {
            "required": False,
            "description": "Jira Issue description.",
            "default": "AutoPkg Runner completed. See CI logs and Munki repo for changes.",
        },
        "labels": {
            "required": False,
            "description": "Optional labels as comma-separated string or YAML list.",
            "default": "automated, AutoPkg, Munki",
        },
        "assignee": {
            "required": False,
            "description": "Optional assignee accountId (Jira Cloud).",
            "default": "",
        },
        "timeout_seconds": {
            "required": False,
            "description": "HTTP timeout in seconds (default 45).",
            "default": 45,
        },
        "require_munki_change": {
            "required": False,
            "description": "If true, skip creating an issue when MunkiImporter indicates no change.",
            "default": True,
        },
    }
    output_variables: dict[str, dict[str, str]] = {
        "jira_issue_key": {"description": "Created Jira issue key (e.g., ITSD-1234)."},
        "jira_issue_url": {"description": "Direct URL to the created Jira issue."},
    }

    def _parse_labels(self, raw: Any) -> list[str]:
        # Accept comma-separated string or a YAML list
        if raw is None:
            return []
        if isinstance(raw, str):
            s = raw.strip()
            return [part.strip() for part in s.split(",") if part.strip()] if s else []
        if isinstance(raw, (list, tuple)):
            return [str(x).strip() for x in raw if str(x).strip()]
        return []

    def _bool(self, val: Any, default: bool = False) -> bool:
        if isinstance(val, bool):
            return val
        if val is None:
            return default
        s = str(val).strip().lower()
        if s in {"1", "true", "yes", "on"}:
            return True
        if s in {"0", "false", "no", "off"}:
            return False
        return default

    def _should_skip(self) -> bool:
        require_change = self._bool(self.env.get("require_munki_change"), True)
        repo_changed = self._bool(self.env.get("munki_repo_changed"), True)
        return require_change and not repo_changed

    def _issue_fields(self) -> dict[str, Any]:
        name = (
            self._strip(self.env.get("NAME"))
            or self._strip(self.env.get("display_name"))
            or "Package"
        )
        version = self._strip(self.env.get("version")) or self._strip(
            self.env.get("VERSION")
        )

        summary = self._strip(self.env.get("summary")) or self._default_summary(
            name, version
        )
        description = self._strip(self.env.get("description")) or (
            "AutoPkg Runner completed. See CI logs and Munki repo for changes."
        )
        issue_type = self._strip(self.env.get("issue_type")) or "Task"

        fields: dict[str, Any] = {
            "project": {"key": self._strip(self.env["JIRA_PROJECT_KEY"])},
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type},
        }

        epic_field = self._strip(self.env["JIRA_EPIC_LINK_FIELD"])
        epic_key = self._strip(self.env["EPIC_KEY"])
        if epic_field and epic_key:
            fields[epic_field] = epic_key

        labels = self._parse_labels(self.env.get("labels"))
        if labels:
            fields["labels"] = labels

        assignee = self._strip(self.env.get("assignee"))
        if assignee:
            fields["assignee"] = {"accountId": assignee}

        return fields

    def _encode_payload(self, fields: dict[str, Any]) -> bytes:
        return json.dumps({"fields": fields}).encode("utf-8")

    def _post_issue(self, server: str, payload: bytes) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{server}/rest/api/2/issue", data=payload, method="POST"
        )
        request.add_header("Authorization", f"Bearer {self._token()}")
        request.add_header("Content-Type", "application/json")

        timeout = self._timeout_seconds()

        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=ssl._create_unverified_context(),
            ) as response:
                if response.status not in (200, 201):
                    raise ProcessorError(f"Jira returned HTTP {response.status}")

                try:
                    return json.loads(response.read().decode("utf-8"))
                except Exception:
                    return {}
        except urllib.error.HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="ignore")
            except Exception:
                detail = str(error)
            raise ProcessorError(f"Failed to create Jira issue: {error.code} {detail}")
        except Exception as error:
            raise ProcessorError(f"Failed to create Jira issue: {error}")

    def _record_outputs(self, issue_key: str, server: str) -> None:
        if issue_key:
            self.env["jira_issue_key"] = issue_key
            self.env["jira_issue_url"] = f"{server}/browse/{issue_key}"
            self.output(f"Created Jira issue {issue_key}")
        else:
            self.output("Created Jira issue (no key in response)")

    def _transition_issue(self, issue_key: str, server: str) -> None:
        transition_id = self._strip(self.env.get("JIRA_TRANSITION_ID"))
        if not issue_key or not transition_id:
            return

        payload = json.dumps({"transition": {"id": transition_id}}).encode("utf-8")
        request = urllib.request.Request(
            f"{server}/rest/api/2/issue/{issue_key}/transitions",
            data=payload,
            method="POST",
        )
        request.add_header("Authorization", f"Bearer {self._token()}")
        request.add_header("Content-Type", "application/json")

        timeout = self._timeout_seconds()

        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=ssl._create_unverified_context(),
            ) as response:
                if response.status not in (200, 204):
                    raise ProcessorError(
                        f"Jira transition failed with HTTP {response.status}"
                    )
        except urllib.error.HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="ignore")
            except Exception:
                detail = str(error)
            raise ProcessorError(
                f"Failed to transition Jira issue {issue_key}: {error.code} {detail}"
            )
        except Exception as error:
            raise ProcessorError(
                f"Failed to transition Jira issue {issue_key}: {error}"
            )

        self.output(
            f"Transitioned Jira issue {issue_key} using transition id {transition_id}"
        )

    def _server(self) -> str:
        return str(self.env["JIRA_SERVER"]).rstrip("/")

    def _token(self) -> str:
        return str(self.env["JIRA_TOKEN"]).strip()

    def _timeout_seconds(self) -> int:
        raw = self.env.get("timeout_seconds")
        try:
            return int(raw) if raw is not None else 45
        except Exception:
            return 45

    def _strip(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return ""
        return str(value).strip()

    def _default_summary(self, name: str, version: str) -> str:
        parts = [name]
        if version:
            parts.append(version)
        parts.append("imported to Munki")
        return " ".join(parts)

    def main(self) -> None:
        self.env = getattr(self, "env", {})

        if self._should_skip():
            self.output(
                "MunkiImporter reported no changes. Skipping Jira Task creation."
            )
            return

        server = self._server()
        fields = self._issue_fields()
        payload = self._encode_payload(fields)
        response_payload = self._post_issue(server, payload)

        issue_key = self._strip(response_payload.get("key"))
        self._transition_issue(issue_key, server)

        self._record_outputs(issue_key, server)
