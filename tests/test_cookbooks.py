"""Every example and cookbook script compiles and calls the SDK with real signatures.

Third-party libraries (LangChain, LlamaIndex) are not imported: the check is static.
"""
import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import fastocr_sdk
from fastocr_sdk import AsyncFastOCR, DocumentJob, FastOCR
from fastocr_sdk.async_client import AsyncDocumentsResource
from fastocr_sdk.client import DocumentsResource

HERE = Path(__file__).resolve().parent
SDK_NAMES = {"FastOCR", "AsyncFastOCR"}
JOB_ATTRIBUTES = set(dir(DocumentJob)) | {f.name for f in dataclasses.fields(DocumentJob)}


def script_paths():
    roots = [HERE.parent / "examples"]
    nearest = next((p / "cookbooks" for p in HERE.parents if (p / "cookbooks").is_dir()), None)
    roots += [nearest] if nearest else []
    return sorted({path for root in roots for path in root.rglob("*.py")})


SCRIPTS = script_paths()


def client_expressions(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and is_sdk_constructor(node.value):
            names.update(ast.unparse(target) for target in node.targets)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if is_sdk_constructor(item.context_expr) and item.optional_vars is not None:
                    names.add(ast.unparse(item.optional_vars))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.annotation is not None and ast.unparse(arg.annotation) in SDK_NAMES:
                    names.add(arg.arg)
    return names


def is_sdk_constructor(node):
    return isinstance(node, ast.Call) and ast.unparse(node.func) in SDK_NAMES


def check_call(node, signature, where):
    if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
        return
    try:
        signature.bind(None, *[0] * len(node.args), **{k.arg: 0 for k in node.keywords})
    except TypeError as error:
        pytest.fail(f"{where}: {ast.unparse(node.func)}() does not match the SDK: {error}")


def job_variables(tree, clients):
    names = set()
    for node in ast.walk(tree):
        value = node.value if isinstance(node, ast.Assign) else None
        value = value.value if isinstance(value, ast.Await) else value
        if isinstance(value, ast.Call) and ast.unparse(value.func).endswith(".documents.process"):
            if ast.unparse(value.func).removesuffix(".documents.process") in clients:
                names.update(ast.unparse(target) for target in node.targets)
    return names


@pytest.mark.parametrize("path", SCRIPTS, ids=[str(p.relative_to(HERE.parent.parent.parent if "cookbooks" in p.parts else HERE.parent)) for p in SCRIPTS])
def test_script_compiles_and_matches_the_sdk(path):
    source = path.read_text()
    tree = ast.parse(source)
    compile(source, str(path), "exec")
    is_async = "AsyncFastOCR" in source
    client_class = AsyncFastOCR if is_async else FastOCR
    resource_class = AsyncDocumentsResource if is_async else DocumentsResource
    clients = client_expressions(tree)
    jobs = job_variables(tree, clients)
    where = path.name

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "fastocr_sdk":
            for alias in node.names:
                assert hasattr(fastocr_sdk, alias.name), f"{where}: fastocr_sdk has no {alias.name}"
        if isinstance(node, ast.ImportFrom) and node.module == "fastocr":
            pytest.fail(f"{where}: imports the unrelated 'fastocr' package")
        if isinstance(node, ast.Attribute) and ast.unparse(node.value) in jobs:
            assert node.attr in JOB_ATTRIBUTES, f"{where}: DocumentJob has no {node.attr}"
        if not isinstance(node, ast.Call):
            continue
        if is_sdk_constructor(node):
            check_call(node, inspect.signature(client_class.__init__), where)
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        owner = ast.unparse(func.value)
        if owner in clients and func.attr == "extract_text":
            check_call(node, inspect.signature(client_class.extract_text), where)
        elif owner.endswith(".documents") and owner.removesuffix(".documents") in clients:
            method = getattr(resource_class, func.attr, None)
            assert method is not None and not func.attr.startswith("_"), f"{where}: no documents.{func.attr}"
            check_call(node, inspect.signature(method), where)


def test_scripts_were_found():
    assert SCRIPTS, "no example scripts found"
    assert any("examples" in p.parts for p in SCRIPTS)


def test_the_checker_catches_a_wrong_call(tmp_path):
    bad = "from fastocr_sdk import FastOCR\nc = FastOCR()\nc.documents.process('a.pdf', output_format='txt')\n"
    tree = ast.parse(bad)
    clients = client_expressions(tree)
    call = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "c.documents.process"][0]

    assert clients == {"c"}
    with pytest.raises(pytest.fail.Exception):
        check_call(call, inspect.signature(DocumentsResource.process), "bad.py")
