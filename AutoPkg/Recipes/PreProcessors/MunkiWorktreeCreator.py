# Downloaded from https://github.com/autopkg/mscottblake-recipes/blob/f52cc854837ec404e0ea6d2fe61808233c27151f/SharedPreProcessors/MunkiWorktreeCreator.py
# Commit: f52cc854837ec404e0ea6d2fe61808233c27151f
# Downloaded at: 2026-03-12 03:04:08 UTC

"""
AutoPkg Pre-Processor: Git Worktree Creator

Creates a temporary Git worktree for the duration of the recipe run.
This isolates file modifications and Git operations, allowing high concurrency.
"""

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from autopkglib import Processor, ProcessorError

__all__ = ["MunkiWorktreeCreator"]


class MunkiWorktreeCreator(Processor):
    description = "Creates a Git worktree for isolated MunkiImporter runs."
    input_variables: dict[str, dict[str, Any]] = {
        "GIT_DEFAULT_BRANCH": {
            "required": False,
            "description": "The branch to checkout in the worktree (default: main).",
            "default": "main",
        },
        "COPY_CATALOGS_TO_WORKTREE": {
            "required": False,
            "description": "Copy catalogs to worktree. Useful when catalogs are not tracked by git.",
            "default": False,
        },
    }
    output_variables: dict[str, str] = {
        "repo_path": "Overwritten to point to the temporary worktree.",
        "MUNKI_REPO": "Overwritten to point to the temporary worktree.",
        "original_munki_repo": "The path to the main repo (for cleanup later).",
        "worktree_branch": "The specific branch name created for this worktree.",
    }

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

    def main(self) -> None:
        self.env = getattr(self, "env", {})

        main_repo = Path(self.env["MUNKI_REPO"]).resolve()
        recipe_name = self.env["NAME"]
        base_branch = self.env["GIT_DEFAULT_BRANCH"]
        should_copy_catalogs = self._bool(self.env["COPY_CATALOGS_TO_WORKTREE"])

        # Sanity check the main repo
        if not (main_repo / ".git").exists():
            raise ProcessorError(f"Main repo not found at {main_repo}")

        # Use timestamp and recipe name to ensure uniqueness
        ts = int(time.time())
        branch_name = f"autopkg/{recipe_name.replace(' ', '_')}-{ts}"

        # Create a temp directory outside the repo to hold the worktree
        temp_dir = Path(tempfile.mkdtemp(prefix="autopkg_worktree_"))

        # Create Worktree
        # git worktree add -b <new_branch> <path> <origin/base>
        cmd = [
            "git",
            "worktree",
            "add",
            "-b",
            branch_name,
            str(temp_dir),
            f"origin/{base_branch}",
        ]

        self.output(f"Creating a git worktree at {temp_dir} on branch {branch_name}")

        try:
            subprocess.run(
                cmd, cwd=str(main_repo), check=True, capture_output=True, text=True
            )

            # This forces MunkiImporter (and other subsequent steps) to use the worktree
            self.env["original_munki_repo"] = str(main_repo)
            self.env["repo_path"] = str(temp_dir)
            self.env["MUNKI_REPO"] = str(temp_dir)
            self.env["worktree_branch"] = branch_name

            self.output(
                (
                    "Successfully created a git worktree directory at ",
                    f"'{temp_dir}' on branch '{branch_name}'",
                ),
                verbose_level=0,
            )
        except subprocess.CalledProcessError as exc:
            # Cleanup temp dir if git fails
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise ProcessorError(f"Failed to create worktree: {exc.stderr}") from exc

        if should_copy_catalogs:
            self.output("Copying catalogs to worktree")
            try:
                cmd = ["cp", "-R", main_repo / "catalogs", temp_dir / "catalogs"]
                subprocess.run(cmd, check=True, capture_output=True, text=True)

                self.output("Successfully copied catalogs to worktree", verbose_level=0)
            except subprocess.CalledProcessError as exc:
                raise ProcessorError(
                    f"Failed to copy catalogs to worktree: {exc.stderr}"
                ) from exc


if __name__ == "__main__":
    processor = MunkiWorktreeCreator()
    processor.execute_shell()
