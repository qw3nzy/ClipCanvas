"""ClipCanvas server entry point; shared implementation lives in clipcanvas_server."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from clipcanvas_server import *

if __name__ == '__main__':
    serve()
