#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#define MAX_RECTS 4096

static int failures;

static double seconds(void)
{
    LARGE_INTEGER now, frequency;

    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&frequency);
    return (double)now.QuadPart / frequency.QuadPart;
}

static RGNDATA *make_data(const RECT *rects, int count)
{
    RGNDATA *data = calloc(1, sizeof(RGNDATAHEADER) + count * sizeof(RECT));

    data->rdh.dwSize = sizeof(RGNDATAHEADER);
    data->rdh.iType = RDH_RECTANGLES;
    data->rdh.nCount = count;
    data->rdh.nRgnSize = count * sizeof(RECT);
    memcpy(data->Buffer, rects, count * sizeof(RECT));
    return data;
}

static HRGN union_one_by_one(const RECT *rects, int count)
{
    HRGN region = CreateRectRgn(0, 0, 0, 0);
    int i;

    for (i = 0; i < count; i++)
    {
        HRGN piece = CreateRectRgnIndirect(&rects[i]);
        CombineRgn(region, region, piece, RGN_OR);
        DeleteObject(piece);
    }
    return region;
}

static BOOL same_rects(HRGN a, HRGN b)
{
    DWORD size_a = GetRegionData(a, 0, NULL), size_b = GetRegionData(b, 0, NULL);
    RGNDATA *data_a, *data_b;
    BOOL same;

    if (size_a != size_b) return FALSE;
    data_a = malloc(size_a);
    data_b = malloc(size_b);
    GetRegionData(a, size_a, data_a);
    GetRegionData(b, size_b, data_b);
    same = data_a->rdh.nCount == data_b->rdh.nCount
        && EqualRect(&data_a->rdh.rcBound, &data_b->rdh.rcBound)
        && !memcmp(data_a->Buffer, data_b->Buffer, data_a->rdh.nCount * sizeof(RECT));
    free(data_a);
    free(data_b);
    return same;
}

static void compare(const char *name, const RECT *rects, int count)
{
    RGNDATA *data = make_data(rects, count);
    HRGN created = ExtCreateRegion(NULL, data->rdh.dwSize + data->rdh.nRgnSize, data);
    HRGN expected = union_one_by_one(rects, count);
    BOOL same = created && same_rects(created, expected);

    printf("%s: %s\n", name, same ? "ok" : "FAIL");
    if (!same) failures++;
    DeleteObject(created);
    DeleteObject(expected);
    free(data);
}

static int circle_rows(RECT *rects, int radius, int split)
{
    int y, n = 0;

    for (y = -radius; y < radius; y++)
    {
        int half = (int)sqrt((double)radius * radius - (y + 0.5) * (y + 0.5));

        if (split && half > 8)
        {
            SetRect(&rects[n++], 500 - half, 500 + y, 496, 500 + y + 1);
            SetRect(&rects[n++], 504, 500 + y, 500 + half, 500 + y + 1);
        }
        else
            SetRect(&rects[n++], 500 - half, 500 + y, 500 + half, 500 + y + 1);
    }
    return n;
}

int main(void)
{
    static RECT rects[MAX_RECTS];
    RGNDATA *data;
    double start, fast;
    int n, i;

    n = circle_rows(rects, 300, 0);
    compare("circle, one span per row", rects, n);
    n = circle_rows(rects, 300, 1);
    compare("circle with a gap, two spans per row", rects, n);

    n = 0;
    for (i = 0; i < 40; i++)
        SetRect(&rects[n++], 10, 100 + i, 90, 101 + i);
    for (i = 0; i < 40; i++)
        SetRect(&rects[n++], 10, 160 + i, 90, 161 + i);
    compare("identical rows with a gap between two blocks", rects, n);

    n = 0;
    SetRect(&rects[n++], 0, 0, 10, 5);
    SetRect(&rects[n++], 10, 0, 20, 5);
    SetRect(&rects[n++], 30, 0, 40, 5);
    SetRect(&rects[n++], 5, 5, 5, 9);
    SetRect(&rects[n++], 0, 5, 20, 9);
    SetRect(&rects[n++], 30, 5, 40, 9);
    compare("touching spans and an empty rectangle", rects, n);

    n = 0;
    SetRect(&rects[n++], 0, 10, 50, 20);
    SetRect(&rects[n++], 0, 0, 50, 10);
    compare("bands out of order", rects, n);

    n = 0;
    SetRect(&rects[n++], 0, 0, 50, 20);
    SetRect(&rects[n++], 20, 10, 80, 30);
    compare("overlapping rectangles", rects, n);

    n = 0;
    SetRect(&rects[n++], 40, 0, 60, 10);
    SetRect(&rects[n++], 0, 0, 20, 10);
    compare("spans out of order in a band", rects, n);

    n = circle_rows(rects, 1000, 0);
    data = make_data(rects, n);
    start = seconds();
    for (i = 0; i < 20; i++)
        DeleteObject(ExtCreateRegion(NULL, data->rdh.dwSize + data->rdh.nRgnSize, data));
    fast = (seconds() - start) / 20;
    printf("ExtCreateRegion of a %d-row circle: %.3f ms, wanted under 1 ms: %s\n", n, fast * 1000,
           fast < 0.001 ? "ok" : "FAIL");
    if (fast >= 0.001) failures++;
    free(data);

    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
