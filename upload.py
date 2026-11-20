"""Upload Baby Sensory episodes (long video + thumbnail + Short) to the BABY channel.

Reuses the Google Cloud OAuth client of the Ball League project (../ball-league/client_secret.json)
but keeps its OWN token (token_baby.json) and its OWN channel id (config.json), so the channels
never mix.

    python upload.py --auth          # one-time sign-in: choose the baby channel, confirm, saved in config.json
    python upload.py --dry-run       # show what would be uploaded, upload nothing
    python upload.py --unattended    # scheduled runs: never opens a browser

Every video is marked "made for kids" (required by YouTube/COPPA for content aimed at children).
Never uploads a duplicate: same file hash or an episode number already on the channel => skipped.
"""
import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
CONFIG = ROOT / "config.json"
TOKEN = ROOT / "token_baby.json"
CLIENT = ROOT.parent / "ball-league" / "client_secret.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
OTHER_CHANNELS = {"UChn-MGHHRkCYVVFn_5tpZYw": "Ball League", "UCyTVew7g9eSeeyRicdbw1Ig": "Golden Tides"}
REAUTH = "BABY CHANNEL AUTH MISSING/EXPIRED: run 'python upload.py --auth' and pick the baby channel."


def config():
    return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}


def service(interactive):
    creds = Credentials.from_authorized_user_file(str(TOKEN)) if TOKEN.exists() else None
    if not creds or not creds.valid:
        ok = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                ok = True
            except Exception as exc:
                print(f"refresh failed: {exc}")
        if not ok:
            if not interactive:
                raise SystemExit(REAUTH)
            creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES).run_local_server(port=0)
    yt = build("youtube", "v3", credentials=creds)
    me = yt.channels().list(part="id,snippet", mine=True).execute()["items"][0]
    cfg = config()
    if me["id"] in OTHER_CHANNELS:
        TOKEN.unlink(missing_ok=True)
        raise SystemExit(f"Signed in to {OTHER_CHANNELS[me['id']]}, not the baby channel. Run --auth again and pick the baby channel.")
    if cfg.get("channel_id") and me["id"] != cfg["channel_id"]:
        TOKEN.unlink(missing_ok=True)
        raise SystemExit(f"Signed in to {me['snippet']['title']} ({me['id']}), expected {cfg['channel_id']}. "
                         "Run --auth again choosing the baby channel.")
    if not cfg.get("channel_id"):
        if not interactive:
            raise SystemExit(REAUTH)
        ans = input(f"Use channel '{me['snippet']['title']}' ({me['id']}) for Baby Sensory uploads? [y/N] ")
        if ans.strip().lower() != "y":
            raise SystemExit("Not saved. Run --auth again and pick the baby channel.")
        CONFIG.write_text(json.dumps({"channel_id": me["id"], "channel_name": me["snippet"]["title"]}, indent=2),
                          encoding="utf-8")
    TOKEN.write_text(creds.to_json(), encoding="utf-8")      # saved only after the channel check passed
    return yt, me


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def channel_episodes(yt, channel_id):
    ch = yt.channels().list(part="contentDetails", id=channel_id).execute()["items"][0]
    pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    found, tok = {}, None
    while True:
        r = yt.playlistItems().list(part="snippet", playlistId=pl, maxResults=50, pageToken=tok).execute()
        for it in r.get("items", []):
            sn = it["snippet"]
            m = re.search(r"\bEpisode (\d+)\b", sn.get("description", ""))
            if m and "#shorts" not in sn.get("title", ""):
                found[int(m.group(1))] = sn["resourceId"]["videoId"]
        tok = r.get("nextPageToken")
        if not tok:
            return found


def insert(yt, path, title, desc, tags, cat, publish_at=None):
    """publish_at (aware datetime): upload as private and let YouTube make it public at that time,
    so the video goes out even if this PC is off then."""
    status = {"privacyStatus": "public", "selfDeclaredMadeForKids": True, "containsSyntheticMedia": False}
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    body = {"snippet": {"title": title, "description": desc, "tags": tags, "categoryId": cat}, "status": status}
    media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True, chunksize=16 << 20)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    res = None
    while res is None:
        _, res = req.next_chunk()
    return res


def check_previous(yt):
    """Warn (in daily.log) when YouTube rejected or removed an episode we uploaded, e.g. >15 min on an unverified channel."""
    ups = sorted(OUT.glob("ep_*/uploaded.json"))[-5:]
    for up in ups:
        info = json.loads(up.read_text(encoding="utf-8"))
        items = yt.videos().list(part="status", id=info["id"]).execute().get("items", [])
        st = items[0]["status"] if items else {}
        if not items or st.get("uploadStatus") in ("rejected", "failed", "deleted"):
            why = st.get("rejectionReason") or st.get("failureReason") or "not on the channel any more"
            print(f"LONG VIDEO REJECTED {up.parent.name} ({info['id']}): {why}. "
                  f"Fix, then: python upload.py --reupload-long {int(up.parent.name[3:])}")


