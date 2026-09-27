"""Host workflow compatibility guards."""

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_self_assessment.ps1"


def test_windows_powershell_script_is_ascii_safe() -> None:
    """Windows PowerShell 5 must not corrupt literals when no UTF-8 BOM is present."""
    source = SCRIPT.read_bytes().decode("ascii")

    assert "Find-PrivateJsonFile" in source
    assert "Import-ApplicationEnvironment" in source
    assert "Model preflight failed before scoring" in source
