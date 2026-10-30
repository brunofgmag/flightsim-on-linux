#include <windows.h>
#include <stdio.h>

#define FONTS 60
#define ROUNDS 6000

static double seconds(void)
{
    LARGE_INTEGER now, frequency;

    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&frequency);
    return (double)now.QuadPart / frequency.QuadPart;
}

static double draw_cycling(HDC hdc, HFONT *fonts, int count)
{
    double start = seconds();
    int i;

    for (i = 0; i < ROUNDS; i++)
    {
        SelectObject(hdc, fonts[i % count]);
        TextOutW(hdc, 2, 2, L"FL350", 5);
    }
    return seconds() - start;
}

int main(void)
{
    BITMAPINFO info = { { sizeof(BITMAPINFOHEADER), 256, -64, 1, 32, BI_RGB } };
    HFONT fonts[FONTS], original;
    HBITMAP bitmap;
    double two, many;
    void *bits;
    HDC hdc;
    int i;

    hdc = CreateCompatibleDC(NULL);
    bitmap = CreateDIBSection(hdc, &info, DIB_RGB_COLORS, &bits, NULL, 0);
    SelectObject(hdc, bitmap);
    for (i = 0; i < FONTS; i++)
        fonts[i] = CreateFontW(-20, 0, 0, 0, 400 + i, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                               CLIP_DEFAULT_PRECIS, ANTIALIASED_QUALITY, DEFAULT_PITCH, L"Arial");
    original = SelectObject(hdc, fonts[0]);

    draw_cycling(hdc, fonts, FONTS);
    two = draw_cycling(hdc, fonts, 2);
    many = draw_cycling(hdc, fonts, FONTS);

    printf("%d draws alternating 2 fonts: %.1f ms\n", ROUNDS, two * 1000);
    printf("%d draws cycling %d fonts: %.1f ms\n", ROUNDS, FONTS, many * 1000);
    printf("cycling costs %.1fx the two-font time, wanted under 2x\n", many / two);

    SelectObject(hdc, original);
    for (i = 0; i < FONTS; i++)
        DeleteObject(fonts[i]);
    DeleteDC(hdc);
    DeleteObject(bitmap);
    return many / two < 2.0 ? 0 : 1;
}
