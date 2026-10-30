"""Private credential storage shared with the BotMinds network worker."""

import json
import os
from pathlib import Path
import tempfile
import threading
import time


class PlayerbotsAISettings:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _read(self):
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 8192:
                raise ValueError("Invalid credential file")
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict) or not isinstance(value.get("api_key", ""), str):
            raise ValueError("Invalid credential file")
        return value

    @staticmethod
    def _status(value):
        return {"configured": bool(value.get("api_key")), "model": "qwen3.8-flash",
                "updated_at": value.get("updated_at")}

    def status(self):
        with self.lock:
            return self._status(self._read())

    def update(self, api_key, account_id):
        if not isinstance(api_key, str) or (api_key and (
                not 16 <= len(api_key) <= 512 or any(not 33 <= ord(char) <= 126 for char in api_key))):
            raise ValueError("Invalid API key")
        value = {"api_key": api_key, "updated_at": int(time.time()), "updated_by": account_id}
        with self.lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix=".playerbots-ai-", delete=False) as file:
                    temporary = Path(file.name)
                    os.fchmod(file.fileno(), 0o600)
                    json.dump(value, file, ensure_ascii=False)
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return self._status(value)
