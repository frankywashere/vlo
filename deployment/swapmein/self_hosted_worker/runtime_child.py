"""Linux parent-death protection for an owned foreground runtime child."""
from __future__ import annotations
import argparse
import ctypes
import os
from pathlib import Path
import signal
import sys

def protect(expected_parent: int):
    if sys.platform!='linux':raise RuntimeError('GPU runtime process ownership requires Linux')
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(1,signal.SIGKILL,0,0,0)!=0:raise RuntimeError('Parent-death protection could not be set')
    if os.getppid()!=expected_parent:raise RuntimeError('The runtime owner is no longer present')

def main():
    p=argparse.ArgumentParser();p.add_argument('--parent',required=True,type=int);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    if not a.command:raise SystemExit(1)
    protect(a.parent);os.execv(a.command[0],a.command)
if __name__=='__main__':main()
