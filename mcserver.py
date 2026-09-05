#!/usr/bin/env python3
"""入口脚本：mcserver <子命令>

用法示例：
  python mcserver.py list
  python mcserver.py deploy --version 1.21.1
  python mcserver.py start --version 1.21.1
"""
import sys

from server_manager.cli import main

if __name__ == "__main__":
    sys.exit(main())
