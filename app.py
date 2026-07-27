"""Root-level Vercel WSGI entrypoint.

Placing this at the project root means Vercel's Python builder automatically
includes all sibling files and directories (nse/, nse/templates/, nse/static/)
in the serverless function bundle — no includeFiles config required.
"""
import os
import sys

# Project root is the directory containing this file.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from nse import create_app

app = create_app(root=PROJECT_ROOT)
