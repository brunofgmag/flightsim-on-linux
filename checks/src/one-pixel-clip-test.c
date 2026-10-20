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
        Sleep(5);
    } while ((LONG)(end - GetTickCount()) > 0);
}

static void clip_points(const RECT *window_rect)
{
    RECT clip;
    int i;

    SetCursor(LoadCursorA(NULL, (LPCSTR)IDC_ARROW));
    for (i = 0; i < 5; i++)
    {
        SetRect(&clip, window_rect->left + 100 + i, window_rect->top + 100 + i,
                window_rect->left + 101 + i, window_rect->top + 101 + i);
        ClipCursor(&clip);
        pump(30);
    }
    ClipCursor(NULL);
    SetCursorPos(window_rect->left + 300, window_rect->top + 200);
    SetRect(&clip, window_rect->left + 100, window_rect->top + 100,
            window_rect->left + 108, window_rect->top + 108);
    ClipCursor(&clip);
    pump(30);
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
    window_class.lpszClassName = "one-pixel-clip";
    RegisterClassA(&window_class);

    hwnd = CreateWindowExA(0, window_class.lpszClassName, "one-pixel-clip", WS_POPUP | WS_VISIBLE,
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
    clip_points(&window_rect);

    DestroyWindow(hwnd);
    printf("clipped\n");
    return 0;
}
