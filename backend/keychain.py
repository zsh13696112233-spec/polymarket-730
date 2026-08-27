from __future__ import annotations

import subprocess
from dataclasses import dataclass


class KeychainError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KeychainReference:
    service: str
    account: str


class MacOSKeychain:
    """Minimal macOS Keychain wrapper that never exposes a secret in logs or storage."""

    SECURITY = "/usr/bin/security"

    def set_secret(self, reference: KeychainReference, secret: str) -> None:
        value = secret.strip()
        if not value:
            raise KeychainError("密钥不能为空")
        completed = subprocess.run(  # noqa: S603
            [
                self.SECURITY,
                "add-generic-password",
                "-U",
                "-s",
                reference.service,
                "-a",
                reference.account,
                "-w",
                value,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise KeychainError("无法写入 macOS 钥匙串，请检查系统授权")

    def get_secret(self, reference: KeychainReference) -> str:
        completed = subprocess.run(  # noqa: S603
            [
                self.SECURITY,
                "find-generic-password",
                "-s",
                reference.service,
                "-a",
                reference.account,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise KeychainError("钥匙串中没有找到对应密钥")
        secret = completed.stdout.strip()
        if not secret:
            raise KeychainError("钥匙串中的密钥为空")
        return secret

    def delete_secret(self, reference: KeychainReference) -> None:
        completed = subprocess.run(  # noqa: S603
            [
                self.SECURITY,
                "delete-generic-password",
                "-s",
                reference.service,
                "-a",
                reference.account,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode not in {0, 44}:
            raise KeychainError("无法从 macOS 钥匙串删除密钥")
