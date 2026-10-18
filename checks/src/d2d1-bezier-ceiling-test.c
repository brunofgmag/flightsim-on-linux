#define COBJMACROS
#define WIDL_C_INLINE_WRAPPERS
#include <windows.h>
#include <stdio.h>
#include <d2d1.h>

#define CLOSE_LIMIT_MS 1000.0
#define FIGURE_COUNT 2

static int take_ceiling_marks(void)
{
    char folder[MAX_PATH], pattern[MAX_PATH], file[MAX_PATH];
    WIN32_FIND_DATAA found;
    HANDLE find;
    DWORD length;
    int marks = 0;

    length = GetTempPathA(sizeof(folder), folder);
    if (!length || length >= sizeof(folder))
        return 0;
    snprintf(pattern, sizeof(pattern), "%sd2d1-teto-%lu-*.txt", folder, GetCurrentProcessId());
    find = FindFirstFileA(pattern, &found);
    if (find == INVALID_HANDLE_VALUE)
        return 0;
    do
    {
        snprintf(file, sizeof(file), "%s%s", folder, found.cFileName);
        if (found.nFileSizeLow)
            ++marks;
        DeleteFileA(file);
    } while (FindNextFileA(find, &found));
    FindClose(find);
    return marks;
}

int main(void)
{
    D2D1_POINT_2F start = { 10.0f, 20.0f };
    D2D1_BEZIER_SEGMENT beziers[2] = {
        { { 60.0f, 0.0f }, { 60.0f, 100.0f }, { 110.0f, 20.0f } },
        { { 60.0f, 50.0f }, { 10.0f, 50.0f }, { 10.0f, 20.0f } },
    };
    ID2D1Factory *factory;
    ID2D1PathGeometry *geometry;
    ID2D1GeometrySink *sink;
    LARGE_INTEGER frequency, begin, end;
    double elapsed_ms;
    HRESULT hr;
    int i, marks, closed, fast;

    if (FAILED(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, &IID_ID2D1Factory, NULL, (void **)&factory))
            || FAILED(ID2D1Factory_CreatePathGeometry(factory, &geometry))
            || FAILED(ID2D1PathGeometry_Open(geometry, &sink)))
    {
        printf("could not create a path geometry\n");
        return 2;
    }
    ID2D1GeometrySink_SetFillMode(sink, D2D1_FILL_MODE_WINDING);
    for (i = 0; i < FIGURE_COUNT; ++i)
    {
        ID2D1GeometrySink_BeginFigure(sink, start, D2D1_FIGURE_BEGIN_FILLED);
        ID2D1GeometrySink_AddBeziers(sink, beziers, ARRAYSIZE(beziers));
        ID2D1GeometrySink_EndFigure(sink, D2D1_FIGURE_END_CLOSED);
    }

    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&begin);
    hr = ID2D1GeometrySink_Close(sink);
    QueryPerformanceCounter(&end);
    elapsed_ms = (end.QuadPart - begin.QuadPart) * 1000.0 / frequency.QuadPart;

    marks = take_ceiling_marks();
    closed = SUCCEEDED(hr);
    fast = elapsed_ms < CLOSE_LIMIT_MS;

    printf("Close of %d identical figures with two Bezier curves each returned %#lx after %.0f ms, limit %.0f ms, %d ceiling mark(s) %s\n",
            FIGURE_COUNT, hr, elapsed_ms, CLOSE_LIMIT_MS, marks, closed && fast && marks ? "ok" : "FAIL");
    if (!closed)
        printf("FAIL: Close did not succeed\n");
    if (!fast)
        printf("FAIL: Close took %.0f ms, the limit is %.0f ms\n", elapsed_ms, CLOSE_LIMIT_MS);
    if (!marks)
        printf("FAIL: the ceiling patch left no d2d1-teto-%lu-*.txt mark in the temp folder\n", GetCurrentProcessId());
    fflush(stdout);
    return closed && fast && marks ? 0 : 1;
}
