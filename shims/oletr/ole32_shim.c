#include <windows.h>
#include <ole2.h>

typedef HRESULT (WINAPI *revoke_drag_drop_fn)(HWND);

HRESULT WINAPI RevokeDragDrop(HWND window)
{
    static revoke_drag_drop_fn ole32_revoke_drag_drop;
    DWORD owner = 0;

    if (IsWindow(window) && GetWindowThreadProcessId(window, &owner) && owner != GetCurrentProcessId())
        return DRAGDROP_E_NOTREGISTERED;

    if (!ole32_revoke_drag_drop)
        ole32_revoke_drag_drop = (revoke_drag_drop_fn)GetProcAddress(LoadLibraryW(L"ole32.dll"), "RevokeDragDrop");
    return ole32_revoke_drag_drop(window);
}
