#include <windows.h>
#include <gdiplus.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#define WIDTH 420
#define HEIGHT 480
#define SKY 0xff2080ffu
#define GROUND 0xff804020u
#define BLACK 0xff000000u

static int failures;

static void check(const char *name, BOOL passed, const char *detail)
{
    printf("%s%s%s %s\n", name, detail ? ": " : "", detail ? detail : "", passed ? "ok" : "FAIL");
    if (!passed)
        failures++;
}

static GpRegion *attitude_window(void)
{
    GpPath *path;
    GpRegion *region, *left, *right;
    GpRectF left_strip = { 79.2f, 175.2f, 16.8f, 129.6f };
    GpRectF right_strip = { 321.6f, 175.2f, 16.8f, 129.6f };

    GdipCreatePath(FillModeAlternate, &path);
    GdipAddPathEllipse(path, 79.2f, 110.4f, 259.2f, 259.2f);
    GdipCreateRegionPath(path, &region);
    GdipCreateRegionRect(&left_strip, &left);
    GdipCreateRegionRect(&right_strip, &right);
    GdipCombineRegionRegion(region, left, CombineModeExclude);
    GdipCombineRegionRegion(region, right, CombineModeExclude);
    GdipDeleteRegion(left);
    GdipDeleteRegion(right);
    GdipDeletePath(path);
    return region;
}

static void fill_sky_and_ground(GpGraphics *graphics)
{
    GpSolidFill *sky, *ground;

    GdipCreateSolidFill(SKY, &sky);
    GdipCreateSolidFill(GROUND, &ground);
    GdipFillRectangle(graphics, (GpBrush *)sky, -129.6f, -518.4f, 259.2f, 518.4f);
    GdipFillRectangle(graphics, (GpBrush *)ground, -129.6f, 0.0f, 259.2f, 518.4f);
    GdipDeleteBrush((GpBrush *)sky);
    GdipDeleteBrush((GpBrush *)ground);
}

static GpBitmap *new_display(GpGraphics **graphics)
{
    GpBitmap *bitmap;

    GdipCreateBitmapFromScan0(WIDTH, HEIGHT, 0, PixelFormat32bppARGB, NULL, &bitmap);
    GdipGetImageGraphicsContext((GpImage *)bitmap, graphics);
    GdipGraphicsClear(*graphics, BLACK);
    return bitmap;
}

static GpBitmap *render_like_fslabs(REAL roll, REAL pitch)
{
    GraphicsContainer outer, rotated, pitched;
    GpGraphics *graphics;
    GpRegion *window = attitude_window();
    GpBitmap *bitmap = new_display(&graphics);

    GdipBeginContainer2(graphics, &outer);
    GdipSetClipRegion(graphics, window, CombineModeReplace);
    GdipTranslateWorldTransform(graphics, 208.8f, 240.0f, MatrixOrderPrepend);
    GdipBeginContainer2(graphics, &rotated);
    GdipRotateWorldTransform(graphics, roll, MatrixOrderAppend);
    GdipBeginContainer2(graphics, &pitched);
    GdipTranslateWorldTransform(graphics, 0.0f, pitch, MatrixOrderAppend);
    fill_sky_and_ground(graphics);
    GdipEndContainer(graphics, pitched);
    GdipEndContainer(graphics, rotated);
    GdipEndContainer(graphics, outer);

    GdipDeleteRegion(window);
    GdipDeleteGraphics(graphics);
    return bitmap;
}

static GpBitmap *render_reference(REAL roll, REAL pitch)
{
    GpGraphics *graphics;
    GpRegion *window = attitude_window();
    GpBitmap *bitmap = new_display(&graphics);

    GdipSetClipRegion(graphics, window, CombineModeReplace);
    GdipTranslateWorldTransform(graphics, 208.8f, 240.0f, MatrixOrderPrepend);
    GdipRotateWorldTransform(graphics, roll, MatrixOrderPrepend);
    GdipTranslateWorldTransform(graphics, 0.0f, pitch, MatrixOrderPrepend);
    fill_sky_and_ground(graphics);

    GdipDeleteRegion(window);
    GdipDeleteGraphics(graphics);
    return bitmap;
}

static void compare_attitude(REAL roll, REAL pitch)
{
    GpBitmap *fslabs = render_like_fslabs(roll, pitch);
    GpBitmap *reference = render_reference(roll, pitch);
    GpRegion *window = attitude_window();
    GpGraphics *graphics;
    INT x, y, inside = 0, black = 0, different = 0;
    char name[64], detail[160];

    GdipGetImageGraphicsContext((GpImage *)reference, &graphics);
    for (y = 0; y < HEIGHT; y++)
        for (x = 0; x < WIDTH; x++)
        {
            BOOL visible;
            ARGB a, b;

            GdipIsVisibleRegionPoint(window, x + 0.5f, y + 0.5f, graphics, &visible);
            GdipBitmapGetPixel(fslabs, x, y, &a);
            GdipBitmapGetPixel(reference, x, y, &b);
            if (a != b)
                different++;
            if (!visible)
                continue;
            inside++;
            if (a == BLACK && b != BLACK)
                black++;
        }
    GdipDeleteGraphics(graphics);

    sprintf(name, "attitude at roll %.0f, pitch %.0f", roll, pitch);
    sprintf(detail, "%d of %d window pixels left black, %d pixels differ from the reference", black, inside, different);
    check(name, black == 0 && different < inside / 100, detail);

    GdipDeleteRegion(window);
    GdipDisposeImage((GpImage *)fslabs);
    GdipDisposeImage((GpImage *)reference);
}

