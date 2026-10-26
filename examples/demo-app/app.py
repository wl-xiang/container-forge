"""运行模式示例应用：仅使用标准库，容器内执行后以退出码 0 结束。"""
import platform
import socket
import sys
from datetime import datetime


def main() -> int:
    print("== container-forge demo app ==")
    print(f"time          : {datetime.now().isoformat()}")
    print(f"python        : {platform.python_version()}")
    print(f"platform      : {sys.platform} ({platform.machine()})")
    print(f"hostname      : {socket.gethostname()}")
    print("bootstrap     : 运行模式验证通过 (exit code 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
