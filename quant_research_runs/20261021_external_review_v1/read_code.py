"""Bounded local source inspection."""
from pathlib import Path
import ast
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
names = set(sys.argv[2:])
for node in ast.parse(source).body:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
        print(ast.get_source_segment(source, node))

