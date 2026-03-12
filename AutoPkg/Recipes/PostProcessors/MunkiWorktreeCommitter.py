# Downloaded from https://github.com/autopkg/mscottblake-recipes/blob/f52cc854837ec404e0ea6d2fe61808233c27151f/SharedPostProcessors/MunkiWorktreeCommitter.py
# Commit: f52cc854837ec404e0ea6d2fe61808233c27151f
# Downloaded at: 2026-03-12 03:04:03 UTC

"""
AutoPkg Post-Processor: Commit Munki repo changes to Git.

Notes
- Designed to run AFTER MunkiImporter.
- Handles high concurrency where multiple recipes modify the same repo simultaneously.
"""

import plistlib
import random
import shlex
import shutil
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from autopkglib import Processor, ProcessorError

__all__ = ["MunkiWorktreeCommitter"]


class MunkiWorktreeCommitter(Processor):
    description = "Stage and commit Munki repo changes produced by MunkiImporter."
    input_variables: dict[str, dict[str, Any]] = {
        "repo_path": {
            "required": False,
            "description": "Path to Munki repo root. If omitted, derived from MunkiImporter outputs.",
        },
        "pkginfo_path": {
            "required": False,
            "description": "Backward-compatible path to pkginfo plist (prefer MunkiImporter outputs).",
        },
        "commit_message": {
            "required": False,
            "description": "Commit message template. Supports {name} and {version} from pkginfo.",
            "default": "AutoPkg Import: {name} {version}",
        },
        "add_pkg": {
            "required": False,
            "description": "Stage the installer package referenced by MunkiImporter.",
            "default": True,
        },
        "add_icon": {
            "required": False,
            "description": "Stage the icon referenced by MunkiImporter, if present.",
            "default": False,
        },
        "additional_paths": {
            "required": False,
            "description": "Extra file paths (absolute or repo-relative) to stage.",
        },
        "push": {
            "required": False,
            "description": "If true, push commit to remote after committing.",
            "default": True,
        },
        "remote": {
            "required": False,
            "description": "Remote name for push (if enabled).",
            "default": "origin",
        },
        "branch": {
            "required": False,
            "description": "Branch name for push (if enabled).",
            "default": "main",
        },
        "require_munki_change": {
            "required": False,
            "description": "If true, skip when MunkiImporter indicates no change.",
            "default": True,
        },
        "original_munki_repo": {
            "required": False,
            "description": "Path to main repo (set by MunkiWorktreeCreator).",
        },
        "worktree_branch": {
            "required": False,
            "description": "The temp branch name (set by MunkiWorktreeCreator).",
        },
    }
    output_variables: dict[str, str] = {
        "munki_repo_commit_sha": "Commit SHA created (if any).",
        "munki_repo_commit_paths": "List of repo-relative paths included in the commit.",
        "munki_repo_commit_message": "The commit message used.",
        "munki_repo_commit_skipped": "True if there was nothing to commit.",
    }

    def _strip(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return ""
        return str(value).strip()

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

    def _run_cmd(self, args: list[str], cwd: Path) -> str:
        try:
            result = subprocess.run(
                args,
                cwd=str(cwd),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            quoted = " ".join(map(shlex.quote, args))
            raise ProcessorError(f"Command failed: {quoted}\n{error.stdout}")
        return result.stdout.strip()

    def _existing_path(self, value: Any) -> Path | None:
        if not value:
            return None
        try:
            path = Path(str(value)).expanduser()
        except TypeError:
            return None
        if path.exists():
            return path.resolve()
        return None

    def _pkginfo_path(self) -> Path | None:
        for key in (
            "pkginfo_repo_path",
            "pkginfo_path",
            "munki_pkginfo_path",
        ):
            path = self._existing_path(self.env.get(key))
            if path:
                return path
        return None

    def _pkg_path(self, repo_root: Path) -> Path | None:
        for key in ("pkg_repo_path", "pkg_path"):
            path = self._existing_path(self.env.get(key))
            if path:
                return path

        pkginfo_path = self._pkginfo_path()
        if not pkginfo_path or not pkginfo_path.exists():
            return None

        try:
            with pkginfo_path.open("rb") as handle:
                pkginfo = plistlib.load(handle)
        except Exception:
            return None

        location = pkginfo.get("installer_item_location")
        if isinstance(location, str) and location:
            candidate = (repo_root / "pkgs" / location).resolve()
            if candidate.exists():
                return candidate
        return None

    def _icon_path(self, repo_root: Path) -> Path | None:
        for key in ("icon_repo_path", "icon_path"):
            path = self._existing_path(self.env.get(key))
            if path:
                return path

        pkginfo_path = self._pkginfo_path()
        if not pkginfo_path or not pkginfo_path.exists():
            return None

        try:
            with pkginfo_path.open("rb") as handle:
                pkginfo = plistlib.load(handle)
        except Exception:
            return None

        icon_name = pkginfo.get("icon_name")
        if isinstance(icon_name, str) and icon_name:
            candidate = (repo_root / "icons" / icon_name).resolve()
            if candidate.exists():
                return candidate
        return None

    def _additional_paths(self, repo_root: Path) -> set[Path]:
        value = self.env.get("additional_paths")
        paths: set[Path] = set()

        if value is None:
            return paths

        if isinstance(value, str):
            candidates: Iterable[Any] = [value]
        elif isinstance(value, Iterable):
            candidates = value
        else:
            candidates = [value]

        for item in candidates:
            path = self._existing_path(item)
            if not path and item:
                candidate = repo_root / str(item)
                if candidate.exists():
                    path = candidate.resolve()
            if path:
                paths.add(path)

        return paths

    def _relative_to_repo(self, path: Path, repo_root: Path) -> str:
        try:
            return str(path.resolve().relative_to(repo_root.resolve()))
        except Exception:
            return path.name

    def _paths_to_stage(self, repo_root: Path) -> list[Path]:
        paths: set[Path] = set()

        pkginfo_path = self._pkginfo_path()
        if pkginfo_path and pkginfo_path.exists():
            paths.add(pkginfo_path)

        if self._bool(self.env.get("add_pkg"), True):
            pkg_path = self._pkg_path(repo_root)
            if pkg_path:
                paths.add(pkg_path)

        if self._bool(self.env.get("add_icon"), True):
            icon_path = self._icon_path(repo_root)
            if icon_path:
                paths.add(icon_path)

        paths.update(self._additional_paths(repo_root))

        return sorted(paths)

    def _record_skip(self, message: str) -> None:
        self.env["munki_repo_commit_skipped"] = True
        self.env["munki_repo_commit_paths"] = []
        self.output(message)

    def _git_has_changes(self, repo_root: Path) -> bool:
        status = self._run_cmd(["git", "status", "--porcelain"], cwd=repo_root)
        return bool(status.strip())

    def _commit_message(self, pkginfo_path: Path | None) -> str:
        name = None
        version = None

        if pkginfo_path and pkginfo_path.exists():
            try:
                with pkginfo_path.open("rb") as handle:
                    pkginfo = plistlib.load(handle)
                name = pkginfo.get("name")
                version = pkginfo.get("version")
            except Exception:
                pass

        name = name or self.env.get("NAME") or self.env.get("display_name") or "package"
        version = version or self.env.get("version") or self.env.get("VERSION") or ""

        template = self.env.get("commit_message")

        return (
            str(template)
            .format(epic_key=self.env.get("EPIC_KEY"), name=name, version=version)
            .strip()
        )

    def _push_worktree_to_main(self, worktree_path: Path) -> None:
        """Rebase isolated worktree commits onto main and push."""
        remote = self.env.get("remote", "origin")
        target_branch = self.env.get("branch", "main")
        max_retries = 10

        for attempt in range(1, max_retries + 1):
            try:
                self._run_cmd(
                    ["git", "fetch", remote, target_branch], cwd=worktree_path
                )

                self._run_cmd(
                    ["git", "rebase", "--autostash", f"{remote}/{target_branch}"],
                    cwd=worktree_path,
                )

                self._run_cmd(
                    ["git", "push", remote, f"HEAD:{target_branch}"], cwd=worktree_path
                )

                self.output(f"Successfully pushed to {remote}/{target_branch}")
                return

            except ProcessorError as exc:
                self.output(f"Git push failed (attempt {attempt}). Error: {exc}")

                sleep_time = random.uniform(1.0, 4.0) + attempt
                self.output("Retrying git push in {sleep_time:.1f}s...")
                time.sleep(sleep_time)

        raise ProcessorError(
            f"Failed to push to {target_branch} after {max_retries} attempts."
        )

    def _cleanup(self, worktree_path: Path, original_repo: Path) -> None:
        """Remove the worktree and the temp branch."""
        branch_name = self.env.get("worktree_branch")
        if not branch_name:
            return

        self.output(f"Cleaning up worktree at {worktree_path}...")

        try:
            # This command must be run from the original repo, not the worktree
            self._run_cmd(
                ["git", "worktree", "remove", "--force", str(worktree_path)],
                cwd=original_repo,
            )

            # We pushed our changes to main, so the temp branch is now garbage
            self._run_cmd(["git", "branch", "-D", branch_name], cwd=original_repo)
        except Exception:
            pass

        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)

    def main(self) -> None:
        self.env = getattr(self, "env", {})

        # The MunkiWorktreeCreator pre-processor sets 'repo_path' to the temp dir
        worktree_path = Path(self.env["repo_path"])
        original_repo = self.env.get("original_munki_repo")

        # Fallback if Pre-Processor wasn't used (standard run)
        if not original_repo:
            self.output("WARNING: Not running in Worktree mode. Concurrency unsafe.")
            original_repo = str(worktree_path)

        # Cleanup Helper Wrapper
        def do_cleanup() -> None:
            self._cleanup(worktree_path, Path(original_repo))

        if self._should_skip():
            self._record_skip("MunkiImporter reported no changes.")
            do_cleanup()
            return

        paths = self._paths_to_stage(worktree_path)

        if paths:
            rels = [str(path) for path in paths]
            self._run_cmd(["git", "add", "-A", "--", *rels], cwd=worktree_path)
        else:
            rels = []
            self._run_cmd(["git", "add", "-A"], cwd=worktree_path)

        if not self._git_has_changes(worktree_path):
            self._record_skip("No changes to commit.")
            do_cleanup()
            return

        message = self._commit_message(self._pkginfo_path())
        self._run_cmd(["git", "commit", "-m", message], cwd=worktree_path)

        sha = self._run_cmd(["git", "rev-parse", "HEAD"], cwd=worktree_path)
        self.env["munki_repo_commit_sha"] = sha
        self.env["munki_repo_commit_message"] = message
        self.env["munki_repo_commit_paths"] = rels
        self.env["munki_repo_commit_skipped"] = False

        self.output(
            f"Committed changes to Munki: {sha[:7]} - {message}", verbose_level=0
        )

        if self._bool(self.env.get("push")):
            try:
                self._push_worktree_to_main(worktree_path)
            except ProcessorError:
                # Ensure cleanup happens even if push fails
                do_cleanup()
                raise

        do_cleanup()


if __name__ == "__main__":
    processor = MunkiWorktreeCommitter()
    processor.execute_shell()
