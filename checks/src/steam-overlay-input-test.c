#include <windows.h>
#include <stdio.h>

static volatile LONG moves;

static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam)
{
    if (msg == WM_MOUSEMOVE) InterlockedIncrement(&moves);
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

static LONG count_moves(DWORD milliseconds)
{
    pump(100);
    InterlockedExchange(&moves, 0);
    pump(milliseconds);
    return InterlockedExchange(&moves, 0);
}

static void write_result(const char *text)
{
    char path[MAX_PATH], *slash;
    FILE *file;

    GetModuleFileNameA(NULL, path, sizeof(path));
    if ((slash = strrchr(path, '\\'))) strcpy(slash + 1, "steam-overlay-input.txt");
    if ((file = fopen(path, "w")))
    {
        fputs(text, file);
        fclose(file);
    }
}

int main(void)
{
    WNDCLASSA window_class = {0};
    HWND hwnd;
    HANDLE overlay;
    LONG before, during, after;
    char result[256];
    int width = GetSystemMetrics(SM_CXSCREEN), height = GetSystemMetrics(SM_CYSCREEN);

    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = GetModuleHandleA(NULL);
    window_class.hbrBackground = GetStockObject(BLACK_BRUSH);
    window_class.lpszClassName = "steam-overlay-input";
    RegisterClassA(&window_class);

    hwnd = CreateWindowExA(0, window_class.lpszClassName, "steam-overlay-input", WS_POPUP | WS_VISIBLE,
                           0, 0, width, height - 1, NULL, NULL, window_class.hInstance, NULL);
    if (!hwnd)
    {
        write_result("the window did not open\n");
        return 2;
    }
    SetForegroundWindow(hwnd);
    UpdateWindow(hwnd);
    pump(1000);
    if (GetForegroundWindow() != hwnd)
    {
        write_result("the window did not become the foreground window\n");
        return 2;
    }

    overlay = CreateEventW(NULL, TRUE, FALSE, L"__wine_steamclient_GameOverlayActivated");
    if (!overlay)
    {
        write_result("the overlay event could not be created\n");
        return 2;
    }
    before = count_moves(700);
    SetEvent(overlay);
    during = count_moves(700);
    ResetEvent(overlay);
    after = count_moves(700);

    snprintf(result, sizeof(result), "mouse moves the window received: %ld before the overlay opened, %ld while it was open, %ld after it closed\n",
             before, during, after);
    write_result(result);
    DestroyWindow(hwnd);
    if (!before || !after) return 2;
    return during ? 1 : 0;
}
