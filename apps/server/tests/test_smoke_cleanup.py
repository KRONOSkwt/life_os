"""Regression for temporary files briefly locked during Windows cleanup."""

import ctypes
import os
import runpy
import threading
from ctypes import wintypes

import pytest

from life_os.api import ROOT


@pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing semantics")
def test_cleanup_waits_for_a_windows_file_handle_to_close():
    helpers = runpy.run_path(str(ROOT / "scripts/smoke-server.py"), run_name="cleanup_test")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    timer = None
    try:
        with helpers["temporary_workspace"]("cleanup-test-") as directory:
            logfile = directory / "locked.log"
            logfile.write_text("temporary", encoding="utf-8")
            # GENERIC_READ, FILE_SHARE_READ (no FILE_SHARE_DELETE), OPEN_EXISTING.
            handle = kernel.CreateFileW(str(logfile), 0x80000000, 1, None, 3, 128, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            timer = threading.Timer(0.3, kernel.CloseHandle, args=(handle,))
            timer.start()
        assert not directory.exists()
    finally:
        if timer is not None:
            timer.join()
