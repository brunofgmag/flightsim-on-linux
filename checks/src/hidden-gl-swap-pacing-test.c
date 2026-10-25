#include <windows.h>
#include <GL/gl.h>
#include <stdio.h>

#define SAMPLE_MS 2000

typedef BOOL (WINAPI *swap_interval_proc)(int interval);

static DWORD primary_refresh(void)
{
    DEVMODEW mode = {.dmSize = sizeof(mode)};

    if (EnumDisplaySettingsW(NULL, ENUM_CURRENT_SETTINGS, &mode) && mode.dmDisplayFrequency > 1)
        return mode.dmDisplayFrequency;
    return 60;
}

static HWND create_hidden_window(void)
{
    WNDCLASSW wc = {.lpfnWndProc = DefWindowProcW, .hInstance = GetModuleHandleW(NULL), .lpszClassName = L"hidden-gl-swap"};

    RegisterClassW(&wc);
    return CreateWindowExW(0, wc.lpszClassName, L"", WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN | WS_CLIPSIBLINGS,
                           0, 0, 640, 480, NULL, NULL, wc.hInstance, NULL);
}

static HGLRC create_context(HDC dc)
{
    PIXELFORMATDESCRIPTOR pfd = {
        .nSize = sizeof(pfd),
        .nVersion = 1,
        .dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER,
        .iPixelType = PFD_TYPE_RGBA,
        .cColorBits = 32,
        .cDepthBits = 24,
    };
    int format = ChoosePixelFormat(dc, &pfd);

    if (!format || !SetPixelFormat(dc, format, &pfd)) return NULL;
    return wglCreateContext(dc);
}

int main(void)
{
    HWND hwnd = create_hidden_window();
    HDC dc = hwnd ? GetDC(hwnd) : NULL;
    HGLRC context = dc ? create_context(dc) : NULL;
    swap_interval_proc swap_interval;
    LARGE_INTEGER frequency, start, now;
    DWORD refresh = primary_refresh();
    unsigned int swaps = 0;
    double seconds, rate;

    if (!context || !wglMakeCurrent(dc, context))
    {
        printf("no OpenGL context on a hidden window (error %lu)\n", GetLastError());
        return 2;
    }
    if (!(swap_interval = (swap_interval_proc)wglGetProcAddress("wglSwapIntervalEXT")) || !swap_interval(1))
    {
        printf("wglSwapIntervalEXT(1) is not available\n");
        return 2;
    }

    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&start);
    do
    {
        glClearColor(swaps & 1, 0, 0, 1);
        glClear(GL_COLOR_BUFFER_BIT);
        SwapBuffers(dc);
        swaps++;
        QueryPerformanceCounter(&now);
    } while ((now.QuadPart - start.QuadPart) * 1000 / frequency.QuadPart < SAMPLE_MS);

    seconds = (double)(now.QuadPart - start.QuadPart) / frequency.QuadPart;
    rate = swaps / seconds;
    printf("hidden window with swap interval 1: %.0f swaps a second, primary monitor at %lu Hz\n", rate, refresh);

    wglMakeCurrent(NULL, NULL);
    wglDeleteContext(context);
    ReleaseDC(hwnd, dc);
    DestroyWindow(hwnd);
    return rate <= refresh * 1.25 ? 0 : 1;
}
