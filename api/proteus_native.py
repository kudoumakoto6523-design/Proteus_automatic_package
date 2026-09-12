"""Shared Win32 window/menu helpers for PID-scoped Proteus sessions."""
import argparse
import ctypes as c
import json
from ctypes import wintypes as w

u = c.WinDLL("user32", use_last_error=True)
k = c.WinDLL("kernel32", use_last_error=True)
CALLBACK = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
u.GetWindowThreadProcessId.argtypes = (w.HWND, c.POINTER(w.DWORD))
u.GetWindowTextLengthW.argtypes = (w.HWND,)
u.GetWindowTextW.argtypes = (w.HWND, w.LPWSTR, c.c_int)
u.GetClassNameW.argtypes = (w.HWND, w.LPWSTR, c.c_int)
u.GetDlgCtrlID.argtypes = (w.HWND,)
u.IsWindowVisible.argtypes = (w.HWND,)
u.IsWindowEnabled.argtypes = (w.HWND,)
u.GetMenu.argtypes = (w.HWND,)
u.GetMenu.restype = w.HMENU
u.GetSubMenu.argtypes = (w.HMENU, c.c_int)
u.GetSubMenu.restype = w.HMENU
u.GetMenuItemCount.argtypes = (w.HMENU,)
u.GetMenuItemID.argtypes = (w.HMENU, c.c_int)
u.GetMenuItemID.restype = w.UINT
u.GetMenuStringW.argtypes = (w.HMENU, w.UINT, w.LPWSTR, c.c_int, w.UINT)
u.GetMenuState.argtypes = (w.HMENU, w.UINT, w.UINT)
u.PostMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
k.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
k.OpenProcess.restype = w.HANDLE
k.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD))
k.CloseHandle.argtypes = (w.HANDLE,)


def windows(pid):
    rows = []

    def describe(hwnd):
        text = c.create_unicode_buffer(u.GetWindowTextLengthW(hwnd) + 1)
        cls = c.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, text, len(text))
        u.GetClassNameW(hwnd, cls, len(cls))
        return dict(hwnd=hwnd, title=text.value, cls=cls.value,
                    control_id=u.GetDlgCtrlID(hwnd), enabled=bool(u.IsWindowEnabled(hwnd)))

    @CALLBACK
    def visit(hwnd, _):
        owner = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, c.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd):
            row = describe(hwnd)
            children = []

            @CALLBACK
            def child(handle, _):
                if u.IsWindowVisible(handle):
                    children.append(describe(handle))
                return True

            u.EnumChildWindows(hwnd, child, 0)
            row["children"] = children
            rows.append(row)
        return True

    u.EnumWindows(visit, 0)
    return rows


def menus(menu, prefix=""):
    rows = []
    for index in range(u.GetMenuItemCount(menu)):
        text = c.create_unicode_buffer(512)
        u.GetMenuStringW(menu, index, text, len(text), 0x400)
        label = text.value.replace("&", "").split("\t")[0].strip()
        if not label:
            continue
        path = f"{prefix}/{label}" if prefix else label
        sub = u.GetSubMenu(menu, index)
        if sub:
            rows.extend(menus(sub, path))
        else:
            rows.append(dict(path=path, command_id=u.GetMenuItemID(menu, index),
                             enabled=not bool(u.GetMenuState(menu, index, 0x400) & 3)))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    # ponytail: labels follow this installation's language; rediscover paths on other versions/locales.
    parser.add_argument("--menu", help="Exact discovered menu path to invoke; default is read-only.")
    args = parser.parse_args()
    process = k.OpenProcess(0x1000, False, args.pid)
    if not process:
        raise c.WinError(c.get_last_error())
    try:
        path = c.create_unicode_buffer(32768)
        size = w.DWORD(len(path))
        if not k.QueryFullProcessImageNameW(process, 0, path, c.byref(size)):
            raise c.WinError(c.get_last_error())
        if path.value.lower().rsplit("\\", 1)[-1] != "pds.exe":
            raise ValueError("The PID must belong to PDS.EXE.")
    finally:
        k.CloseHandle(process)
    rows = windows(args.pid)
    main_windows = [row for row in rows if u.GetMenu(row["hwnd"])]
    if len(main_windows) != 1:
        raise RuntimeError("Expected exactly one Proteus window with a menu.")
    window = main_windows[0]
    entries = menus(u.GetMenu(window["hwnd"]))
    if args.menu:
        matches = [entry for entry in entries if entry["path"] == args.menu]
        if len(matches) != 1 or not matches[0]["enabled"] or not window["enabled"]:
            raise ValueError("Menu is missing, ambiguous, disabled, or blocked by a dialog.")
        if not u.PostMessageW(window["hwnd"], 0x111, matches[0]["command_id"], 0):
            raise c.WinError(c.get_last_error())
        print(json.dumps({"sent": matches[0], "pid": args.pid}))
    else:
        print(json.dumps({"pid": args.pid, "windows": rows, "menus": entries}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
