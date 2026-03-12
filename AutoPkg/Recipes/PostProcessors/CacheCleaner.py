# Downloaded from https://github.com/autopkg/mscottblake-recipes/blob/f52cc854837ec404e0ea6d2fe61808233c27151f/SharedPostProcessors/CacheCleaner.py
# Commit: f52cc854837ec404e0ea6d2fe61808233c27151f
# Downloaded at: 2026-03-12 03:04:02 UTC

import shutil
from pathlib import Path
from typing import Any

from autopkglib import Processor, ProcessorError  # pylint: disable=import-error

__all__ = ["CacheCleaner"]


class CacheCleaner(Processor):
    """
    A PostProcessor for AutoPkg that removes the recipe's cache directory
    after a successful run, utilizing `pathlib` for all path manipulations
    and directory deletion.

    This ensures that each recipe run starts with a clean slate, preventing
    potential issues with stale or leftover files from previous runs.
    `pathlib` provides an object-oriented way to handle file system paths,
    often leading to cleaner and more readable code.
    """

    description = "Deletes the current recipe's cache directory using pathlib."
    input_variables = {}
    output_variables = {}

    def main(self) -> None:
        """
        Main method of the processor.

        This method retrieves the RECIPE_CACHE_DIR, converts it to a `Path` object,
        and attempts to remove it and its contents recursively. If the directory does
        not exist or if there's an error during removal, appropriate messages are logged.
        """
        if not self.env:
            self.env: dict[str, Any] = {}

        recipe_cache_path: Path = Path(self.env["RECIPE_CACHE_DIR"])

        self.output(
            f"Attempting to delete recipe cache directory: {recipe_cache_path}",
            verbose_level=2,
        )

        if not recipe_cache_path.exists():
            raise ProcessorError(f"Cache directory does not exist: {recipe_cache_path}")
        if not recipe_cache_path.is_dir():
            raise ProcessorError(f"Path is not a directory: {recipe_cache_path}")

        try:
            shutil.rmtree(recipe_cache_path)
        except OSError as exc:
            raise ProcessorError(f"Error deleting cache directory: {exc}") from exc

        self.output(
            f"Successfully deleted cache directory: {recipe_cache_path}",
            verbose_level=0,
        )


if __name__ == "__main__":
    processor = CacheCleaner()
    processor.execute_shell()
