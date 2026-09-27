"""Điểm vào khi đóng gói bằng PyInstaller (không dùng import tương đối)."""

import multiprocessing
import sys

from dichyk.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
