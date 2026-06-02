"""riftor entry point."""

from __future__ import annotations

import argparse

from riftor import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="riftor",
        description="An open-source offensive-security AI agent that lives in your terminal.",
    )
    parser.add_argument("--version", action="version", version=f"riftor {__version__}")
    parser.add_argument(
        "--config", action="store_true", help="print the config file path and exit"
    )
    args = parser.parse_args()

    from riftor.config import CONFIG_PATH, Config

    if args.config:
        print(CONFIG_PATH)
        return

    cfg = Config.load()

    from riftor.tui.app import RiftorApp

    RiftorApp(cfg).run()


if __name__ == "__main__":
    main()
