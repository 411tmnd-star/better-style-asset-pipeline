"""Run a Python script under an audit hook that records every file it opens; report any open outside allowed roots.

    python -I tools/audit_run.py <allowed_root> <script.py> [args...]

Used as evidence that catalogue/build.py never reads HUMAN_ENGINE (or any path outside the project and Python itself).
"""
import os
import runpy
import sys

norm = lambda p: os.path.normcase(os.path.abspath(p))
allowed = [norm(sys.argv[1])]
script = os.path.abspath(sys.argv[2])
opened = set()


def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        try:
            opened.add(norm(os.fsdecode(args[0])))
        except Exception:
            pass


sys.addaudithook(hook)
sys.argv = [script] + sys.argv[3:]
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as e:
    code = e.code or 0

py_roots = {norm(p) for p in (sys.prefix, sys.base_prefix, os.path.dirname(os.__file__))}
sysroot = norm(os.environ.get("SystemRoot", r"C:\Windows"))
outside = sorted(p for p in opened
                 if not any(p.startswith(r) for r in list(allowed) + list(py_roots) + [sysroot]))
print(f"AUDIT: {len(opened)} distinct paths opened; outside project/python/system: {len(outside)}")
for p in outside:
    print("  OUTSIDE:", p)
print("AUDIT: HUMAN_ENGINE touched:", any("human_engine" in p for p in opened))
sys.exit(code)
