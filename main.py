"""
Kevyt käynnistystiedosto: `python main.py` käynnistää sovelluksen.

Katso lisätietoja README.md:stä.
"""
import os
import sys

# Lisää projektin juuri polkuun
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.app import main

if __name__ == "__main__":
    sys.exit(main())
