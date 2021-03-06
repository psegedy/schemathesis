import os
import sys

import click
import pytest
from hypothesis.reporting import report

import schemathesis
import schemathesis.cli.context
from schemathesis import models, runner, utils
from schemathesis.cli.output import default
from schemathesis.cli.output.default import display_internal_error
from schemathesis.runner.events import Finished, InternalError
from schemathesis.runner.serialization import SerializedTestResult

from ...utils import strip_style_win32


@pytest.fixture(autouse=True)
def click_context():
    """Add terminal colors to the output in tests."""
    with click.Context(schemathesis.cli.run, color=True):
        yield


@pytest.fixture()
def execution_context():
    return schemathesis.cli.context.ExecutionContext([], endpoints_count=1)


@pytest.fixture
def endpoint(swagger_20):
    return models.Endpoint("/success", "GET", definition={}, base_url="http://127.0.0.1:8080", schema=swagger_20)


@pytest.fixture()
def results_set(endpoint):
    statistic = models.TestResult(endpoint)
    return models.TestResultSet([statistic])


@pytest.fixture()
def after_execution(results_set, endpoint, swagger_20):
    return runner.events.AfterExecution.from_result(
        result=results_set.results[0], status=models.Status.success, hypothesis_output=[]
    )


@pytest.mark.parametrize(
    "title,separator,printed,expected",
    [
        ("TEST", "-", "data in section", "------- TEST -------"),
        ("TEST", "*", "data in section", "******* TEST *******"),
    ],
)
def test_display_section_name(capsys, title, separator, printed, expected):
    # When section name is displayed
    default.display_section_name(title, separator=separator)
    out = capsys.readouterr().out.strip()
    terminal_width = default.get_terminal_width()
    # It should fit into the terminal width
    assert len(click.unstyle(out)) == terminal_width
    # And the section name should be bold
    assert strip_style_win32(click.style(click.unstyle(out), bold=True)) == out
    assert expected in out


def test_handle_initialized(capsys, execution_context, results_set, swagger_20):
    # Given Initialized event
    event = runner.events.Initialized.from_schema(schema=swagger_20)
    # When this even is handled
    default.handle_initialized(execution_context, event)
    out = capsys.readouterr().out
    lines = out.split("\n")
    # Then initial title is displayed
    assert " Schemathesis test session starts " in lines[0]
    # And platform information is there
    assert lines[1].startswith("platform")
    # And current directory
    assert f"rootdir: {os.getcwd()}" in lines
    # And number of collected endpoints
    assert strip_style_win32(click.style("collected endpoints: 1", bold=True)) in lines
    # And the output has an empty line in the end
    assert out.endswith("\n\n")


def test_display_statistic(capsys, swagger_20, endpoint):
    # Given multiple successful & failed checks in a single test
    success = models.Check("not_a_server_error", models.Status.success)
    failure = models.Check("not_a_server_error", models.Status.failure)
    single_test_statistic = models.TestResult(
        endpoint, [success, success, success, failure, failure, models.Check("different_check", models.Status.success)]
    )
    results = models.TestResultSet([single_test_statistic])
    event = Finished.from_results(results, running_time=1.0)
    # When test results are displayed
    default.display_statistic(event)

    lines = [line for line in capsys.readouterr().out.split("\n") if line]
    failed = strip_style_win32(click.style("FAILED", bold=True, fg="red"))
    not_a_server_error = strip_style_win32(click.style("not_a_server_error", bold=True))
    different_check = strip_style_win32(click.style("different_check", bold=True))
    passed = strip_style_win32(click.style("PASSED", bold=True, fg="green"))
    # Then all check results should be properly displayed with relevant colors
    assert lines[1:3] == [
        f"{not_a_server_error}            3 / 5 passed          {failed} ",
        f"{different_check}               1 / 1 passed          {passed} ",
    ]


def test_display_statistic_empty(capsys, results_set):
    default.display_statistic(results_set)
    assert capsys.readouterr().out.split("\n")[2] == strip_style_win32(
        click.style("No checks were performed.", bold=True)
    )


def test_capture_hypothesis_output():
    # When Hypothesis output us captured
    with utils.capture_hypothesis_output() as hypothesis_output:
        value = "Some text"
        report(value)
        report(value)
    # Then all calls to internal Hypothesis reporting will put its output to a list
    assert hypothesis_output == [value, value]


@pytest.mark.parametrize("position, length, expected", ((1, 100, "[  1%]"), (20, 100, "[ 20%]"), (100, 100, "[100%]")))
def test_get_percentage(position, length, expected):
    assert default.get_percentage(position, length) == expected


