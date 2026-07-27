"""Root-level Vercel WSGI entrypoint.

Placing this at the project root (alongside nse/) means Vercel's Python builder
bundles everything under nse/ (templates, static, blueprints) automatically via
the includeFiles config in vercel.json.
"""
import os
import sys

# Project root = directory containing this file (app.py is at the project root)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# Diagnostic: print resolved paths so Vercel logs show us exactly what's available
_tmpl = os.path.join(PROJECT_ROOT, "nse", "templates")
print(f"[startup] PROJECT_ROOT={PROJECT_ROOT}", file=sys.stderr)
print(f"[startup] template_folder={_tmpl}", file=sys.stderr)
print(f"[startup] templates_exist={os.path.exists(_tmpl)}", file=sys.stderr)
if os.path.exists(_tmpl):
    print(f"[startup] templates contents={os.listdir(_tmpl)}", file=sys.stderr)
else:
    # List what IS in /var/task to understand the container layout
    vt = "/var/task"
    if os.path.exists(vt):
        print(f"[startup] /var/task contents={os.listdir(vt)}", file=sys.stderr)

from nse import create_app

app = create_app(root=PROJECT_ROOT)
