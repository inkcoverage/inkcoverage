"""Create the owned local inspector review PDF; no customer files are used."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from test_pdf_inspect import fixture

target = ROOT/'tmp/inspect-pdf/estimator-inspector-sample.pdf'
target.parent.mkdir(parents=True, exist_ok=True)
fixture(target)
print(target)
