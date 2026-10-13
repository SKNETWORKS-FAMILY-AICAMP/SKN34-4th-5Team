"""Compatibility entrypoint; implementation lives in llm.v2.evaluation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from llm.v2.evaluation.eval_ragas import *

if __name__ == "__main__":
    sys.exit(main())
