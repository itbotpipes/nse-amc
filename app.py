"""Root-level Vercel WSGI entrypoint."""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from flask import Flask as _Flask

# --- Minimal debug app to show filesystem before anything else ---
_debug_app = _Flask(__name__)

@_debug_app.route("/_debug")
def _debug():
    lines = [f"PROJECT_ROOT: {PROJECT_ROOT}"]
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Skip heavy dirs
        dirs[:] = [d for d in dirs if d not in ('.git', '_vendor', '__pycache__', '.venv', 'node_modules')]
        level = root.replace(PROJECT_ROOT, '').count(os.sep)
        indent = '  ' * level
        lines.append(f"{indent}{os.path.basename(root)}/")
        subindent = '  ' * (level + 1)
        for f in files:
            lines.append(f"{subindent}{f}")
    return "<pre>" + "\n".join(lines) + "</pre>", 200

# Now load the real app
from nse import create_app
app = create_app(root=PROJECT_ROOT)

# Attach the debug route to the real app too
app.add_url_rule("/_debug", "_debug", _debug)
