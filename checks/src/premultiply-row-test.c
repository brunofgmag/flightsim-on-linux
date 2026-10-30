#include <windows.h>
#include <gdiplus.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BENCH_SIZE 1024
#define BENCH_ROUNDS 20
#define MAX_WIDTH 40
#define OUT_WIDTH (MAX_WIDTH + 4)
#define ROWS 64

static int failures;

static double seconds(void)
{
    LARGE_INTEGER now, frequency;

    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&frequency);
    return (double)now.QuadPart / frequency.QuadPart;
}

__attribute__((optimize("no-tree-vectorize"))) static void reference_premultiply(BYTE *dst, const BYTE *src, int count)
{
    int i;

    for (i = 0; i < count; i++)
    {
        BYTE alpha = src[3];

        *dst++ = (*src++ * alpha + 127) / 255;
        *dst++ = (*src++ * alpha + 127) / 255;
        *dst++ = (*src++ * alpha + 127) / 255;
        *dst++ = *src++;
    }
}

static GpStatus convert(DWORD *pixels, int width, int height, DWORD *out, int out_stride)
{
    GpBitmap *bitmap;
    BitmapData locked;
    GpRect rect = {0, 0, width, height};
    GpStatus status = GdipCreateBitmapFromScan0(width, height, width * 4, PixelFormat32bppARGB, (BYTE *)pixels, &bitmap);

    if (status != Ok) return status;
    locked.Stride = out_stride;
    locked.Scan0 = out;
    status = GdipBitmapLockBits(bitmap, &rect, ImageLockModeRead | ImageLockModeUserInputBuf, PixelFormat32bppPARGB, &locked);
    if (status == Ok) GdipBitmapUnlockBits(bitmap, &locked);
    GdipDisposeImage((GpImage *)bitmap);
    return status;
}

static void compare_every_alpha(void)
{
    static DWORD pixels[65536], out[65536], expected[65536];
    int alpha, colour, wrong;

    for (alpha = 0; alpha < 256; alpha++)
        for (colour = 0; colour < 256; colour++)
            pixels[alpha * 256 + colour] = (DWORD)alpha << 24 | colour << 16 | (colour ^ 0x5a) << 8 | (255 - colour);
    reference_premultiply((BYTE *)expected, (const BYTE *)pixels, 65536);
    wrong = convert(pixels, 256, 256, out, 256 * 4) != Ok || memcmp(out, expected, sizeof(out));
    printf("every alpha and colour: %s\n", wrong ? "FAIL" : "ok");
    if (wrong) failures++;
}

static void compare_every_width(void)
{
    static DWORD pixels[MAX_WIDTH * ROWS], out[OUT_WIDTH * ROWS], expected[OUT_WIDTH * ROWS];
    int width, i, y, wrong = 0;

    for (width = 1; width <= MAX_WIDTH; width++)
    {
        for (i = 0; i < width * ROWS; i++)
        {
            DWORD alpha = rand() & 0xff;

            if (rand() % 3 == 0) alpha = 0;
            else if (rand() % 3 == 0) alpha = 255;
            pixels[i] = alpha << 24 | (((DWORD)rand() << 12 ^ rand()) & 0xffffff);
        }
        memset(out, 0xa5, sizeof(out));
        memset(expected, 0xa5, sizeof(expected));
        for (y = 0; y < ROWS; y++)
            reference_premultiply((BYTE *)(expected + y * OUT_WIDTH), (const BYTE *)(pixels + y * width), width);
        if (convert(pixels, width, ROWS, out, OUT_WIDTH * 4) != Ok
            || memcmp(out, expected, sizeof(out))) wrong++;
    }
    printf("every width up to %d pixels: %s\n", MAX_WIDTH, wrong ? "FAIL" : "ok");
    if (wrong) failures++;
}

static void compare_speed(void)
{
    DWORD *pixels = malloc(BENCH_SIZE * BENCH_SIZE * 4), *out = malloc(BENCH_SIZE * BENCH_SIZE * 4);
    double start, scalar = 1e9, row = 1e9;
    int i;

    for (i = 0; i < BENCH_SIZE * BENCH_SIZE; i++)
        pixels[i] = (DWORD)(1 + rand() % 254) << 24 | (((DWORD)rand() << 12 ^ rand()) & 0xffffff);
    memset(out, 0, BENCH_SIZE * BENCH_SIZE * 4);
    for (i = 0; i < BENCH_ROUNDS; i++)
    {
        start = seconds();
        reference_premultiply((BYTE *)out, (const BYTE *)pixels, BENCH_SIZE * BENCH_SIZE);
        if (seconds() - start < scalar) scalar = seconds() - start;
        start = seconds();
        convert(pixels, BENCH_SIZE, BENCH_SIZE, out, BENCH_SIZE * 4);
        if (seconds() - start < row) row = seconds() - start;
    }
    printf("conversion of %d pixels: %.3f ms, one pixel at a time in C %.3f ms, wanted under 60%%: %s\n",
           BENCH_SIZE * BENCH_SIZE, row * 1000, scalar * 1000, row < scalar * 0.6 ? "ok" : "FAIL");
    if (row >= scalar * 0.6) failures++;
    free(pixels);
    free(out);
}

int main(void)
{
    GdiplusStartupInput input = {1, NULL, FALSE, FALSE};
    ULONG_PTR token;

    GdiplusStartup(&token, &input, NULL);
    srand(20261030);
    compare_every_alpha();
    compare_every_width();
    compare_speed();
    GdiplusShutdown(token);
    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