@pytest.mark.parametrize("current_line_length", (0, 20))
@pytest.mark.parametrize("endpoints_processed, percentage", ((0, "[  0%]"), (1, "[100%]")))
def test_display_percentage(
    capsys, execution_context, after_execution, swagger_20, current_line_length, endpoints_processed, percentage
):
    execution_context.current_line_length = current_line_length
    execution_context.endpoints_processed = endpoints_processed
    # When percentage is displayed
    default.display_percentage(execution_context, after_execution)
    out = capsys.readouterr().out
    # Then the whole line fits precisely to the terminal width
    assert len(click.unstyle(out)) + current_line_length == default.get_terminal_width()
    # And the percentage displayed as expected in cyan color
    assert out.strip() == strip_style_win32(click.style(percentage, fg="cyan"))


def test_display_hypothesis_output(capsys):
    # When Hypothesis output is displayed
    default.display_hypothesis_output(["foo", "bar"])
    lines = capsys.readouterr().out.split("\n")
    # Then the relevant section title is displayed
    assert " HYPOTHESIS OUTPUT" in lines[0]
    # And the output is displayed as separate lines in red color
    assert " ".join(lines[1:3]) == strip_style_win32(click.style("foo bar", fg="red"))


@pytest.mark.parametrize("body", ({}, {"foo": "bar"}, None))
def test_display_single_failure(capsys, swagger_20, endpoint, body):
    # Given a single test result with multiple successful & failed checks
    success = models.Check("not_a_server_error", models.Status.success)
    failure = models.Check("not_a_server_error", models.Status.failure, models.Case(endpoint, body=body))
    test_statistic = models.TestResult(
        endpoint, [success, success, success, failure, failure, models.Check("different_check", models.Status.success)]
    )
    # When this failure is displayed
    default.display_failures_for_single_test(SerializedTestResult.from_test_result(test_statistic))
    out = capsys.readouterr().out
    lines = out.split("\n")
    # Then the endpoint name is displayed as a subsection
    assert " GET: /success " in lines[0]
    # And check name is displayed in red
    assert lines[1] == strip_style_win32(click.style("Check           : not_a_server_error", fg="red"))
    # And body should be displayed if it is not None
    if body is None:
        assert "Body" not in out
    else:
        assert strip_style_win32(click.style(f"Body            : {body}", fg="red")) in lines
    # And empty parameters are not present in the output
    assert "Path parameters" not in out
    # And not needed attributes are not displayed
    assert "Path" not in out
    assert "Method" not in out
    assert "Base url" not in out


@pytest.mark.parametrize(
    "status, expected_symbol, color",
    ((models.Status.success, ".", "green"), (models.Status.failure, "F", "red"), (models.Status.error, "E", "red")),
)
def test_handle_after_execution(capsys, execution_context, after_execution, status, expected_symbol, color):
    # Given AfterExecution even with certain status
    after_execution.status = status
    # When this event is handled
    default.handle_after_execution(execution_context, after_execution)

    lines = capsys.readouterr().out.strip().split("\n")
    symbol, percentage = lines[0].split()
    # Then the symbol corresponding to the status is displayed with a proper color
    assert strip_style_win32(click.style(expected_symbol, fg=color)) == symbol
    # And percentage is displayed in cyan color
    assert strip_style_win32(click.style("[100%]", fg="cyan")) == percentage


def test_after_execution_attributes(execution_context, after_execution):
    # When `handle_after_execution` is executed
    default.handle_after_execution(execution_context, after_execution)
    # Then number of endpoints processed grows by 1
    assert execution_context.endpoints_processed == 1
    # And the line length grows by 1 symbol
    assert execution_context.current_line_length == 1

    default.handle_after_execution(execution_context, after_execution)
    assert execution_context.endpoints_processed == 2
    assert execution_context.current_line_length == 2


