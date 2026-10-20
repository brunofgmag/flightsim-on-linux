#include <windows.h>
#include <stdio.h>

static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam)
{
    if (msg == WM_SETCURSOR)
    {
        SetCursor(NULL);
        return TRUE;
    }
    return DefWindowProcA(hwnd, msg, wparam, lparam);
}

static void pump(DWORD milliseconds)
{
    DWORD end = GetTickCount() + milliseconds;
    MSG msg;

    while ((LONG)(end - GetTickCount()) > 0)
    {
        while (PeekMessageA(&msg, NULL, 0, 0, PM_REMOVE))
        {
            TranslateMessage(&msg);
            DispatchMessageA(&msg);
        }
        Sleep(10);
    }
}

static void clip_case(const char *name, const RECT *clip)
{
    OutputDebugStringA(name);
    ClipCursor(clip);
    pump(300);
    ClipCursor(NULL);
    pump(300);
}

int main(void)
{
    WNDCLASSA window_class = {0};
    RECT window_rect, small_rect;
    POINT center;
    HWND hwnd;

    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = GetModuleHandleA(NULL);
    window_class.hbrBackground = GetStockObject(BLACK_BRUSH);
    window_class.lpszClassName = "hidden-cursor-clip";
    RegisterClassA(&window_class);

    hwnd = CreateWindowExA(0, window_class.lpszClassName, "hidden-cursor-clip", WS_POPUP | WS_VISIBLE,
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

    SetCursor(NULL);
    ShowCursor(FALSE);
    GetWindowRect(hwnd, &window_rect);
    center.x = (window_rect.left + window_rect.right) / 2;
    center.y = (window_rect.top + window_rect.bottom) / 2;
    SetRect(&small_rect, center.x - 10, center.y - 10, center.x + 10, center.y + 10);

    clip_case("case-whole-window", &window_rect);
    clip_case("case-small-rectangle", &small_rect);
    OutputDebugStringA("case-end");
    if (GetForegroundWindow() != hwnd)
    {
        printf("the window lost the foreground during the test\n");
        return 2;
    }

    DestroyWindow(hwnd);
    printf("ran\n");
    return 0;
}
