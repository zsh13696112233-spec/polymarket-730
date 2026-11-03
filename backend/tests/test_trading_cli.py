import sys
from unittest.mock import Mock

import pytest

from backend import trading_cli


@pytest.mark.parametrize(
    ("account", "expected"),
    [
        ("0x" + "aB" * 20, "0x" + "ab" * 20),
        ("MyWalletAlias", "MyWalletAlias"),
    ],
)
def test_set_key_normalizes_wallet_address_only(monkeypatch, account, expected):
    keychain = Mock()
    monkeypatch.setattr(trading_cli, "MacOSKeychain", lambda: keychain)
    monkeypatch.setattr(trading_cli.getpass, "getpass", lambda _: "test-secret")
    monkeypatch.setattr(sys, "argv", ["trading_cli", "set-key", "--account", account])

    trading_cli.main()

    keychain.set_secret.assert_called_once_with(
        trading_cli.KeychainReference(service=trading_cli.DEFAULT_SERVICE, account=expected),
        "test-secret",
    )
