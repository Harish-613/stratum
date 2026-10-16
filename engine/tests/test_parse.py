from stratum_engine.parse import parse_python

SAMPLE = b'''
import os
import json, sys
from pathlib import Path

class Graph:
    pass

def load(path):
    return Path(path).read_text()

def main():
    pass
'''


def test_finds_imports():
    result = parse_python(SAMPLE)
    assert result["imports"] == ["os", "json", "sys", "pathlib"]


def test_finds_functions_and_classes():
    result = parse_python(SAMPLE)
    assert result["functions"] == ["load", "main"]
    assert result["classes"] == ["Graph"]


def test_empty_file():
    assert parse_python(b"") == {"imports": [], "functions": [], "classes": []}