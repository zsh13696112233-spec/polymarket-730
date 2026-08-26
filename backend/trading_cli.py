"""Manage the shared execution-wallet credentials in macOS Keychain."""

from backend.copy_cli import DEFAULT_SERVICE, main, parser

__all__ = ["DEFAULT_SERVICE", "main", "parser"]

if __name__ == "__main__":
    main()
