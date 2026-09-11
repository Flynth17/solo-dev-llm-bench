"""Focused coverage for the Windows-specific static-hardware branches added in Act 8.

The real machine is Windows, but these tests do NOT shell out to PowerShell or touch
real hardware — each branch is driven with a mocked ``subprocess.run`` /
``platform.system`` so they are deterministic and host-independent. This keeps coverage
of the changed code paths (Win32_Processor.Name; summed Win32_PhysicalMemory.Capacity)
without depending on live machine state.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from src import hardware


def _mock_run(stdout=""):
    """Return a subprocess.run mock whose result has the given stdout string."""
    return MagicMock(return_value=MagicMock(returncode=0, stdout=stdout))


class TestWindowsCpuModel:
    @patch("src.hardware.platform.system", return_value="Windows")
    def test_reads_win32_processor_name_marketing_string(self, _ps):
        with patch("src.hardware.subprocess.run", _mock_run(
                "AMD Ryzen 9 9950X3D 16-Core Processor\n")):
            assert hardware._cpu_model() == "AMD Ryzen 9 9950X3D 16-Core Processor"

    @patch("src.hardware.platform.system", return_value="Windows")
    def test_falls_back_to_platform_processor_when_cim_empty(self, _ps):
        with patch("src.hardware.subprocess.run", _mock_run("\n")), \
             patch("src.hardware.platform.processor", return_value="Generic CPU"):
            assert hardware._cpu_model() == "Generic CPU"

    @patch("src.hardware.platform.system", return_value="Windows")
    def test_falls_back_to_platform_processor_when_cim_absent(self, _ps):
        # CIM unavailable (non-zero return) -> generic identifier fallback.
        with patch("src.hardware.subprocess.run", MagicMock(return_value=MagicMock(
                returncode=1, stdout=""))), \
             patch("src.hardware.platform.processor", return_value="AMD64"):
            assert hardware._cpu_model() == "AMD64"


class TestWindowsInstalledRam:
    @patch("src.hardware.platform.system", return_value="Windows")
    def test_sums_physical_memory_capacity(self, _ps):
        # 2 x 32 GiB modules -> 68719476736 bytes summed.
        with patch("src.hardware.subprocess.run", _mock_run("68719476736\n")):
            assert hardware._installed_ram_bytes() == 68719476736

    @patch("src.hardware.platform.system", return_value="Windows")
    def test_falls_back_to_total_physical_memory_when_no_capacity(self, _ps):
        with patch("src.hardware._win32_installed_ram_bytes", return_value=None), \
             patch("src.hardware.subprocess.run", _mock_run("66094223360\n")):
            assert hardware._installed_ram_bytes() == 66094223360

    @patch("src.hardware.platform.system", return_value="Windows")
    def test_returns_none_when_summed_capacity_absent(self, _ps):
        with patch("src.hardware._win32_installed_ram_bytes", return_value=None), \
             patch("src.hardware.subprocess.run", MagicMock(return_value=MagicMock(
                 returncode=1, stdout="" ))):
            assert hardware._installed_ram_bytes() is None


class TestWindowsSnapshotAggregate:
    def test_snapshot_keys_and_serialisable(self):
        snap = hardware.snapshot_hardware()
        expected = {
            "cpu_model", "cpu_logical_cores", "cpu_physical_cores",
            "installed_ram_bytes", "gpu_model", "total_vram_bytes",
            "os_platform", "os_version", "nvidia_driver_version", "python_version",
        }
        assert set(snap.keys()) == expected
        # Every value is a JSON-serialisable primitive (str / int / None).
        json.dumps(snap)  # must not raise
        for v in snap.values():
            assert v is None or isinstance(v, (str, int))

    @patch("src.hardware._nvidia_info", return_value=(None, None, None))
    @patch("src.hardware._cpu_model",
           return_value="AMD Ryzen 9 9950X3D 16-Core Processor")
    @patch("src.hardware._installed_ram_bytes", return_value=68719476736)
    def test_snapshot_windows_reports_expected_fields(self, _ram, _cpu, _nv):
        # Assemble the aggregate deterministically from the (Windows) sub-functions
        # without orchestrating multiple real subprocess calls.
        snap = hardware.snapshot_hardware()
        assert snap["cpu_model"] == "AMD Ryzen 9 9950X3D 16-Core Processor"
        assert snap["installed_ram_bytes"] == 68719476736


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
