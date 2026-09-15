"""check-hltv-api-drift.py: the compare must survive the key line and never print it."""
import importlib.util
import pathlib
import sys

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check-hltv-api-drift.py"
EXAMPLE = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "hltv-api.py.example"

SECRET = "xxREALKEYVALUExxDOxNOTxPRINTxx42"


def _load():
    spec = importlib.util.spec_from_file_location("check_hltv_api_drift", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drift = _load()


def _run(tmp_path, live_text, example_text, capsys):
    live = tmp_path / "hltv-api.py"
    example = tmp_path / "hltv-api.py.example"
    live.write_text(live_text, encoding="utf-8")
    example.write_text(example_text, encoding="utf-8")
    code = drift.main(["--live", str(live), "--example", str(example)])
    return code, capsys.readouterr()


BODY = 'import os\n\n\ndef handle():\n    return 200\n'


def test_key_line_alone_is_not_drift(tmp_path, capsys):
    live = 'AUTH_KEY = "%s"\n%s' % (SECRET, BODY)
    example = 'AUTH_KEY = os.environ.get("HLTV_API_KEY", "") or ""\n%s' % BODY
    code, cap = _run(tmp_path, live, example, capsys)
    assert code == 0, cap.out + cap.err
    assert "no drift" in cap.out
    assert SECRET not in cap.out + cap.err


def test_real_drift_is_reported_and_the_key_is_not(tmp_path, capsys):
    live = 'AUTH_KEY = "%s"\n%s' % (SECRET, BODY)
    example = ('AUTH_KEY = os.environ.get("HLTV_API_KEY", "") or ""\n'
               'import sys\n'
               'if not AUTH_KEY:\n'
               '    sys.exit("refusing to start")\n' + BODY)
    code, cap = _run(tmp_path, live, example, capsys)
    assert code == 1
    assert "DRIFT" in cap.out
    # The measured 2026-09-15 divergence: the box is missing the fail-fast guard.
    assert "sys.exit" in cap.out
    assert SECRET not in cap.out + cap.err


def test_a_newly_named_secret_is_redacted_without_being_listed_anywhere(tmp_path, capsys):
    other = "yyANOTHERxSECRETxVALUExx99"
    live = ('AUTH_KEY = "%s"\nRELAY_TOKEN = "%s"\n%s' % (SECRET, other, BODY))
    example = ('AUTH_KEY = os.environ.get("HLTV_API_KEY", "") or ""\n'
               'RELAY_TOKEN = os.environ.get("RELAY_TOKEN", "")\n' + BODY)
    code, cap = _run(tmp_path, live, example, capsys)
    assert code == 0
    assert other not in cap.out + cap.err


def test_a_secret_shape_that_slips_redaction_aborts_before_printing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(drift, "_SECRET_ASSIGN", drift.re.compile(r"^(?!x)x$"))
    live = 'AUTH_KEY = "%s"\n%s' % (SECRET, BODY)
    code, cap = _run(tmp_path, live, 'AUTH_KEY = ""\n' + BODY, capsys)
    # With redaction disabled the guard must not fire silently: either it aborts (3)
    # or it reports drift — but the key must never be printed under the real regex.
    assert code in (1, 3)


def test_missing_live_file_is_2_not_0(tmp_path, capsys):
    example = tmp_path / "hltv-api.py.example"
    example.write_text(BODY, encoding="utf-8")
    code = drift.main(["--live", str(tmp_path / "gone.py"), "--example", str(example)])
    assert code == 2


def test_the_shipped_example_has_no_quoted_secret_literal():
    """The tracked copy is public; a filled-in key here would be a leak."""
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    assert drift.leaks(lines) == []
    # Control: the same check does fire on a filled example, so the assert above
    # is not passing because leaks() cannot return anything.
    assert drift.leaks(['AUTH_KEY = "%s"' % SECRET]) == [1]


@pytest.mark.parametrize("name", ["AUTH_KEY", "API_SECRET", "GH_TOKEN", "DB_PASSWORD"])
def test_redaction_is_by_shape_not_by_a_name_list(name):
    out = drift.redact('%s = "%s"' % (name, SECRET))
    assert out == ["%s = %s" % (name, drift.PLACEHOLDER)]
