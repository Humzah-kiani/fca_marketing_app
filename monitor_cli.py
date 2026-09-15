"""
Run this on a schedule (cron, systemd timer, GitHub Action, Task Scheduler,
etc.) to check the tracked FCA Handbook/guidance pages for changes and flag
any previously generated posts that may now be out of date.

Example crontab entry (daily at 06:00):
    0 6 * * * cd /path/to/fca_marketing_app && /usr/bin/python3 monitor_cli.py >> /var/log/fca_monitor.log 2>&1

Exit code is always 0 on a successful check (even if changes were found);
check the output/log for CHANGE lines, or query the fca_change_log /
post_flags tables directly to drive your own email/Slack notifications.
"""
from db import init_db
from fca_monitor import check_for_updates, ensure_sections_seeded


def main():
    init_db()
    ensure_sections_seeded()
    changes = check_for_updates()

    if not changes:
        print("No FCA changes detected.")
        return

    for c in changes:
        if "error" in c:
            print(f"ERROR checking {c['section']} ({c.get('url', '')}): {c['error']}")
        else:
            print(
                f"CHANGE: {c['section']} ({c['url']}) — "
                f"{len(c.get('flagged_post_ids', []))} post(s) flagged for review "
                f"(change_log_id={c['change_log_id']})"
            )


if __name__ == "__main__":
    main()
