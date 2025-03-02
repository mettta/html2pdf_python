# Invoke is broken on Python 3.11
# https://github.com/pyinvoke/invoke/issues/833#issuecomment-1293148106
import inspect
import os
import re
import sys
from typing import Optional

if not hasattr(inspect, "getargspec"):
    inspect.getargspec = inspect.getfullargspec

import invoke  # pylint: disable=wrong-import-position
from invoke import task  # pylint: disable=wrong-import-position

# Specifying encoding because Windows crashes otherwise when running Invoke
# tasks below:
# UnicodeEncodeError: 'charmap' codec can't encode character '\ufffd'
# in position 16: character maps to <undefined>
# People say, it might also be possible to export PYTHONIOENCODING=utf8 but this
# seems to work.
# FIXME: If you are a Windows user and expert, please advise on how to do this
# properly.
sys.stdout = open(  # pylint: disable=consider-using-with
    1, "w", encoding="utf-8", closefd=False, buffering=1
)


def run_invoke(
    context,
    cmd,
    environment: Optional[dict] = None,
    warn: bool = False,
    pty: bool = False,
) -> invoke.runners.Result:
    def one_line_command(string):
        return re.sub("\\s+", " ", string).strip()

    return context.run(
        one_line_command(cmd),
        env=environment,
        hide=False,
        warn=warn,
        pty=pty,
        echo=True,
    )


@task(default=True)
def list_tasks(context):
    clean_command = """
        invoke --list
    """
    run_invoke(context, clean_command)


@task
def bootstrap(context):
    run_invoke(context, "git submodule update --init --recursive")
    run_invoke(context, "pip install -r requirements.development.txt")


@task(aliases=["b"])
def build(context):
    run_invoke(
        context, "cd submodules/html2pdf && npm install && npm run build"
    )
    # Windows can't do slashes for this one.
    if not os.path.isdir(os.path.join("html2print", "html2pdf_js")):
        run_invoke(
            context,
            """
            cd html2print && mkdir html2pdf_js
            """,
        )
    run_invoke(
        context,
        """
        cp submodules/html2pdf/dist/bundle.js html2print/html2pdf_js/html2pdf.min.js
        """,
    )


@task
def format_readme(context):
    run_invoke(
        context,
        """
    prettier
        --write --print-width 80 --prose-wrap always --parser=markdown
        README.md
    """,
    )


@task
def get_chrome_driver(
    context,
):
    run_invoke(
        context,
        """
        python html2print/html2print.py get_driver
    """,
    )


@task
def lint_ruff_format(context):
    result: invoke.runners.Result = run_invoke(
        context,
        """
            ruff
                format
                *.py
                html2print/
                tests/integration/
        """,
    )
    # Ruff always exits with 0, so we handle the output.
    if "reformatted" in result.stdout:
        print("invoke: ruff format found issues")  # noqa: T201
        result.exited = 1
        raise invoke.exceptions.UnexpectedExit(result)


@task(aliases=["lr"])
def lint_ruff(context):
    run_invoke(
        context,
        """
            ruff check *.py html2print/ --fix --cache-dir build/ruff
        """,
    )


@task(aliases=["lm"])
def lint_mypy(context):
    # These checks do not seem to be useful:
    # - import
    # - misc
    run_invoke(
        context,
        """
            mypy html2print/
                --show-error-codes
                --disable-error-code=import
                --disable-error-code=misc
                --cache-dir=build/mypy
                --strict
                --python-version=3.8
        """,
    )


@task(aliases=["l"])
def lint(context):
    lint_ruff_format(context)
    lint_ruff(context)
    lint_mypy(context)


@task(aliases=["ti"])
def test_integration(
    context,
    focus=None,
    debug=False,
):
    clean_itest_artifacts(context)

    get_chrome_driver(context)

    cwd = os.getcwd()

    html2pdf_exec = f'python3 \\"{cwd}/html2print/html2print.py\\"'

    focus_or_none = f"--filter {focus}" if focus else ""
    debug_opts = "-vv --show-all" if debug else ""

    # For now, the --threads are set to 1 because running tests parallelized
    # will result in race conditions between Web Driver Manager downloading
    # ChromeDriver.
    itest_command = f"""
        lit
        --threads 1
        --param HTML2PRINT_EXEC="{html2pdf_exec}"
        -v
        {debug_opts}
        {focus_or_none}
        tests/integration
    """

    run_invoke(context, itest_command)


@task(aliases=["t"])
def test(context):
    test_integration(context)


@task
def clean_itest_artifacts(context):
    # https://unix.stackexchange.com/a/689930/77389
    find_command = """
        git clean -dfX tests/integration/
    """
    # The command sometimes exits with 1 even if the files are deleted.
    # warn=True ensures that the execution continues.
    run_invoke(context, find_command, warn=True)


@task
def package(context):
    build(context)

    run_invoke(
        context,
        """
            rm -rfv dist/
        """,
    )
    run_invoke(
        context,
        """
            python3 -m build
        """,
    )
    run_invoke(
        context,
        """
            twine check dist/*
        """,
    )


@task
def release(context, test_pypi=False, username=None, password=None):
    """
    A release can be made to PyPI or test package index (TestPyPI):
    https://pypi.org/project/html2print/
    https://test.pypi.org/project/html2print/
    """

    # When a username is provided, we also need password, and then we don't use
    # tokens set up on a local machine.
    assert username is None or password is not None

    package(context)

    repository_argument_or_none = (
        ""
        if username
        else (
            "--repository html2print_test"
            if test_pypi
            else "--repository html2print_release"
        )
    )
    user_password = f"-u{username} -p{password}" if username is not None else ""

    # The token is in a core developer's .pypirc file.
    # https://test.pypi.org/manage/account/token/
    # https://packaging.python.org/en/latest/specifications/pypirc/#pypirc
    run_invoke(
        context,
        f"""
            twine upload dist/html2print-*.tar.gz
                {repository_argument_or_none}
                {user_password}
        """,
    )


@task(aliases=["bd"])
def build_docker(context, image: str = "html2print:latest", no_cache: bool = False, source="pypi"):
    no_cache_argument = "--no-cache" if no_cache else ""
    run_invoke(
        context,
        f"""
        docker build .
            --build-arg HTML2PRINT_SOURCE={source}
            -t {image}
            {no_cache_argument}
        """
    )


@task(aliases=["rd"])
def run_docker(context, image: str = "html2print:latest", command: Optional[str] = None):
    command_argument = f'/bin/bash -c "{command}"' if command is not None else ""
    entry_point_argument = '--entrypoint=""' if command_argument else ""

    run_invoke(
        context,
        f"""
        docker run
            --name html2print
            --rm
            -it
            -v "$(pwd):/data"
            {entry_point_argument}
            {image}
            {command_argument}
        """,
        pty=True
    )


@task(aliases=["td"])
def test_docker(context, image: str = "html2print:latest"):
    run_invoke(
        context,
        f"""
        mkdir -p output/ && chmod 777 output/
        """
    )
    run_docker(
        context,
        image=image,
        command=(
            "cd tests/integration/01_hello_world && html2print print index.html /data/output/index.pdf"
        )
    )
