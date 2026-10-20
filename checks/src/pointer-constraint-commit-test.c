#include <windows.h>
#include <stdio.h>

static BOOL hidden;

static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam)
{
    if (msg == WM_SETCURSOR)
    {
        SetCursor(hidden ? NULL : LoadCursorA(NULL, (LPCSTR)IDC_ARROW));
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
        Sleep(5);
    } while ((LONG)(end - GetTickCount()) > 0);
}

static void set_hidden(BOOL value)
{
    hidden = value;
    SetCursor(hidden ? NULL : LoadCursorA(NULL, (LPCSTR)IDC_ARROW));
}

static void replay_simulator(const RECT *window_rect)
{
    RECT point;
    POINT pos;
    int i;

    GetCursorPos(&pos);
    SetRect(&point, pos.x, pos.y, pos.x + 1, pos.y + 1);

    set_hidden(TRUE);
    ClipCursor(window_rect);
    ClipCursor(&point);
    for (i = 0; i < 3; i++)
    {
        pump(25);
        ShowCursor(FALSE);
        ShowCursor(TRUE);
        ClipCursor(window_rect);
        ClipCursor(&point);
    }
    pump(25);
    ClipCursor(NULL);
    set_hidden(FALSE);
    ClipCursor(NULL);
    ClipCursor(&point);
    pump(25);
    ClipCursor(NULL);
}

int main(void)
{
    WNDCLASSA window_class = {0};
    RECT window_rect;
    HWND hwnd;

    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = GetModuleHandleA(NULL);
    window_class.hbrBackground = GetStockObject(BLACK_BRUSH);
    window_class.lpszClassName = "pointer-constraint-commit";
    RegisterClassA(&window_class);

    hwnd = CreateWindowExA(0, window_class.lpszClassName, "pointer-constraint-commit", WS_POPUP | WS_VISIBLE,
                           100, 100, 400, 300, NULL, NULL, window_class.hInstance, NULL);
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
    GetWindowRect(hwnd, &window_rect);
    replay_simulator(&window_rect);
    pump(100);
    if (GetForegroundWindow() != hwnd)
    {
        printf("the window lost the foreground during the test\n");
        return 2;
    }

    DestroyWindow(hwnd);
    printf("replayed\n");
    return 0;
}
