# -*- coding: utf-8 -*-
"""Native entry point for the customer MockTC compatibility service."""
import os
import shutil
import sys
from pathlib import Path

from gunicorn.app.base import BaseApplication


def prepare_persistent_fixtures():
    runtime_root = Path(sys.argv[0]).resolve().parent
    bundled = runtime_root / "mocktc_app" / "fixtures"
    target = Path(os.environ.get("MOCKTC_FIXTURE_DIR") or
                  "/var/lib/xiaogang/mocktc/fixtures")
    target.mkdir(parents=True, exist_ok=True)
    if not bundled.is_dir():
        raise RuntimeError("bundled MockTC fixtures are unavailable")
    marker = target / ".bundled-fixtures-initialized"
    if marker.exists():
        return
    database = Path(os.environ.get("MOCKTC_DB_PATH") or target.parent / "mocktc.db")
    # 只在全新数据目录首次初始化。已有数据库或 fixture 状态说明
    # 这是持久化实例；此时缺失文件可能是管理员有意删除，不得从镜像复活。
    established = database.exists() or any(target.iterdir())
    if established:
        marker.touch(mode=0o600, exist_ok=True)
        return
    for source in bundled.iterdir():
        if source.is_file() and not (target / source.name).exists():
            shutil.copyfile(source, target / source.name)
    marker.touch(mode=0o600, exist_ok=True)


class MockTCApplication(BaseApplication):
    def load_config(self):
        options = {
            "bind": os.environ.get("MOCKTC_BIND", "0.0.0.0:18120"),
            "workers": int(os.environ.get("MOCKTC_WORKERS", "1")),
            "threads": int(os.environ.get("MOCKTC_THREADS", "4")),
            "timeout": int(os.environ.get("MOCKTC_TIMEOUT", "120")),
            "accesslog": "-", "errorlog": "-",
        }
        for key, value in options.items():
            self.cfg.set(key, value)

    def load(self):
        prepare_persistent_fixtures()
        from mocktc_app.app import app
        return app


if __name__ == "__main__":
    MockTCApplication().run()
