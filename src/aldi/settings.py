"""Her settings: the Gemini key and the reporting token, in a file in her user config directory.

Neither is ever in the repo or a Job folder.
"""

import json
import os
from pathlib import Path

import platformdirs


def default_config_dir() -> Path:
    return Path(platformdirs.user_config_dir("Aldi", appauthor=False))


class Settings:
    def __init__(self, config_dir: Path, dev_env_file: Path | None = None):
        """`dev_env_file`: a `.env` whose `GEMINI_API_KEY` (or the variable itself) is the key while none is saved."""
        self._file = config_dir / "settings.json"
        self._dev_env_file = dev_env_file

    @property
    def key(self) -> str:
        """The saved Gemini key, or in development the one in `.env`; empty when there is none."""
        return self._read().get("gemini_api_key") or self._dev_key()

    @property
    def report_token(self) -> str:
        """The fine-grained GitHub token Error Reports are filed with; empty when there is none."""
        return self._read().get("report_token", "")

    def save_key(self, key: str) -> None:
        self._save(gemini_api_key=key)

    def save_report_token(self, token: str) -> None:
        self._save(report_token=token)

    def _save(self, **changes: str) -> None:
        settings = self._read() | changes
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    def _read(self) -> dict:
        if not self._file.is_file():
            return {}
        return json.loads(self._file.read_text(encoding="utf-8"))

    def _dev_key(self) -> str:
        if self._dev_env_file is None:
            return ""
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key and self._dev_env_file.is_file():
            for line in self._dev_env_file.read_text(encoding="utf-8").splitlines():
                name, _, value = line.partition("=")
                if name.strip() == "GEMINI_API_KEY":
                    key = value.strip().strip("\"'")
        return key
