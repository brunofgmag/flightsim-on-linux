#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHILD_X 40
#define CHILD_Y 40
#define CHILD_W 240
#define CHILD_H 180
#define SQUARE 60
#define SQUARE_OFFSET 10

static LRESULT CALLBACK parent_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if (msg == WM_PAINT)
    {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(hwnd, &ps);
        HBRUSH blue = CreateSolidBrush(RGB(0, 0, 255));
        FillRect(dc, &ps.rcPaint, blue);
        DeleteObject(blue);
        EndPaint(hwnd, &ps);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static void blit_square(HWND hwnd)
{
    BITMAPINFO info = {{sizeof(BITMAPINFOHEADER), SQUARE, -SQUARE, 1, 32, BI_RGB}};
    void *bits;
    HDC dc = GetDC(hwnd);
    HDC mem = CreateCompatibleDC(dc);
    HBITMAP bitmap = CreateDIBSection(dc, &info, DIB_RGB_COLORS, &bits, NULL, 0);
    for (int i = 0; i < SQUARE * SQUARE; i++) ((DWORD *)bits)[i] = 0x00ffff00;
    HGDIOBJ old = SelectObject(mem, bitmap);
    BitBlt(dc, SQUARE_OFFSET, SQUARE_OFFSET, SQUARE, SQUARE, mem, 0, 0, SRCCOPY);
    SelectObject(mem, old);
    DeleteObject(bitmap);
    DeleteDC(mem);
    ReleaseDC(hwnd, dc);
}

static LRESULT CALLBACK child_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if (msg == WM_PAINT)
    {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(hwnd, &ps);
        HBRUSH red = CreateSolidBrush(RGB(255, 0, 0));
        FillRect(dc, &ps.rcPaint, red);
        DeleteObject(red);
        EndPaint(hwnd, &ps);
        return 0;
    }
    if (msg == WM_TIMER)
    {
        blit_square(hwnd);
        return 0;
    }
    if (msg == WM_DESTROY)
    {
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static HWND create_window(const WCHAR *class_name, WNDPROC proc, DWORD style, int x, int y, int w, int h, HWND parent)
{
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = proc;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.hCursor = LoadCursorW(NULL, (const WCHAR *)IDC_ARROW);
    wc.lpszClassName = class_name;
    RegisterClassW(&wc);
    return CreateWindowExW(0, class_name, class_name, style, x, y, w, h, parent, NULL, wc.hInstance, NULL);
}

static void pump_for(DWORD ms)
{
    DWORD end = GetTickCount() + ms;
    while ((LONG)(end - GetTickCount()) > 0)
    {
        MSG m;
        while (PeekMessageW(&m, NULL, 0, 0, PM_REMOVE)) DispatchMessageW(&m);
        MsgWaitForMultipleObjects(0, NULL, FALSE, 20, QS_ALLINPUT);
    }
}

static COLORREF read_pixel(HWND hwnd, int x, int y)
{
    HDC dc = GetDCEx(hwnd, NULL, DCX_CACHE);
    COLORREF color = GetPixel(dc, x, y);
    ReleaseDC(hwnd, dc);
    return color;
}

static int run_child(HWND parent)
{
    HWND child = create_window(L"cross-process-child", child_proc, WS_CHILD | WS_VISIBLE,
                               CHILD_X, CHILD_Y, CHILD_W, CHILD_H, parent);
    MSG m;

    if (!child)
    {
        printf("child: CreateWindowExW failed, error %lu\n", GetLastError());
        return 2;
    }
    SetTimer(child, 1, 200, NULL);
    while (GetMessageW(&m, NULL, 0, 0) > 0) DispatchMessageW(&m);
    return 0;
}

static int run_parent(void)
{
    HWND parent = create_window(L"cross-process-parent", parent_proc,
                                WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN | WS_VISIBLE, 200, 200, 480, 360, NULL);
    WCHAR exe[MAX_PATH], command[MAX_PATH + 64];
    STARTUPINFOW si = {sizeof(si)};
    PROCESS_INFORMATION pi;
    COLORREF inside, square;
    int ok;

    GetModuleFileNameW(NULL, exe, MAX_PATH);
    swprintf(command, MAX_PATH + 64, L"\"%ls\" child %llu", exe, (unsigned long long)(ULONG_PTR)parent);
    if (!CreateProcessW(exe, command, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
    {
        printf("CreateProcessW failed, error %lu\n", GetLastError());
        return 2;
    }

    pump_for(3000);
    inside = read_pixel(parent, CHILD_X + CHILD_W - 20, CHILD_Y + CHILD_H - 20);
    square = read_pixel(parent, CHILD_X + SQUARE_OFFSET + SQUARE / 2, CHILD_Y + SQUARE_OFFSET + SQUARE / 2);
    ok = inside == RGB(255, 0, 0) && square == RGB(255, 255, 0);
    printf("child window drawn by another process: fill %06lx (want 0000ff), blit %06lx (want 00ffff): %s\n",
           inside, square, ok ? "visible" : "lost");

    DestroyWindow(parent);
    WaitForSingleObject(pi.hProcess, 5000);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return ok ? 0 : 1;
}

int main(int argc, char **argv)
{
    if (argc > 2 && !strcmp(argv[1], "child")) return run_child((HWND)(ULONG_PTR)strtoull(argv[2], NULL, 10));
    return run_parent();
}
