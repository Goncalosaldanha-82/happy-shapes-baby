"""Daily run (GitHub Actions): keep the YouTube schedule of Happy Shapes Baby filled.

Each run renders the next episode(s) and uploads them as private videos with publishAt 06:00 Lisbon
on the next free days, so YouTube itself publishes them on time. It stops when episodes are scheduled
--ahead-days ahead, after --max-renders renders, or when the shared daily API quota runs out.

Episodes are deterministic (same number -> same video), so an episode that was rendered but not
uploaded (quota, crash) is simply rendered again with the same number on the next run.
The record of what is scheduled lives in output/ep_NNN/uploaded.json, committed by the workflow.

    python daily.py                      # normal run
    python daily.py --max-renders 2      # catch up faster
"""
import argparse
import datetime
import sys
from types import SimpleNamespace

from googleapiclient.errors import HttpError

import make_video as mv
import upload as up


def next_episode():
    """Lowest episode that exists but never went up, else the next new number."""
    eps = [d for d in mv.episodes() if (d / "metadata.json").exists()]
    for d in eps:
        if not (d / "uploaded.json").exists() and not (d / "video.mp4").exists():
            return int(d.name[3:])
    return max((int(d.name[3:]) for d in eps), default=0) + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="06:00")
    ap.add_argument("--ahead-days", type=int, default=7)
    ap.add_argument("--max-renders", type=int, default=1)
    ap.add_argument("--minutes", type=float, default=14.5)
    a = ap.parse_args()
    opts = SimpleNamespace(at=a.at, ahead_days=a.ahead_days, dry_run=False)
    yt, me = up.service(interactive=False)
    up.check_previous(yt)
    try:
        for d in mv.episodes():                             # Short still missing and its file gone: render again
            info = d / "uploaded.json"
            if info.exists() and "short_id" not in info.read_text(encoding="utf-8") and not (d / "short.mp4").exists():
                print(f"re-rendering {d.name} to upload its Short", flush=True)
                mv.render(int(d.name[3:]), a.minutes)
        up.run_schedule(yt, me, opts)                       # anything already rendered / Shorts left over
        for _ in range(a.max_renders):
            slot = up.next_slot(a.at)
            if slot > up.now() + datetime.timedelta(days=a.ahead_days):
                print(f"schedule full up to {slot - datetime.timedelta(days=1):%a %d %b}: nothing to render")
                break
            ep = next_episode()
            print(f"rendering episode {ep} for {slot:%a %d %b %H:%M}", flush=True)
            mv.render(ep, a.minutes)
            up.run_schedule(yt, me, opts)
    except HttpError as exc:
        if "quota" in str(exc).lower():
            print("QUOTA EXCEEDED for today: continues on the next run.")
            return
        raise
    dates = sorted(up.taken_dates())
    print("scheduled/published days:", ", ".join(f"{d:%d %b}" for d in dates[-8:]))


if __name__ == "__main__":
    sys.exit(main())
