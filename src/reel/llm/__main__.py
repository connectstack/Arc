"""``python -m reel.llm [style] [--compact] [--schema]`` - print the exact prompt a model gets."""

from __future__ import annotations

import sys

from reel.llm.prompt import main

raise SystemExit(main(sys.argv[1:]))
