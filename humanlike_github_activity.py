"""Occasionally open, close and merge issues and PRs, alongside humanlike_commits.py.

Decisions are fully determined by --seed and the date, like the commit script.
Issues and PRs can't be backdated, so this only ever acts on the current day.

Usage (inside the repo, with gh authenticated as you, not as github-actions[bot]):
    python humanlike_github_activity.py --seed 21 --dry-run   # show the next 30 days
    python humanlike_github_activity.py --seed 21             # act on today
    python humanlike_github_activity.py --seed 21 --force     # open one of each now (testing)
"""

import argparse
import json
import random
import subprocess
from datetime import date, datetime, timedelta

LABEL = "auto"
NOTES_FILE = "NOTES.md"

# Odds by weekday (Mon=0 ... Sun=6) of opening one issue / one PR that day.
ISSUE_PROBABILITY_BY_WEEKDAY = [0.20, 0.22, 0.20, 0.20, 0.18, 0.03, 0.03]
PR_PROBABILITY_BY_WEEKDAY = [0.15, 0.17, 0.15, 0.15, 0.13, 0.02, 0.02]

# Days an item stays open before it is closed or merged.
ISSUE_HOLD_DAYS = (1, 6)
PR_HOLD_DAYS = (1, 3)
ISSUE_CLOSE_PROBABILITY = 0.70   # the rest stay open indefinitely

# Same as humanlike_commits.py: a run slightly after midnight still counts as the previous day.
DAILY_RUN_GRACE = timedelta(hours=2)

ISSUES = [
    ("Split activity.log by year", "The log keeps growing. Splitting it per year would keep diffs small."),
    ("Add a short README", "Explain what this repo is for and how the log is written."),
    ("Document log line format", "Lines are ISO timestamps. Write that down somewhere."),
    ("Check timezone handling around DST", "Make sure entries near the DST switch land on the right day."),
    ("Add a .editorconfig", "Keep line endings and final newlines consistent."),
    ("Monthly summary in NOTES.md", "A one-line summary per month would make the history easier to skim."),
    ("Pin action versions to SHAs", "Workflows use tags. Pinning to commit SHAs is safer."),
    ("Workflow should fail loudly on push errors", "If the push fails the run should be clearly red."),
    ("Consider a weekly digest", "Collect the week's entries into a short digest."),
    ("Clean up old branches", "A few merged branches are still around."),
    ("Add a license", "Pick a license so the repo terms are clear."),
    ("Validate log entries are sorted", "Entries should always be in chronological order."),
    ("Review workflow permissions", "Check each workflow only has the permissions it needs."),
    ("Tidy NOTES.md headings", "Headings are inconsistent between months."),
    ("Track yearly totals", "Keep a running total per year in the notes."),
]

PRS = [
    ("Update notes", "Add a note for today."),
    ("Tidy notes formatting", "Small formatting cleanup in the notes."),
    ("Add monthly summary line", "Summarise the month so far."),
    ("Fix typo in notes", "Minor wording fix."),
    ("Record workflow tweak in notes", "Note a small change to the workflow setup."),
    ("Add reminder to notes", "Add a reminder for an open item."),
    ("Note timezone check", "Record that the timezone handling was checked."),
    ("Reorganise notes section", "Group related notes together."),
]


def opens_today(seed: int, day: date, kind: str, odds: list) -> bool:
    return random.Random(f"{seed}:{day.isoformat()}:{kind}").random() < odds[day.weekday()]


def pick(seed: int, day: date, kind: str, pool: list) -> tuple:
    return random.Random(f"{seed}:{day.isoformat()}:{kind}:pick").choice(pool)


def marker(day: date) -> str:
    return f"<!-- {LABEL}:{day.isoformat()} -->"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def git(*args: str) -> None:
    subprocess.run(["git", *args], check=True)


def list_items(kind: str, state: str) -> list:
    fields = "number,body,createdAt"
    return json.loads(gh(kind, "list", "--label", LABEL, "--state", state, "--limit", "200", "--json", fields))


def already_opened(kind: str, day: date) -> bool:
    return any(marker(day) in item["body"] for item in list_items(kind, "all"))


def open_issue(seed: int, day: date) -> None:
    if already_opened("issue", day):
        return
    title, body = pick(seed, day, "issue", ISSUES)
    gh("issue", "create", "--title", title, "--body", f"{body}\n\n{marker(day)}", "--label", LABEL)
    print(f"Opened issue: {title}")


def open_pr(seed: int, day: date) -> None:
    if already_opened("pr", day):
        return
    title, body = pick(seed, day, "pr", PRS)
    branch = f"chore/{day.isoformat()}"
    git("checkout", "-q", "-b", branch)
    with open(NOTES_FILE, "a", encoding="utf-8") as notes:
        notes.write(f"- {day.isoformat()}: {body}\n")
    git("add", NOTES_FILE)
    git("commit", "-q", "-m", title)
    git("push", "-q", "-u", "origin", branch)
    git("checkout", "-q", "-")
    gh("pr", "create", "--title", title, "--body", f"{body}\n\n{marker(day)}",
       "--label", LABEL, "--base", "main", "--head", branch)
    print(f"Opened PR: {title}")


def age_in_days(item: dict, today: date) -> int:
    created = datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00")).astimezone()
    return (today - created.date()).days


def mature_items(seed: int, today: date) -> None:
    for issue in list_items("issue", "open"):
        rng = random.Random(f"{seed}:issue:{issue['number']}")
        hold, closes = rng.randint(*ISSUE_HOLD_DAYS), rng.random() < ISSUE_CLOSE_PROBABILITY
        if closes and age_in_days(issue, today) >= hold:
            gh("issue", "close", str(issue["number"]), "--reason", "completed")
            print(f"Closed issue #{issue['number']}")

    for pr in list_items("pr", "open"):
        hold = random.Random(f"{seed}:pr:{pr['number']}").randint(*PR_HOLD_DAYS)
        if age_in_days(pr, today) >= hold:
            gh("pr", "merge", str(pr["number"]), "--squash", "--delete-branch")
            print(f"Merged PR #{pr['number']}")


def ensure_label() -> None:
    gh("label", "create", LABEL, "--color", "ededed", "--force")


def dry_run(seed: int, first_day: date, days: int) -> None:
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        actions = []
        if opens_today(seed, day, "issue", ISSUE_PROBABILITY_BY_WEEKDAY):
            actions.append(f"issue '{pick(seed, day, 'issue', ISSUES)[0]}'")
        if opens_today(seed, day, "pr", PR_PROBABILITY_BY_WEEKDAY):
            actions.append(f"PR '{pick(seed, day, 'pr', PRS)[0]}'")
        if actions:
            print(f"{day} {day:%a}: {', '.join(actions)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, required=True, help="pattern seed; never change it")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the next 30 days and exit")
    mode.add_argument("--force", action="store_true", help="open one issue and one PR today")
    args = parser.parse_args()

    today = (datetime.now() - DAILY_RUN_GRACE).date()
    if args.dry_run:
        dry_run(args.seed, today, 30)
        return

    ensure_label()
    mature_items(args.seed, today)
    if args.force or opens_today(args.seed, today, "issue", ISSUE_PROBABILITY_BY_WEEKDAY):
        open_issue(args.seed, today)
    if args.force or opens_today(args.seed, today, "pr", PR_PROBABILITY_BY_WEEKDAY):
        open_pr(args.seed, today)


if __name__ == "__main__":
    main()
