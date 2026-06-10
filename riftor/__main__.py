"""riftor entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
    parser.add_argument(
        "--doctor", action="store_true",
        help="check which external recon tools (nmap/httpx/…) are installed, then exit",
    )
    parser.add_argument("--model", help="override the model for this run")
    parser.add_argument(
        "--chakla-model", dest="chakla_model",
        help="override the Chakla (worker) model for this run",
    )
    parser.add_argument("--api-key", dest="api_key", help="override the API key for this run")
    parser.add_argument("--workdir", help="engagement working directory (default: cwd)")
    parser.add_argument("--scope-file", dest="scope_file", help="load scope targets from a file")
    parser.add_argument(
        "--browser-headed", dest="browser_headed", action="store_true",
        help="run the Playwright browser headed (visible) for this run",
    )
    parser.add_argument(
        "--no-telemetry", action="store_true",
        help="disable telemetry (crash + usage reporting) for this run",
    )
    parser.add_argument(
        "-p", "--prompt",
        help="run a single task non-interactively and exit (headless one-shot)",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="non-interactive mode; implied by --prompt. Prints the result and exits.",
    )
    parser.add_argument(
        "--i-know-what-i-am-doing-give-me-full-access",
        action="store_true",
        dest="yolo",
        help="bypass all permission prompts, deny rules, scope enforcement, and step limits",
    )
    args = parser.parse_args()

    from riftor.config import CONFIG_PATH, Config

    if args.config:
        print(CONFIG_PATH)
        return

    if args.doctor:
        from riftor.engagement.doctor import check_toolchain, render_plain

        print(render_plain(check_toolchain()))
        return

    cfg = Config.load()
    if args.model:
        cfg.model = args.model
    if args.chakla_model:
        cfg.chakla_model = args.chakla_model
    if args.api_key:
        cfg.api_key = args.api_key
    if args.browser_headed:
        cfg.browser_headless = False  # this run only; not written to config.toml
    if args.no_telemetry:
        cfg.telemetry = False

    workdir = Path(args.workdir).expanduser() if args.workdir else Path.cwd()

    if args.prompt or args.headless:
        from riftor.headless import run_headless

        code = run_headless(cfg, workdir, prompt=args.prompt, scope_file=args.scope_file, yolo=args.yolo)
        sys.exit(code)

    # Graceful first-run: if no credentials are configured, guide the operator.
    if not cfg.has_credentials() and not cfg.onboarded:
        _print_onboarding(cfg)

    from riftor.tui.app import RiftorApp

    app = RiftorApp(cfg, workdir=workdir, yolo=args.yolo)
    if args.scope_file:
        _preload_scope(app, args.scope_file)
    app.run()


def _print_onboarding(cfg) -> None:
    env = cfg.provider_env() or "ANTHROPIC_API_KEY"
    print("riftor: no API key detected for the configured model.")
    print(f"  model:    {cfg.model}")
    print(f"  set one:  export {env}=...   (or run a local Ollama server)")
    print("  then:     riftor   ·   change the model with /model or /config")
    print("Launching anyway — set a key, or use /config inside the app.\n")
    cfg.onboarded = True
    try:
        cfg.save()
    except Exception:  # noqa: BLE001
        pass


def _preload_scope(app, scope_file: str) -> None:
    try:
        text = Path(scope_file).expanduser().read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"riftor: could not read scope file {scope_file}: {exc}", file=sys.stderr)
        return
    app.engagement.import_scope(text)


if __name__ == "__main__":
    main()
