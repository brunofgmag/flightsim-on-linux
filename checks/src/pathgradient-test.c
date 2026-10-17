#include <windows.h>
#include <gdiplus.h>
#include <stdio.h>
#include <stdlib.h>

#define SIZE 101

static int failures;

static GpBitmap *render(void (*setup)(GpPathGradient *))
{
    GpPointF corners[4] = { { 0, 0 }, { SIZE, 0 }, { SIZE, SIZE }, { 0, SIZE } };
    ARGB surround = 0xffffffff;
    INT count = 1;
    GpPathGradient *brush;
    GpGraphics *graphics;
    GpBitmap *bitmap;

    GdipCreateBitmapFromScan0(SIZE, SIZE, 0, PixelFormat32bppARGB, NULL, &bitmap);
    GdipGetImageGraphicsContext((GpImage *)bitmap, &graphics);
    GdipCreatePathGradient(corners, 4, WrapModeClamp, &brush);
    GdipSetPathGradientCenterColor(brush, 0xff000000);
    GdipSetPathGradientSurroundColorsWithCount(brush, &surround, &count);
    setup(brush);
    GdipFillRectangleI(graphics, (GpBrush *)brush, 0, 0, SIZE, SIZE);
    GdipDeleteBrush((GpBrush *)brush);
    GdipDeleteGraphics(graphics);
    return bitmap;
}

static void expect(const char *name, GpBitmap *bitmap, INT x, INT y, ARGB wanted, INT tolerance)
{
    ARGB got;
    INT i, worst = 0;

    GdipBitmapGetPixel(bitmap, x, y, &got);
    for (i = 0; i < 32; i += 8)
    {
        INT diff = abs((INT)((got >> i) & 0xff) - (INT)((wanted >> i) & 0xff));
        if (diff > worst)
            worst = diff;
    }
    printf("%-34s (%3d,%3d) wanted %08lx got %08lx %s\n", name, x, y, wanted, got, worst <= tolerance ? "ok" : "FAIL");
    if (worst > tolerance)
        failures++;
}

static void plain(GpPathGradient *brush)
{
}

static void focus(GpPathGradient *brush)
{
    GdipSetPathGradientFocusScales(brush, 0.5f, 0.5f);
}

static void preset(GpPathGradient *brush)
{
    ARGB colors[3] = { 0xffff0000, 0xff00ff00, 0xff0000ff };
    REAL positions[3] = { 0.0f, 0.5f, 1.0f };

    GdipSetPathGradientPresetBlend(brush, colors, positions, 3);
}

static void blend(GpPathGradient *brush)
{
    REAL factors[3] = { 0.0f, 0.0f, 1.0f };
    REAL positions[3] = { 0.0f, 0.8f, 1.0f };

    GdipSetPathGradientBlend(brush, factors, positions, 3);
}

int main(void)
{
    GdiplusStartupInput input = { 1 };
    ULONG_PTR token;
    GpBitmap *bitmap;

    GdiplusStartup(&token, &input, NULL);

    bitmap = render(plain);
    expect("no focus, middle of radius", bitmap, 25, 50, 0xff808080, 12);
    GdipDisposeImage((GpImage *)bitmap);

    bitmap = render(focus);
    expect("focus 0.5, inside focus", bitmap, 35, 50, 0xff000000, 12);
    expect("focus 0.5, between edge and focus", bitmap, 12, 50, 0xff808080, 16);
    GdipDisposeImage((GpImage *)bitmap);

    bitmap = render(preset);
    expect("preset, near edge", bitmap, 1, 50, 0xffff0000, 16);
    expect("preset, middle of radius", bitmap, 25, 50, 0xff00ff00, 16);
    expect("preset, near centre", bitmap, 49, 50, 0xff0000ff, 16);
    GdipDisposeImage((GpImage *)bitmap);

    bitmap = render(blend);
    expect("blend 0 to 0.8, middle of radius", bitmap, 25, 50, 0xffffffff, 12);
    expect("blend 0 to 0.8, near centre", bitmap, 48, 50, 0xff505050, 32);
    GdipDisposeImage((GpImage *)bitmap);

    GdiplusShutdown(token);
    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