static void transform_inside_container(void)
{
    GraphicsContainer container;
    GpGraphics *graphics;
    GpBitmap *bitmap = new_display(&graphics);
    GpPointF point = { 0.0f, 0.0f };
    char detail[96];

    GdipTranslateWorldTransform(graphics, 100.0f, 80.0f, MatrixOrderAppend);
    GdipBeginContainer2(graphics, &container);
    GdipRotateWorldTransform(graphics, 30.0f, MatrixOrderAppend);
    GdipTransformPoints(graphics, CoordinateSpaceDevice, CoordinateSpaceWorld, &point, 1);
    GdipEndContainer(graphics, container);

    sprintf(detail, "origin at (%.2f, %.2f), wanted (100, 80)", point.X, point.Y);
    check("an appended rotation inside a container comes before the outer translation",
          fabsf(point.X - 100.0f) < 0.01f && fabsf(point.Y - 80.0f) < 0.01f, detail);

    GdipDeleteGraphics(graphics);
    GdipDisposeImage((GpImage *)bitmap);
}

static void reset_inside_container(void)
{
    GraphicsContainer container;
    GpGraphics *graphics;
    GpBitmap *bitmap = new_display(&graphics);
    GpPointF point = { 0.0f, 0.0f };
    char detail[96];

    GdipScaleWorldTransform(graphics, 2.0f, 2.0f, MatrixOrderPrepend);
    GdipTranslateWorldTransform(graphics, 100.0f, 80.0f, MatrixOrderPrepend);
    GdipBeginContainer2(graphics, &container);
    GdipResetWorldTransform(graphics);
    GdipTranslateWorldTransform(graphics, 10.0f, 20.0f, MatrixOrderPrepend);
    GdipTransformPoints(graphics, CoordinateSpaceDevice, CoordinateSpaceWorld, &point, 1);
    GdipEndContainer(graphics, container);

    sprintf(detail, "origin at (%.2f, %.2f), wanted (220, 200)", point.X, point.Y);
    check("a reset inside a container keeps the outer transform (FSLabs ND traffic arrows)",
          fabsf(point.X - 220.0f) < 0.01f && fabsf(point.Y - 200.0f) < 0.01f, detail);

    GdipDeleteGraphics(graphics);
    GdipDisposeImage((GpImage *)bitmap);
}

static void clip_inside_container(void)
{
    GraphicsContainer container;
    GpGraphics *graphics;
    GpBitmap *bitmap = new_display(&graphics);
    GpPath *ellipse;
    BOOL inside_ellipse_outside_rect, inside_both, after_end;

    GdipSetClipRect(graphics, 50.0f, 65.0f, 150.0f, 120.0f, CombineModeReplace);
    GdipBeginContainer2(graphics, &container);
    GdipCreatePath(FillModeAlternate, &ellipse);
    GdipAddPathEllipse(ellipse, 75.0f, 50.0f, 100.0f, 150.0f);
    GdipSetClipPath(graphics, ellipse, CombineModeReplace);
    GdipIsVisiblePoint(graphics, 125.0f, 55.0f, &inside_ellipse_outside_rect);
    GdipIsVisiblePoint(graphics, 125.0f, 125.0f, &inside_both);
    GdipEndContainer(graphics, container);
    GdipIsVisiblePoint(graphics, 60.0f, 70.0f, &after_end);
    GdipDeletePath(ellipse);

    check("a clip replaced inside a container stays inside the outer clip", !inside_ellipse_outside_rect, NULL);
    check("a point inside both clips is visible", inside_both, NULL);
    check("the outer clip is back after EndContainer", after_end, NULL);

    GdipDeleteGraphics(graphics);
    GdipDisposeImage((GpImage *)bitmap);
}

int main(void)
{
    GdiplusStartupInput input = { 1, NULL, FALSE, FALSE };
    ULONG_PTR token;

    GdiplusStartup(&token, &input, NULL);
    compare_attitude(-15.0f, 4.0f);
    compare_attitude(25.0f, 40.0f);
    compare_attitude(0.0f, 0.0f);
    transform_inside_container();
    reset_inside_container();
    clip_inside_container();
    GdiplusShutdown(token);
    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
