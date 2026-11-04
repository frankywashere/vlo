"""Independent hard-cap guard for one exact owned Linux runtime process."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import time

def matches(record):
    try:return (Path('/proc')/str(record['pid'])/'cmdline').read_bytes().decode().split(chr(0))[:-1]==record['command']
    except (OSError,KeyError,ValueError):return False

def watch(record_path: Path,deadline: float,parent: int,interval: float=1):
    record=json.loads(record_path.read_text())
    while matches(record):
        if time.time()>=deadline or not (Path('/proc')/str(parent)).exists():
            try:os.killpg(record['pid'],signal.SIGTERM)
            except ProcessLookupError:return
            stop=time.monotonic()+10
            while matches(record) and time.monotonic()<stop:time.sleep(.1)
            if matches(record):
                try:os.killpg(record['pid'],signal.SIGKILL)
                except ProcessLookupError:pass
            return
        time.sleep(interval)

def main():
    p=argparse.ArgumentParser();p.add_argument('--record',type=Path,required=True);p.add_argument('--deadline',type=float,required=True);p.add_argument('--parent',type=int,required=True);a=p.parse_args();watch(a.record,a.deadline,a.parent)
if __name__=='__main__':main()
