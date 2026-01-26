#!/usr/bin/env python3
"""
Mini-K8s Orchestrator Entry Point.

Run with: uv run orchestrator.py

This is a thin wrapper that imports and runs the main application from src/.
"""

import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from main import main

if __name__ == "__main__":
    main()
