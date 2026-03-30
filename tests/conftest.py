"""Pytest configuration for Stokowski tests."""

import sys
from pathlib import Path

# Add parent directory to path so we can import stokowski
sys.path.insert(0, str(Path(__file__).parent.parent))
