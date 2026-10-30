#include <windows.h>
#include <gdiplus.h>
#include <stdio.h>
#include <stdlib.h>

#define WIDTH 200
#define HEIGHT 100
#define SKY 0xff004680
#define INK 0xffdce6e6

static int failures;

static int count_pixels(const DWORD *bits, DWORD value)
{
    int i, found = 0;

    for (i = 0; i < WIDTH * HEIGHT; i++)
        if (bits[i] == value) found++;
    return found;
}

static void check(const char *name, void (*draw)(GpGraphics *))
{
    BITMAPINFO info = { { sizeof(BITMAPINFOHEADER), WIDTH, -HEIGHT, 1, 32, BI_RGB } };
    HDC screen = GetDC(NULL), parent = CreateCompatibleDC(screen), hdc = CreateCompatibleDC(parent);
    HBITMAP parent_bitmap, bitmap;
    GpGraphics *graphics;
    DWORD *parent_bits, *bits;
    int i, cleared, drawn;

    parent_bitmap = CreateDIBSection(parent, &info, DIB_RGB_COLORS, (void **)&parent_bits, NULL, 0);
    SelectObject(parent, parent_bitmap);
    bitmap = CreateCompatibleBitmap(parent, WIDTH, HEIGHT);
    SelectObject(hdc, bitmap);
    bits = malloc(WIDTH * HEIGHT * 4);
    for (i = 0; i < WIDTH * HEIGHT; i++) bits[i] = SKY;
    SetDIBits(hdc, bitmap, 0, HEIGHT, bits, &info, DIB_RGB_COLORS);

    GdipCreateFromHDC(hdc, &graphics);
    GdipSetCompositingMode(graphics, CompositingModeSourceCopy);
    GdipSetTextRenderingHint(graphics, TextRenderingHintAntiAlias);
    draw(graphics);
    GdipDeleteGraphics(graphics);

    GetDIBits(hdc, bitmap, 0, HEIGHT, bits, &info, DIB_RGB_COLORS);
    cleared = count_pixels(bits, 0);
    drawn = count_pixels(bits, INK);
    printf("%s: %d pixels in the ink colour, %d sky pixels turned transparent black, wanted some ink and none cleared: %s\n",
           name, drawn, cleared, cleared || !drawn ? "FAIL" : "ok");
    if (cleared || !drawn) failures++;

    free(bits);
    DeleteDC(hdc);
    DeleteObject(bitmap);
    DeleteDC(parent);
    DeleteObject(parent_bitmap);
    ReleaseDC(NULL, screen);
}

static void draw_string(GpGraphics *graphics)
{
    GpFontFamily *family;
    GpFont *font;
    GpSolidFill *brush;
    RectF box = { 40, 25, 120, 50 };

    GdipGetGenericFontFamilySansSerif(&family);
    GdipCreateFont(family, 28, FontStyleRegular, UnitPixel, &font);
    GdipCreateSolidFill(INK, &brush);
    GdipDrawString(graphics, L"10", 2, font, &box, NULL, (GpBrush *)brush);
    GdipDeleteBrush((GpBrush *)brush);
    GdipDeleteFont(font);
    GdipDeleteFontFamily(family);
}

static void fill_ellipse(GpGraphics *graphics)
{
    GpSolidFill *brush;

    GdipCreateSolidFill(INK, &brush);
    GdipFillEllipse(graphics, (GpBrush *)brush, 60, 20, 80, 60);
    GdipDeleteBrush((GpBrush *)brush);
}

int main(void)
{
    GdiplusStartupInput input = { 1, NULL, FALSE, FALSE };
    ULONG_PTR token;

    GdiplusStartup(&token, &input, NULL);
    check("SourceCopy string on a 32-bit compatible bitmap (FSLabs display text)", draw_string);
    check("SourceCopy ellipse fill, same graphics", fill_ellipse);
    GdiplusShutdown(token);
    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
