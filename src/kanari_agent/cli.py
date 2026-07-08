"""
Command-line interface for Kanari Agent
"""

from __future__ import annotations

import argparse
import sys

from kanari_agent.config import AGENT_VERSION


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add args common to multiple subcommands"""
    parser.add_argument("--config", "-c", help="Path to YAML config file")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")


def cmd_audit(args: argparse.Namespace) -> None:
    from kanari_agent.audit import run_audit
    from kanari_agent.config import load_config

    if getattr(args, "deep", False):
        print(
            "--deep is deprecated: configuration analysis now runs by default",
            file=sys.stderr,
        )

    config = load_config(args.config)
    exit_code = run_audit(
        config=config,
        json_output=getattr(args, "json", False),
        md_output=getattr(args, "md", False),
        no_color=getattr(args, "no_color", False),
        timeout=getattr(args, "timeout", 3),
        config_checks=not getattr(args, "no_config_checks", False),
    )
    sys.exit(exit_code)


def cmd_watch(args: argparse.Namespace) -> None:
    from kanari_agent.audit import run_watch
    from kanari_agent.config import load_config

    config = load_config(args.config)
    run_watch(
        config=config,
        interval=args.interval,
        no_color=args.no_color,
        deep=getattr(args, "deep", False),
    )


def cmd_login(args: argparse.Namespace) -> None:
    from kanari_agent.login import load_kanari_config, run_login

    # Use --api-url flag, then ~/.kanari/config, then default
    api_url = getattr(args, "api_url", None)
    if not api_url:
        api_url = load_kanari_config().get("api_url", "https://api.getkanari.com")
    run_login(api_url)


def cmd_alerts_configure(args: argparse.Namespace) -> None:
    from kanari_agent.config import load_config
    from kanari_agent.login import load_kanari_config, run_alerts_configure

    # Resolve API key: flag > env var > ~/.kanari/config > config.yaml
    config = load_config(getattr(args, "config", None))
    api_key = config.api_key
    if not api_key:
        print("❌ No API key found. Run kanari login first.")
        sys.exit(1)

    kanari_cfg = load_kanari_config()
    api_url = getattr(args, "api_url", None) or kanari_cfg.get(
        "api_url", "https://api.getkanari.com"
    )

    run_alerts_configure(
        api_url=api_url,
        api_key=api_key,
        slack_webhook=getattr(args, "slack_webhook", None),
        alert_email=getattr(args, "email", None),
    )


def cmd_status(args: argparse.Namespace) -> None:
    import urllib.error

    from kanari_agent.login import _get_authed, load_kanari_config

    kanari_cfg = load_kanari_config()
    api_key = kanari_cfg.get("api_key")
    if not api_key:
        print("❌ Not authenticated. Run kanari login first.")
        sys.exit(1)

    api_url = getattr(args, "api_url", None) or kanari_cfg.get(
        "api_url", "https://api.getkanari.com"
    )

    try:
        resp = _get_authed(f"{api_url}/billing/status", api_key=api_key)
        plan = resp.get("plan", "free")
        subscribed = resp.get("subscribed", False)
        sub_label = "active" if subscribed else "none"
        print(f"Plan:         {plan}")
        print(f"Subscription: {sub_label}")
        if plan == "free":
            print("\nUpgrade with: kanari upgrade --plan solo")
    except urllib.error.HTTPError as e:
        print(f"❌ Error {e.code}: {e.reason}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Could not reach {api_url}: {e}")
        sys.exit(1)


def cmd_upgrade(args: argparse.Namespace) -> None:
    import urllib.error
    import webbrowser

    from kanari_agent.login import _post, load_kanari_config

    kanari_cfg = load_kanari_config()
    api_key = kanari_cfg.get("api_key")
    if not api_key:
        print("❌ Not authenticated. Run kanari login first.")
        sys.exit(1)

    api_url = getattr(args, "api_url", None) or kanari_cfg.get(
        "api_url", "https://api.getkanari.com"
    )
    plan = args.plan

    try:
        resp = _post(
            f"{api_url}/billing/checkout?plan={plan}",
            data={},
            api_key=api_key,
        )
        checkout_url = resp.get("checkout_url")
        if not checkout_url:
            print("❌ No checkout URL received.")
            sys.exit(1)
        print(f"Opening checkout for plan: {plan}")
        print(f"URL: {checkout_url}")
        webbrowser.open(checkout_url)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            import json as _json

            detail = _json.loads(body).get("detail", body)
        except Exception:
            detail = body
        print(f"❌ Error {e.code}: {detail}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Could not reach {api_url}: {e}")
        sys.exit(1)


def cmd_manage_billing(args: argparse.Namespace) -> None:
    import urllib.error
    import webbrowser

    from kanari_agent.login import _get_authed, load_kanari_config

    kanari_cfg = load_kanari_config()
    api_key = kanari_cfg.get("api_key")
    if not api_key:
        print("❌ Not authenticated. Run kanari login first.")
        sys.exit(1)

    api_url = getattr(args, "api_url", None) or kanari_cfg.get(
        "api_url", "https://api.getkanari.com"
    )

    try:
        resp = _get_authed(f"{api_url}/billing/portal", api_key=api_key)
        portal_url = resp.get("portal_url")
        if not portal_url:
            print("❌ No portal URL received.")
            sys.exit(1)
        print("Opening billing portal...")
        webbrowser.open(portal_url)
    except urllib.error.HTTPError as e:
        if e.code == 400:
            print("❌ No billing account found. Subscribe first with: kanari upgrade --plan solo")
        else:
            print(f"❌ Error {e.code}: {e.reason}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Could not reach {api_url}: {e}")
        sys.exit(1)


def cmd_doctor(args: argparse.Namespace) -> None:
    from kanari_agent.doctor import run_doctor

    exit_code = run_doctor(
        config_path=getattr(args, "config", None),
        no_color=getattr(args, "no_color", False),
    )
    sys.exit(exit_code)


def cmd_agent(args: argparse.Namespace) -> None:
    from kanari_agent.agent import KanariAgent
    from kanari_agent.config import load_config

    config = load_config(args.config)
    if args.local:
        config.local_mode = True
    if getattr(args, "token", None):
        config.api_key = args.token
    if getattr(args, "interval", None):
        config.check_interval_seconds = args.interval

    agent = KanariAgent(config)
    agent.run()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kanari",
        description="Kanari — on-call monitoring for Celery + Redis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", "-v", action="version", version=f"kanari-agent {AGENT_VERSION}"
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = False  # allow --version without subcommand

    # ── login ──────────────────────────────────────────────────────────────────
    login_p = subparsers.add_parser(
        "login", help="Authenticate and save API key to ~/.kanari/config"
    )
    login_p.add_argument(
        "--api-url", default=None, help="Backend URL (default: https://api.getkanari.com)"
    )
    login_p.set_defaults(func=cmd_login)

    # ── alerts ─────────────────────────────────────────────────────────────────
    alerts_p = subparsers.add_parser("alerts", help="Configure alert channels")
    alerts_sub = alerts_p.add_subparsers(dest="alerts_command", metavar="SUBCOMMAND")
    alerts_sub.required = True

    alerts_cfg_p = alerts_sub.add_parser("configure", help="Set Slack webhook and/or alert email")
    alerts_cfg_p.add_argument("--config", "-c", help="Path to YAML config file")
    alerts_cfg_p.add_argument("--api-url", default=None, help="Backend URL override")
    alerts_cfg_p.add_argument("--slack-webhook", metavar="URL", help="Slack Incoming Webhook URL")
    alerts_cfg_p.add_argument(
        "--email", metavar="EMAIL", help="Email address for alert notifications"
    )
    alerts_cfg_p.set_defaults(func=cmd_alerts_configure)

    # ── status ─────────────────────────────────────────────────────────────────
    status_p = subparsers.add_parser("status", help="Show current plan and billing status")
    status_p.add_argument("--api-url", default=None, help="Backend URL override")
    status_p.set_defaults(func=cmd_status)

    # ── upgrade ────────────────────────────────────────────────────────────────
    upgrade_p = subparsers.add_parser("upgrade", help="Subscribe or change plan (opens browser)")
    upgrade_p.add_argument(
        "--plan",
        choices=["solo", "team", "business"],
        default="solo",
        help="Plan to subscribe to (default: solo)",
    )
    upgrade_p.add_argument("--api-url", default=None, help="Backend URL override")
    upgrade_p.set_defaults(func=cmd_upgrade)

    # ── manage-billing ─────────────────────────────────────────────────────────
    manage_p = subparsers.add_parser(
        "manage-billing", help="Open LemonSqueezy billing portal (cancel, invoices)"
    )
    manage_p.add_argument("--api-url", default=None, help="Backend URL override")
    manage_p.set_defaults(func=cmd_manage_billing)

    # ── doctor ─────────────────────────────────────────────────────────────────
    doctor_p = subparsers.add_parser(
        "doctor", help="Diagnose setup issues (connectivity, libraries, config)"
    )
    _add_common_args(doctor_p)
    doctor_p.set_defaults(func=cmd_doctor)

    # ── audit ──────────────────────────────────────────────────────────────────
    audit_p = subparsers.add_parser("audit", help="One-shot health check with TUI report")
    _add_common_args(audit_p)
    audit_p.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Print machine-readable JSON summary",
    )
    audit_p.add_argument("--md", action="store_true", help="Print Markdown report")
    audit_p.add_argument(
        "--deep",
        "-d",
        action="store_true",
        help="(deprecated, no-op) Configuration analysis now runs by default",
    )
    audit_p.add_argument(
        "--no-config-checks",
        action="store_true",
        help="Skip Redis/Celery configuration analysis (e.g. restricted Redis)",
    )
    audit_p.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Max runtime in seconds (default 3s)",
    )
    audit_p.set_defaults(func=cmd_audit)

    # ── watch ──────────────────────────────────────────────────────────────────
    watch_p = subparsers.add_parser("watch", help="Interactive TUI loop (refreshes periodically)")
    _add_common_args(watch_p)
    watch_p.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Refresh interval in seconds (default 5)",
    )
    watch_p.add_argument("--deep", "-d", action="store_true", help="Include deep config checks")
    watch_p.set_defaults(func=cmd_watch)

    # ── agent ──────────────────────────────────────────────────────────────────
    agent_p = subparsers.add_parser("agent", help="Daemon loop: emits periodic heartbeats")
    _add_common_args(agent_p)
    agent_p.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Check interval in seconds (default 15)",
    )
    agent_p.add_argument(
        "--local",
        "-l",
        action="store_true",
        help="Local mode: only log, no API calls",
    )
    agent_p.add_argument("--token", help="API token (or set KANARI_API_KEY)")
    agent_p.set_defaults(func=cmd_agent)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        # No subcommand — print help
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
