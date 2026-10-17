#!/usr/bin/env python3
"""Test helper: burn CPU for N seconds of *CPU time*, printing progress every second.

Because it measures CPU time (not wall time), the job makes no progress while the scheduler has it
stopped with SIGSTOP -- which makes Round Robin time slicing easy to see.
Usage: cpu_burn.py SECONDS [LABEL]
"""
import sys
import time

total = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
label = sys.argv[2] if len(sys.argv) > 2 else "job"
start = time.process_time()
last = 0
while True:
    used = time.process_time() - start
    if used >= total:
        break
    if int(used) > last:
        last = int(used)
        print("%s: %ds of CPU used" % (label, last), flush=True)
print("%s: finished" % label, flush=True)
