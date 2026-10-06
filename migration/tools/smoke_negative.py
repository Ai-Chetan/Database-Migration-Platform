#!/usr/bin/env python3
r"""WHERE: E:\Project\Migration\migration\tools\smoke_negative.py
Shortcut for:  python tools\smoke_e2e.py --scenario negative"""
import os, runpy, sys
sys.argv = [sys.argv[0], "--scenario", "negative"]
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke_e2e.py"), run_name="__main__")
