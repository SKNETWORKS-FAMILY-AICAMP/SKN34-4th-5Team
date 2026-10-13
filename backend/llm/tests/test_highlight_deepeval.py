"""Compatibility entrypoint for relocated evaluation regressions."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from llm.v2.evaluation.test_highlight_deepeval import *

if __name__ == "__main__":
    unittest.main()
