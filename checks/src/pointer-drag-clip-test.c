#include <windows.h>
#include <stdio.h>

static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam)
{
    if (msg == WM_SETCURSOR)
    {
        SetCursor(LoadCursorA(NULL, (LPCSTR)IDC_ARROW));
        return TRUE;
    }
    return DefWindowProcA(hwnd, msg, wparam, lparam);
}

static void pump(DWORD milliseconds)
{
    DWORD end = GetTickCount() + milliseconds;
    MSG msg;

    do
    {
        while (PeekMessageA(&msg, NULL, 0, 0, PM_REMOVE))
        {
            TranslateMessage(&msg);
            DispatchMessageA(&msg);
        }
        Sleep(1);
    } while ((LONG)(end - GetTickCount()) > 0);
}

static void drag_like_chaseplane(void)
{
    DWORD end = GetTickCount() + 3000;
    RECT point;
    POINT pos;

    GetCursorPos(&pos);
    SetRect(&point, pos.x, pos.y, pos.x + 1, pos.y + 1);
    while ((LONG)(end - GetTickCount()) > 0)
    {
        ClipCursor(&point);
        pump(29);
        ClipCursor(NULL);
        pump(1);
    }
}

int main(void)
{
    WNDCLASSA window_class = {0};
    HWND hwnd;
    int width = GetSystemMetrics(SM_CXSCREEN), height = GetSystemMetrics(SM_CYSCREEN);

    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = GetModuleHandleA(NULL);
    window_class.hbrBackground = GetStockObject(BLACK_BRUSH);
    window_class.lpszClassName = "pointer-drag-clip";
    RegisterClassA(&window_class);

    hwnd = CreateWindowExA(0, window_class.lpszClassName, "pointer-drag-clip", WS_POPUP | WS_VISIBLE,
                           0, 0, width, height - 1, NULL, NULL, window_class.hInstance, NULL);
    if (!hwnd)
    {
        printf("the window did not open\n");
        return 2;
    }
    SetForegroundWindow(hwnd);
    UpdateWindow(hwnd);
    pump(1000);
    if (GetForegroundWindow() != hwnd)
    {
        printf("the window did not become the foreground window\n");
        return 2;
    }
    SetCursorPos(width / 2, height / 2);
    pump(200);
    drag_like_chaseplane();
    if (GetForegroundWindow() != hwnd)
    {
        printf("the window lost the foreground during the test\n");
        return 2;
    }

    DestroyWindow(hwnd);
    printf("dragged\n");
    return 0;
}