@pytest.mark.parametrize("show_errors_tracebacks", (True, False))
def test_display_single_error(capsys, swagger_20, endpoint, execution_context, show_errors_tracebacks):
    # Given exception is multiline
    exception = None
    try:
        exec("some invalid code")
    except SyntaxError as exc:
        exception = exc

    result = models.TestResult(endpoint)
    result.add_error(exception)
    # When the related test result is displayed
    execution_context.show_errors_tracebacks = show_errors_tracebacks
    default.display_single_error(execution_context, SerializedTestResult.from_test_result(result))
    lines = capsys.readouterr().out.strip().split("\n")
    # Then it should be correctly formatted and displayed in red color
    if sys.version_info <= (3, 8):
        expected = '  File "<string>", line 1\n    some invalid code\n               ^\nSyntaxError: invalid syntax\n'
    else:
        expected = '  File "<string>", line 1\n    some invalid code\n         ^\nSyntaxError: invalid syntax\n'
    if show_errors_tracebacks:
        lines = click.unstyle("\n".join(lines)).split("\n")
        assert lines[1] == "Traceback (most recent call last):"
        # There is a path on the next line, it is simpler to not check it since it doesn't give much value
        # But presence of traceback itself is checked
        expected = f'    exec("some invalid code")\n{expected}'
        assert "\n".join(lines[3:8]) == expected.strip("\n")
    else:
        assert "\n".join(lines[1:6]) == strip_style_win32(click.style(expected, fg="red")).rstrip("\n")


def test_display_failures(swagger_20, capsys, execution_context, results_set):
    # Given two test results - success and failure
    endpoint = models.Endpoint("/api/failure", "GET", {}, base_url="http://127.0.0.1:8080", schema=swagger_20)
    failure = models.TestResult(endpoint)
    failure.add_failure("test", models.Case(endpoint), "Message")
    execution_context.results.append(SerializedTestResult.from_test_result(failure))
    results_set.append(failure)
    event = Finished.from_results(results_set, 1.0)
    # When the failures are displayed
    default.display_failures(execution_context, event)
    out = capsys.readouterr().out.strip()
    # Then section title is displayed
    assert " FAILURES " in out
    # And endpoint with a failure is displayed as a subsection
    assert " GET: /api/failure " in out
    assert "Message" in out
    # And check name is displayed
    assert "Check           : test" in out
    assert "Run this Python code to reproduce this failure: " in out
    assert "requests.get('http://127.0.0.1:8080/api/failure')" in out


@pytest.mark.parametrize("show_errors_tracebacks", (True, False))
def test_display_errors(swagger_20, capsys, results_set, execution_context, show_errors_tracebacks):
    # Given two test results - success and error
    endpoint = models.Endpoint("/api/error", "GET", {}, swagger_20)
    error = models.TestResult(endpoint, seed=123)
    error.add_error(ConnectionError("Connection refused!"), models.Case(endpoint, query={"a": 1}))
    results_set.append(error)
    execution_context.results.append(SerializedTestResult.from_test_result(error))
    event = Finished.from_results(results_set, 1.0)
    # When the errors are displayed
    execution_context.show_errors_tracebacks = show_errors_tracebacks
    default.display_errors(execution_context, event)
    out = capsys.readouterr().out.strip()
    # Then section title is displayed
    assert " ERRORS " in out
    help_message_exists = (
        "Add this option to your command line parameters to see full tracebacks: --show-errors-tracebacks" in out
    )
    # And help message is displayed only if tracebacks are not shown
    assert help_message_exists is not show_errors_tracebacks
    # And endpoint with an error is displayed as a subsection
    assert " GET: /api/error " in out
    # And the error itself is displayed
    assert "ConnectionError: Connection refused!" in out
    # And the example is displayed
    assert "Query           : {'a': 1}" in out
    assert "Or add this option to your command line parameters: --hypothesis-seed=123" in out


@pytest.mark.parametrize("show_errors_tracebacks", (True, False))
def test_display_internal_error(capsys, execution_context, show_errors_tracebacks):
    execution_context.show_errors_tracebacks = show_errors_tracebacks
    try:
        1 / 0
    except ArithmeticError as exc:
        event = InternalError.from_exc(exc)
        display_internal_error(execution_context, event)
        out = capsys.readouterr().out.strip()
        assert ("Traceback (most recent call last):" in out) is show_errors_tracebacks
        assert "ZeroDivisionError: division by zero" in out


@pytest.mark.parametrize("attribute, expected", (("cookies", "Cookies"), ("path_parameters", "Path parameters")))
def test_make_verbose_name(attribute, expected):
    assert default.make_verbose_name(attribute) == expected


def test_display_summary(capsys, results_set, swagger_20):
    # Given the Finished event
    event = runner.events.Finished.from_results(results=results_set, running_time=1.257)
    # When `display_summary` is called
    with pytest.raises(click.exceptions.Exit):
        default.display_summary(event)
    out = capsys.readouterr().out.strip()
    # Then number of total tests & total running time should be displayed
    assert "=== 1 passed in 1.26s ===" in out
    # And it should be in green & bold style
    assert strip_style_win32(click.style(click.unstyle(out), fg="green", bold=True)) == out