def reupload_long(yt, d):
    info = json.loads((d / "uploaded.json").read_text(encoding="utf-8"))
    meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    res = insert(yt, d / "video.mp4", meta["title"], meta["description"], meta["tags"], meta["categoryId"])
    info.update({"id": res["id"], "url": f"https://youtu.be/{res['id']}", "sha256": sha256(d / "video.mp4"),
                 "reuploaded": datetime.datetime.now().isoformat()})
    try:
        yt.thumbnails().set(videoId=res["id"], media_body=MediaFileUpload(str(d / "thumbnail.png"))).execute()
        info["thumbnail"] = True
    except Exception as exc:
        print(f"thumbnail not set ({exc.__class__.__name__}): verify the channel at youtube.com/verify")
    (d / "uploaded.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(d.name, "long ->", info["url"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", action="store_true")
    ap.add_argument("--unattended", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--at", default="06:00", help="local time each episode goes public (YouTube publishes it)")
    ap.add_argument("--ahead-days", type=int, default=7, help="schedule at most this many days ahead")
    ap.add_argument("--reupload-long", type=int, metavar="EP",
                    help="upload only the long video of episode EP again (after YouTube rejected it)")
    a = ap.parse_args()
    yt, me = service(interactive=not a.unattended)
    if a.auth:
        print(f"OK: signed in to {me['snippet']['title']} ({me['id']})")
        return
    check_previous(yt)
    if a.reupload_long:
        reupload_long(yt, OUT / f"ep_{a.reupload_long:03d}")
        return
    try:
        run_schedule(yt, me, a)
    except HttpError as exc:
        if "quota" in str(exc).lower():
            # daily API quota (shared with Ball League + music) is spent: not a failure, the next run continues
            print("QUOTA EXCEEDED for today: the rest goes up on the next run (quota resets 08:00 Lisbon).")
            return
        raise


def run_schedule(yt, me, a):
    on_channel = channel_episodes(yt, me["id"])
    hashes = set()
    for up in OUT.glob("ep_*/uploaded.json"):
        hashes.add(json.loads(up.read_text(encoding="utf-8")).get("sha256"))
    todo = [d for d in sorted(OUT.glob("ep_*")) if (d / "video.mp4").exists()
            and not (d / "uploaded.json").exists()]
    # finish a Short that failed after its long video went up
    half = [d for d in sorted(OUT.glob("ep_*")) if (d / "uploaded.json").exists()
            and not json.loads((d / "uploaded.json").read_text(encoding="utf-8")).get("short_id")]
    horizon = now() + datetime.timedelta(days=a.ahead_days)
    for d in todo:
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        h = sha256(d / "video.mp4")
        if meta["episode"] in on_channel or h in hashes:
            print(f"SKIPPED DUPLICATE {d.name}: already on the channel.")
            continue
        slot = next_slot(a.at)
        if slot > horizon:
            print(f"buffer full: videos scheduled up to {a.ahead_days} days ahead; {d.name} waits")
            break
        print(f"{'WOULD SCHEDULE' if a.dry_run else 'scheduling'} {d.name} for {slot:%a %d %b %H:%M}: \"{meta['title']}\"")
        if a.dry_run:
            continue
        res = insert(yt, d / "video.mp4", meta["title"], meta["description"], meta["tags"], meta["categoryId"], slot)
        info = {"id": res["id"], "url": f"https://youtu.be/{res['id']}", "sha256": h,
                "uploaded": datetime.datetime.now().isoformat(), "publishAt": slot.isoformat()}
        try:
            yt.thumbnails().set(videoId=res["id"], media_body=MediaFileUpload(str(d / "thumbnail.png"))).execute()
            info["thumbnail"] = True
        except Exception as exc:          # custom thumbnails need a phone-verified channel
            print(f"thumbnail not set ({exc.__class__.__name__}): verify the channel at youtube.com/verify")
        (d / "uploaded.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        print(d.name, "->", info["url"], "goes public", f"{slot:%d %b %H:%M}")
        half.append(d)
        upload_short(yt, d, a)
        half.remove(d)
    for d in half:
        upload_short(yt, d, a)
    cleanup()


def upload_short(yt, d, a):
    if a.dry_run or not (d / "short.mp4").exists():
        return
    info = json.loads((d / "uploaded.json").read_text(encoding="utf-8"))
    meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    slot = datetime.datetime.fromisoformat(info["publishAt"]) if info.get("publishAt") else None
    if slot and slot <= now() + datetime.timedelta(minutes=20):
        slot = None                                        # its long video is already out: publish now
    desc = f"Full video ({meta['minutes']} min): {info['url']}\nEpisode {meta['episode']} short\n#babysensory #babyvideo #shorts"
    res = insert(yt, d / "short.mp4", meta["short_title"], desc, meta["tags"][:8] + ["shorts"], meta["categoryId"], slot)
    info["short_id"] = res["id"]
    (d / "uploaded.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(d.name, "short ->", f"https://youtube.com/shorts/{res['id']}", "scheduled" if slot else "public now")


def now():
    return datetime.datetime.now().astimezone()


def taken_dates():
    """Local dates that already have (or will have) an episode go public."""
    out = set()
    for up in OUT.glob("ep_*/uploaded.json"):
        info = json.loads(up.read_text(encoding="utf-8"))
        when = info.get("publishAt") or info.get("uploaded")
        out.add(datetime.datetime.fromisoformat(when).astimezone().date())
    return out


def next_slot(hhmm):
    """First day at hh:mm local that is >= 30 min from now and has no episode yet."""
    hh, mm = map(int, hhmm.split(":"))
    slot = now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    if slot < now() + datetime.timedelta(minutes=30):
        slot += datetime.timedelta(days=1)
    taken = taken_dates()
    while slot.date() in taken:
        slot += datetime.timedelta(days=1)
    return slot


def cleanup(keep_days=3):
    """Delete the big video files of episodes that went public more than keep_days ago (metadata stays)."""
    limit = now() - datetime.timedelta(days=keep_days)
    for up in OUT.glob("ep_*/uploaded.json"):
        info = json.loads(up.read_text(encoding="utf-8"))
        when = datetime.datetime.fromisoformat(info.get("publishAt") or info["uploaded"]).astimezone()
        if when < limit and info.get("short_id"):
            for f in ("video.mp4", "short.mp4"):
                (up.parent / f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
