"""Code highlighting for the major languages (v0.8): the Studio's editor bundle
and the chat's code viewer, both loaded in Node the way the page loads them."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def node(code: str):
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True, cwd=ROOT).stdout
    return json.loads(out)


def editor_file() -> str:
    m = re.search(r"EDITOR_SCRIPT = '/static/(vendor/codemirror-[\w.\-]+\.min\.js)'", (ROOT / "web/app.js").read_text())
    assert m and (ROOT / "web" / m.group(1)).is_file()
    return "web/" + m.group(1)


EDITOR = """
const vm = require('vm'), fs = require('fs');
const ctx = {}; ctx.window = ctx; ctx.navigator = { userAgent: 'node', platform: 'x' }; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(%s, 'utf8'), ctx);
const E = ctx.OptimuxEditor;
"""

NAMES = {
    "main.c": "C", "util.h": "C", "app.cpp": "C++", "app.cc": "C++", "app.hpp": "C++", "sketch.ino": "C++",
    "Main.java": "Java", "lib.rs": "Rust", "main.go": "Go", "index.php": "PHP",
    "boot.s": "Assembly", "boot.S": "Assembly", "boot.asm": "Assembly",
    "App.cs": "C#", "App.kt": "Kotlin", "App.scala": "Scala", "view.m": "Objective-C", "app.dart": "Dart",
    "app.swift": "Swift", "app.rb": "Ruby", "app.lua": "Lua", "app.pl": "Perl", "stats.r": "R",
    "run.ps1": "PowerShell", "Main.hs": "Haskell", "top.v": "Verilog", "top.sv": "SystemVerilog", "top.vhd": "VHDL",
    "CMakeLists.txt": "CMake", "build.cmake": "CMake", "fix.diff": "Diff", "fix.patch": "Diff", "api.proto": "Protocol Buffers",
    "Makefile": "Makefile", "app.py": "Python", "app.ts": "TypeScript",
}


def test_the_editor_knows_the_major_languages():
    got = node(EDITOR % json.dumps(editor_file()) + f"""
        const out = {{}};
        for (const p of {json.dumps(list(NAMES))}) out[p] = [E.languageName('src/' + p), !!E.languageFor('src/' + p)];
        console.log(JSON.stringify(out));""")
    assert {p: v[0] for p, v in got.items()} == NAMES
    assert all(v[1] for v in got.values()), [p for p, v in got.items() if not v[1]]     # each one really highlights
    assert node(EDITOR % json.dumps(editor_file()) + "console.log(JSON.stringify([E.languageName('notes.xyz'), E.languageFor('notes.xyz')]))") == ["Plain text", None]


def test_the_editor_parses_c_java_and_rust():
    got = node(EDITOR % json.dumps(editor_file()) + """
        const tree = (p, src) => String(E.languageFor(p).language.parser.parse(src));
        console.log(JSON.stringify([tree('a.c', 'int main(void) { return 0; }'),
                                    tree('A.java', 'class A { int f() { return 1; } }'),
                                    tree('a.rs', 'fn main() { let x = 1; }')]));""")
    assert "FunctionDefinition" in got[0] and "ClassDeclaration" in got[1] and "FunctionItem" in got[2]


def viewer_files() -> list[str]:
    files = re.findall(r'<script src="/static/(vendor/highlight-[\w.\-]+\.js)"', (ROOT / "index.html").read_text())
    assert len(files) == 2 and all((ROOT / "web" / f).is_file() for f in files), files   # the library, then more languages
    return ["web/" + f for f in files]


VIEWER = """
const vm = require('vm'), fs = require('fs');
const ctx = {}; ctx.window = ctx; ctx.self = ctx; vm.createContext(ctx);
for (const f of %s) vm.runInContext(fs.readFileSync(f, 'utf8'), ctx);
const hljs = ctx.hljs;
"""


def test_the_chat_viewer_highlights_assembly_and_more():
    got = node(VIEWER % json.dumps(viewer_files()) + """
        const has = (l) => !!hljs.getLanguage(l);
        console.log(JSON.stringify({
            langs: ['c', 'cpp', 'java', 'rust', 'x86asm', 'armasm', 'avrasm', 'dockerfile', 'powershell', 'cmake', 'verilog', 'vhdl',
                    'dart', 'scala', 'haskell', 'protobuf'].filter((l) => !has(l)),
            alias: ['asm', 'nasm', 'assembly', 's', 'arm'].filter((l) => !has(l)),
            asm: hljs.highlight('mov eax, 1 ; one', { language: 'asm' }).value,
            arm: hljs.highlight('ldr r0, =0x20 @ load', { language: 'arm' }).value }));""")
    assert got["langs"] == [] and got["alias"] == []
    assert "hljs-keyword" in got["asm"] and "hljs-comment" in got["asm"] and "hljs-" in got["arm"]


def test_the_viewer_picks_the_language_from_the_file_name():
    src = (ROOT / "web/app.js").read_text()
    ext = src[src.index("const EXT_LANG = {"):src.index("const LANG_ALIAS")]
    for e, lang in {"s": "x86asm", "asm": "x86asm", "kt": "kotlin", "cs": "csharp", "rb": "ruby", "php": "php",
                    "swift": "swift", "ps1": "powershell", "v": "verilog", "vhd": "vhdl", "ino": "cpp"}.items():
        assert re.search(rf"\b{e}: '{lang}'", ext), (e, lang)
