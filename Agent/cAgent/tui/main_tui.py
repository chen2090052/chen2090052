import sys
from pathlib import Path

# 确保项目根目录（包含 cAgent 包的上级目录）在 sys.path 中
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cAgent.tui.app import run

if __name__ == "__main__":
    run()
