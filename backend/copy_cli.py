from __future__ import annotations

import argparse
import getpass
import json

from backend.keychain import KeychainReference, MacOSKeychain

DEFAULT_SERVICE = "polymarket-wallet-monitor.execution"


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="管理链上交易执行密钥（仅 macOS 钥匙串）")
    cli.add_argument(
        "action",
        choices=[
            "set-key",
            "delete-key",
            "check-key",
            "set-builder-creds",
            "delete-builder-creds",
        ],
    )
    cli.add_argument("--account", required=True, help="签名钱包地址或本机密钥别名")
    cli.add_argument("--service", default=DEFAULT_SERVICE, help=argparse.SUPPRESS)
    return cli


def main() -> None:
    args = parser().parse_args()
    reference = KeychainReference(service=args.service, account=args.account)
    keychain = MacOSKeychain()
    builder_reference = KeychainReference(
        service=f"{args.service}.builder",
        account=args.account,
    )
    if args.action == "set-key":
        secret = getpass.getpass("输入执行钱包私钥（不会回显）：")
        confirmation = getpass.getpass("再次输入私钥：")
        if secret != confirmation:
            raise SystemExit("两次输入不一致，未保存")
        keychain.set_secret(reference, secret)
        print(f"密钥已保存到 macOS 钥匙串：{args.service} / {args.account}")
        return
    if args.action == "set-builder-creds":
        credentials = {
            "key": getpass.getpass("Builder API key："),
            "secret": getpass.getpass("Builder API secret："),
            "passphrase": getpass.getpass("Builder API passphrase："),
        }
        if not all(credentials.values()):
            raise SystemExit("Builder 凭证不完整，未保存")
        keychain.set_secret(builder_reference, json.dumps(credentials))
        print("Builder 凭证已保存到 macOS 钥匙串")
        return
    if args.action == "delete-builder-creds":
        keychain.delete_secret(builder_reference)
        print("Builder 凭证已删除")
        return
    if args.action == "delete-key":
        keychain.delete_secret(reference)
        print("钥匙串密钥已删除")
        return
    keychain.get_secret(reference)
    print("钥匙串密钥已配置")


if __name__ == "__main__":
    main()
