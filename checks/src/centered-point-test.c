#include <windows.h>
#include <gdiplus.h>
#include <stdio.h>

#define WIDTH 200
#define HEIGHT 60
#define CENTRE 100.0f
#define TOLERANCE 1.5f

static int failures;

static void check(const char *name, REAL got)
{
    int ok = got >= CENTRE - TOLERANCE && got <= CENTRE + TOLERANCE;
    printf("%-34s %7.2f (accepts %.2f to %.2f) %s\n", name, got, CENTRE - TOLERANCE, CENTRE + TOLERANCE, ok ? "ok" : "FAIL");
    if (!ok)
        failures++;
}

int wmain(int argc, WCHAR **argv)
{
    GdiplusStartupInput input = { 1 };
    BITMAPINFO info = { { sizeof(BITMAPINFOHEADER), WIDTH, -HEIGHT, 1, 32, BI_RGB } };
    GpRectF point = { CENTRE, 30.0f, 0.0f, 0.0f }, bounds;
    GpFontCollection *collection;
    GpFontFamily *family = NULL;
    GpStringFormat *format;
    GpGraphics *graphics;
    GpBrush *brush;
    GpFont *font;
    ULONG_PTR token;
    HBITMAP dib;
    DWORD *bits;
    INT found = 0, x, y, left = WIDTH, right = -1;
    HDC dc;

    if (argc < 2)
    {
        fwprintf(stderr, L"usage: centered-point-test <font.ttf>\n");
        return 2;
    }

    GdiplusStartup(&token, &input, NULL);
    GdipNewPrivateFontCollection(&collection);
    GdipPrivateAddFontFile(collection, argv[1]);
    GdipGetFontCollectionFamilyList(collection, 1, &family, &found);
    if (!found)
    {
        fwprintf(stderr, L"no font family in %ls\n", argv[1]);
        return 2;
    }
    GdipCreateFont(family, 22.0f, FontStyleRegular, UnitPixel, &font);

    dc = CreateCompatibleDC(NULL);
    dib = CreateDIBSection(dc, &info, DIB_RGB_COLORS, (void **)&bits, NULL, 0);
    SelectObject(dc, dib);
    memset(bits, 0, WIDTH * HEIGHT * 4);

    GdipCreateFromHDC(dc, &graphics);
    GdipSetTextRenderingHint(graphics, TextRenderingHintAntiAlias);
    GdipCreateStringFormat(0, 0, &format);
    GdipSetStringFormatAlign(format, StringAlignmentCenter);
    GdipSetStringFormatLineAlign(format, StringAlignmentCenter);
    GdipCreateSolidFill(0xffffffff, (GpSolidFill **)&brush);

    GdipDrawString(graphics, L"M", 1, font, &point, format, brush);
    GdipMeasureString(graphics, L"M", 1, font, &point, format, &bounds, NULL, NULL);
    GdipDeleteGraphics(graphics);
    GdiFlush();

    for (y = 0; y < HEIGHT; y++)
        for (x = 0; x < WIDTH; x++)
            if ((bits[y * WIDTH + x] & 0xff) > 64)
            {
                if (x < left) left = x;
                if (x > right) right = x;
            }

    check("centre of the drawn M", (left + right + 1) / 2.0f);
    check("centre of MeasureString box", bounds.X + bounds.Width / 2.0f);

    GdipDeleteBrush(brush);
    GdipDeleteStringFormat(format);
    GdipDeleteFont(font);
    GdiplusShutdown(token);
    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
