"""Vercel serverless entrypoint.

Vercel's @vercel/python runtime serves the module-level WSGI `app` object.
All routes are funneled here by vercel.json, so this single function backs the
whole Flask application (public site, portal, ops console, chat, static files).
"""
import os
import sys

# The project root is one level above this api/ directory.
# Add it to sys.path so `import nse` works correctly.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from nse import create_app

app = create_app(root=PROJECT_ROOT)
