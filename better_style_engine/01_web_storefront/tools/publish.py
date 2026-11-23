"""Command line for controlled publishing. Every live-affecting step needs a typed confirmation.

    python -I tools/publish.py check                                   validate the draft (no build, no files)
    python -I tools/publish.py preview [--provisional] [--serve]       local preview in deploy/site; --provisional shows DRAFT values
    python -I tools/publish.py candidate [--fast] [--dry-run]          build + scan + record a candidate (live site untouched)
    python -I tools/publish.py approve <id> --confirm "APPROVE <id>"   owner approval of one exact candidate
    python -I tools/publish.py publish <id> [--target local|vercel] --confirm PUBLISH [--dry-run]
    python -I tools/publish.py rollback [<id>] --confirm ROLLBACK [--live] [--dry-run]
    python -I tools/publish.py history

--target vercel is refused until config/deployment.json records the owner's B4 approval and a decision about the hosted
database. See publications/log.jsonl for the audit trail.
"""
import argparse
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publishing as pb  # noqa: E402
import serve_site  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    pv = sub.add_parser("preview"); pv.add_argument("--provisional", action="store_true"); pv.add_argument("--serve", action="store_true"); pv.add_argument("--port", type=int, default=8771)
    c = sub.add_parser("candidate"); c.add_argument("--fast", action="store_true"); c.add_argument("--dry-run", action="store_true")
    a = sub.add_parser("approve"); a.add_argument("id"); a.add_argument("--confirm", default="")
    pu = sub.add_parser("publish"); pu.add_argument("id"); pu.add_argument("--target", default="local"); pu.add_argument("--confirm", default=""); pu.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("rollback"); r.add_argument("id", nargs="?"); r.add_argument("--confirm", default=""); r.add_argument("--live", action="store_true"); r.add_argument("--dry-run", action="store_true")
    sub.add_parser("history")
    args = ap.parse_args(argv)
    p = pb.Project()
    try:
        if args.cmd == "check":
            v = pb.check(p)
            for w in v["warnings"]:
                print("WARNING:", w)
            for e in v["errors"]:
                print("ERROR:  ", e)
            print(f"check: {len(v['errors'])} error(s), {len(v['warnings'])} warning(s); draft sha256 {v['draft_sha256'][:16]}")
            return 0 if v["ok"] else 1
        if args.cmd == "preview":
            pb.preview(p, provisional=args.provisional)
            if args.serve:
                srv = serve_site.serve(p.site, args.port)
                print(f"preview: http://127.0.0.1:{args.port}/  (Ctrl+C to stop)" + ("  [DRAFT VALUES: never deployable]" if args.provisional else ""))
                try:
                    srv.serve_forever()
                except KeyboardInterrupt:
                    pass
            return 0
        if args.cmd == "candidate":
            res = pb.create_candidate(p, mode="fast" if args.fast else "full", dry_run=args.dry_run)
            return 0
        if args.cmd == "approve":
            pb.approve(p, args.id, args.confirm); return 0
        if args.cmd == "publish":
            pb.publish(p, args.id, target=args.target, confirm=args.confirm, dry_run=args.dry_run); return 0
        if args.cmd == "rollback":
            pb.rollback(p, args.id, confirm=args.confirm, live=args.live, dry_run=args.dry_run); return 0
        if args.cmd == "history":
            print(json.dumps(pb.history(p), indent=1)); return 0
    except pb.PublishError as e:
        print("REFUSED:", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
