"""Generate backdated commits with a varied, clustered GitHub contribution graph.

The graph is fully determined by --seed and --start, so the one-time backfill and
the daily automated run continue the same pattern with no state file.

Usage (inside a dedicated repo pushed to your account):
    python humanlike_commits.py --seed 21 --start 2025-09-27 --preview   # preview.png only
    python humanlike_commits.py --seed 21 --start 2025-09-27             # backfill up to yesterday
    python humanlike_commits.py --seed 21 --start 2025-09-27 --today     # one day (for automation)

Keep --seed and --start fixed forever. The commit author email must match a
verified email on your GitHub account. Re-running is safe: days already in the
log are skipped.
"""

import argparse
import math
import os
import random
import subprocess
from datetime import date, datetime, timedelta

LOG_FILE = "activity.log"
PREVIEW_FILE = "preview.png"

# Activity odds by weekday (Mon=0 ... Sun=6). Weekends are rare but not zero.
ACTIVE_PROBABILITY_BY_WEEKDAY = [0.96, 0.97, 0.96, 0.95, 0.92, 0.10, 0.06]

# Weekly intensity follows a bounded random walk, which creates dark and light regions.
INTENSITY_MIN, INTENSITY_MAX = 0.25, 1.0
INTENSITY_DRIFT = 0.30
INTENSITY_SPIKE_PROBABILITY = 0.10   # occasional crunch week
GAP_WEEK_PROBABILITY = 0.03          # fully quiet week, roughly 1-2 per year
PEAK_COMMITS_PER_DAY = 6
DAY_NOISE_SIGMA = 0.60               # per-day jitter on top of the weekly level

WORK_START_HOUR, WORK_END_HOUR = 9, 18
LATE_NIGHT_PROBABILITY = 0.12

# A daily run slightly after midnight still counts as the previous day.
DAILY_RUN_GRACE = timedelta(hours=2)


def build_weeks(seed: int, week_count: int) -> list:
    """Return [(intensity, is_gap_week)]. Fixed RNG calls per week keep it stable as it grows."""
    rng = random.Random(seed)
    level = rng.uniform(0.3, 0.7)
    weeks = []
    for _ in range(week_count):
        level = min(INTENSITY_MAX, max(INTENSITY_MIN, level + rng.uniform(-INTENSITY_DRIFT, INTENSITY_DRIFT)))
        is_spike = rng.random() < INTENSITY_SPIKE_PROBABILITY
        is_gap = rng.random() < GAP_WEEK_PROBABILITY
        weeks.append((INTENSITY_MAX if is_spike else level, is_gap))
    return weeks


def commits_for_day(seed: int, day: date, intensity: float, is_gap: bool) -> int:
    rng = random.Random(f"{seed}:{day.isoformat()}")
    if is_gap or rng.random() >= ACTIVE_PROBABILITY_BY_WEEKDAY[day.weekday()]:
        return 0
    jittered = intensity * PEAK_COMMITS_PER_DAY * math.exp(rng.gauss(0, DAY_NOISE_SIGMA))
    return max(1, min(PEAK_COMMITS_PER_DAY + 4, round(jittered)))


def plan_range(seed: int, start: date, first_day: date, last_day: date) -> dict:
    """Return {date: commit_count} for first_day..last_day, anchored to start."""
    weeks = build_weeks(seed, (last_day - start).days // 7 + 1)
    plan, day = {}, first_day
    while day <= last_day:
        intensity, is_gap = weeks[(day - start).days // 7]
        count = commits_for_day(seed, day, intensity, is_gap)
        if count:
            plan[day] = count
        day += timedelta(days=1)
    return plan


def pick_commit_times(seed: int, day: date, count: int) -> list:
    """Cluster commits into one session, never spilling past midnight."""
    rng = random.Random(f"{seed}:{day.isoformat()}:times")
    is_late = rng.random() < LATE_NIGHT_PROBABILITY
    hour = rng.randint(20, 22) if is_late else rng.randint(WORK_START_HOUR, WORK_END_HOUR - 2)
    cursor = datetime(day.year, day.month, day.day, hour, rng.randint(0, 59))
    day_end = datetime(day.year, day.month, day.day, 23, 59, 59)

    times = []
    for _ in range(count):
        step = timedelta(minutes=rng.randint(3, 40), seconds=rng.randint(0, 59))
        cursor = min(day_end, cursor + step)
        times.append(cursor)
    return times


def already_committed(day: date) -> bool:
    if not os.path.exists(LOG_FILE):
        return False
    with open(LOG_FILE, encoding="utf-8") as log:
        return any(line.startswith(day.isoformat()) for line in log)


def make_commit(timestamp: datetime) -> None:
    with open(LOG_FILE, "a", encoding="utf-8") as log:
        log.write(f"{timestamp.isoformat()}\n")

    iso_time = timestamp.strftime("%Y-%m-%dT%H:%M:%S")
    env = {**os.environ, "GIT_AUTHOR_DATE": iso_time, "GIT_COMMITTER_DATE": iso_time}
    subprocess.run(["git", "add", LOG_FILE], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"update {timestamp:%Y-%m-%d %H:%M}"],
                   check=True, env=env)


def write_commits(seed: int, plan: dict) -> None:
    written = 0
    for day in sorted(plan):
        if already_committed(day):
            continue
        for timestamp in pick_commit_times(seed, day, plan[day]):
            make_commit(timestamp)
            written += 1
    print(f"Created {written} commits.")


def render_preview(plan: dict, start: date) -> None:
    """Draw a GitHub-style heatmap of the plan (requires matplotlib)."""
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    github_dark = ListedColormap(["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"])
    grid_start = start - timedelta(days=(start.weekday() + 1) % 7)  # align to Sunday
    week_count = (date.today() - grid_start).days // 7 + 1

    # GitHub shades by quartile of your own non-zero days, so mirror that.
    counts = sorted(plan.values())
    cutoffs = [counts[len(counts) * q // 4] for q in (1, 2, 3)] if counts else [1, 1, 1]
    grid = [[0] * week_count for _ in range(7)]
    for day, count in plan.items():
        offset = (day - grid_start).days
        grid[offset % 7][offset // 7] = 1 + sum(count >= c for c in cutoffs)

    fig, ax = plt.subplots(figsize=(14, 2.4), facecolor="#0d1117")
    ax.imshow(grid, cmap=github_dark, vmin=0, vmax=4, aspect="equal")
    ax.set_xticks([]), ax.set_yticks([])
    ax.set_title(f"{sum(plan.values())} contributions (preview)", color="white", loc="left")
    fig.savefig(PREVIEW_FILE, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"Preview saved to {PREVIEW_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, required=True, help="pattern seed; never change it")
    parser.add_argument("--start", type=date.fromisoformat, required=True,
                        help="first day of the pattern, YYYY-MM-DD; never change it")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="render preview.png only")
    mode.add_argument("--today", action="store_true", help="commit today's share only")
    args = parser.parse_args()

    today = date.today()
    if args.start > today:
        parser.error("--start cannot be in the future")

    if args.preview:
        render_preview(plan_range(args.seed, args.start, args.start, today), args.start)
    elif args.today:
        target = (datetime.now() - DAILY_RUN_GRACE).date()
        write_commits(args.seed, plan_range(args.seed, args.start, target, target))
    else:
        yesterday = today - timedelta(days=1)
        write_commits(args.seed, plan_range(args.seed, args.start, args.start, yesterday))


if __name__ == "__main__":
    main()
