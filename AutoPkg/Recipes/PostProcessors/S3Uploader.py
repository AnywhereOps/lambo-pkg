# Downloaded from https://github.com/autopkg/mscottblake-recipes/blob/f52cc854837ec404e0ea6d2fe61808233c27151f/SharedPostProcessors/S3Uploader.py
# Commit: f52cc854837ec404e0ea6d2fe61808233c27151f
# Downloaded at: 2026-03-12 03:04:03 UTC

"""
AutoPkg Post-Processor: Upload Munki import artifacts to S3.

Environment variables should be exported as AUTOPKG_<NAME> so they flow into the
processor inputs (e.g. AUTOPKG_MUNKI_BUCKET_NAME, AUTOPKG_S3_PREFIX).

Notes
- Requires `boto3` available in the AutoPkg runtime. Uses default AWS credentials
  chain (env vars, config files, IAM role).
- Derives files to upload from MunkiImporter outputs if not explicitly provided.
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from autopkglib import Processor, ProcessorError

try:
    import boto3
except Exception:
    raise ProcessorError(
        "boto3 is required for S3Uploader. Install boto3 in the AutoPkg runtime."
    )

__all__ = ["S3Uploader"]


class S3Uploader(Processor):
    description = "Upload pkg and pkginfo from Munki import to S3."
    input_variables: dict[str, dict[str, Any]] = {
        "MUNKI_BUCKET_NAME": {
            "required": True,
            "description": "Target S3 bucket name.",
        },
        "S3_PREFIX": {
            "required": False,
            "description": "S3 key prefix (folder path) within the bucket.",
            "default": "",
        },
        "S3_ENDPOINT_URL": {
            "required": False,
            "description": "Optional custom S3 endpoint URL (e.g., LocalStack).",
        },
        "S3_UPLOAD_PKG": {
            "required": False,
            "description": "Upload the installer package referenced by pkginfo.",
            "default": True,
        },
        "S3_UPLOAD_PKGINFO": {
            "required": False,
            "description": "Upload the pkginfo plist.",
            "default": False,
        },
        "S3_UPLOAD_ICON": {
            "required": False,
            "description": "Upload the icon, if it exists.",
            "default": False,
        },
        "require_munki_change": {
            "required": False,
            "description": "If true, skip or fail when MunkiImporter indicates no change or was not run.",
            "default": True,
        },
    }
    output_variables = {
        "s3_bucket": {"description": "Bucket uploaded to."},
        "s3_uploaded": {"description": "List of s3://bucket/key objects uploaded."},
    }
    description = __doc__

    def _should_skip(self) -> bool:
        require_change = self._bool(self.env.get("require_munki_change"), True)
        repo_changed = self._bool(self.env.get("munki_repo_changed"), True)
        return require_change and not repo_changed

    def _repo_root_from_pkginfo(self, pkginfo_path: str | Path) -> Path:
        if isinstance(pkginfo_path, str):
            pkginfo_path = Path(pkginfo_path)

        p = pkginfo_path.resolve().parent
        while p != p.parent:
            if p.name == "pkgsinfo":
                return p.parent
            p = p.parent

        raise ProcessorError("Unable to determine Munki Repo root path.")

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

    def _files_to_upload(self) -> set[Path]:
        files: set[Path] = set()

        upload_pkginfo = self._bool(self.env.get("S3_UPLOAD_PKGINFO"))
        upload_pkg = self._bool(self.env.get("S3_UPLOAD_PKG"))
        upload_icon = self._bool(self.env.get("S3_UPLOAD_ICON"))

        if upload_pkginfo:
            pkginfo_raw = self.env.get("pkginfo_repo_path")
            if pkginfo_raw:
                pkginfo_path = Path(pkginfo_raw)
                if pkginfo_path.exists():
                    files.add(pkginfo_path)

        if upload_pkg:
            pkg_raw = self.env.get("pkg_repo_path")
            if pkg_raw:
                pkg_path = Path(pkg_raw)
                if pkg_path.exists():
                    files.add(pkg_path)

        if upload_icon:
            icon_raw = self.env.get("icon_repo_path")
            if icon_raw:
                icon_path = Path(icon_raw)
                if icon_path.exists():
                    files.add(icon_path)

        return files

    def _s3_client(self, endpoint_url: str | None):
        session = boto3.Session()
        if endpoint_url:
            return session.client("s3", endpoint_url=endpoint_url)
        return session.client("s3")

    def _upload_files(
        self,
        client: Any,
        files: Iterable[Path],
        bucket: str,
        prefix: str = "",
    ) -> list[str]:
        uploaded: list[str] = []

        for path in files:
            rel = self._relative_to_repo(
                path, Path(self._strip(self.env.get("MUNKI_REPO")))
            )
            key = f"{prefix}/{rel}" if prefix else rel
            s3_path = f"s3://{bucket}/{key}"
            try:
                client.upload_file(str(path), bucket, key)
                uploaded.append(s3_path)
            except Exception as error:
                raise ProcessorError(
                    f"Failed to upload {path} to s3://{bucket}/{key}: {error}"
                )

        return uploaded

    def _relative_to_repo(self, path: Path, repo_root: Path) -> str:
        try:
            return str(path.resolve().relative_to(repo_root.resolve()))
        except Exception:
            return path.name

    def _record_skip(self, bucket: str, message: str) -> None:
        self.env["s3_bucket"] = bucket
        self.env["s3_uploaded"] = []
        self.output(message)

    def _strip(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return ""
        return str(value).strip()

    def main(self) -> None:
        self.env = getattr(self, "env", {})

        if self._should_skip():
            self.output("MunkiImporter reported no changes. Skipping S3 upload.")
            return

        bucket = self._strip(self.env.get("MUNKI_BUCKET_NAME"))
        prefix = self._strip(self.env.get("S3_PREFIX"))

        files = self._files_to_upload()
        if not files:
            self._record_skip(bucket, "No files to upload to S3.")
            return

        endpoint_url = self._strip(self.env.get("S3_ENDPOINT_URL"))
        client = self._s3_client(endpoint_url)

        uploaded = self._upload_files(client, files, bucket, prefix)

        self.env["s3_bucket"] = bucket
        self.env["s3_uploaded"] = uploaded

        destination = f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"

        self.output(f"Uploaded {len(uploaded)} object(s) to {destination}")
        for obj in uploaded:
            self.output(f"  - {obj}")
