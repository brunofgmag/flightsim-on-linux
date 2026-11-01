#include <windows.h>

static const char *marker;

static LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam)
{
    if (message == WM_CLOSE) {
        HANDLE file = CreateFileA(marker, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (file != INVALID_HANDLE_VALUE)
            CloseHandle(file);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcA(window, message, wparam, lparam);
}

int WINAPI WinMain(HINSTANCE instance, HINSTANCE previous, LPSTR command_line, int show)
{
    WNDCLASSA window_class = {0};
    MSG message;

    marker = command_line;
    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = instance;
    window_class.lpszClassName = "fsol_close_window";
    RegisterClassA(&window_class);
    CreateWindowExA(0, "fsol_close_window", "close window", WS_OVERLAPPEDWINDOW, 0, 0, 10, 10, NULL, NULL, instance, NULL);
    while (GetMessageA(&message, NULL, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageA(&message);
    }
    return 0;
}
