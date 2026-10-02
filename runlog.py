#!/usr/bin/env python3
"""Console tee + crash capture for pipeline runs.

Wraps a pipeline call so everything it logs also lands in
output/runs/audit-<stamp>/console-<stamp>.log, and any crash (SystemExit
included) writes its full traceback there before re-raising — so a
scheduled run that dies leaves the failure on disk.
"""
import os
import traceback

import config


def run(stamp, fn):
    path = os.path.join(config.run_dir(stamp), f"console-{stamp}.log")
    fh = open(path, "a", encoding="utf-8")
    fh.write(f"=== run {config.now().isoformat(timespec='seconds')} ===\n")

    def log(*args):
        line = " ".join(str(a) for a in args)
        print(line)
        fh.write(line + "\n")
        fh.flush()

    try:
        return fn(log)
    except BaseException:
        fh.write(traceback.format_exc())
        fh.flush()
        raise
    finally:
        fh.close()
