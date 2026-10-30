#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BENCH_SIZE 1024
#define BENCH_ROUNDS 20

static int failures;

static double seconds(void)
{
    LARGE_INTEGER now, frequency;

    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&frequency);
    return (double)now.QuadPart / frequency.QuadPart;
}

__attribute__((optimize("no-tree-vectorize"))) static DWORD reference_pixel(DWORD dst, DWORD src)
{
    BYTE b = (BYTE)src;
    BYTE g = (BYTE)(src >> 8);
    BYTE r = (BYTE)(src >> 16);
    DWORD alpha = (BYTE)(src >> 24);

    return ((b + ((BYTE)dst * (255 - alpha) + 127) / 255) |
            (g + ((BYTE)(dst >> 8) * (255 - alpha) + 127) / 255) << 8 |
            (r + ((BYTE)(dst >> 16) * (255 - alpha) + 127) / 255) << 16 |
            (alpha + ((BYTE)(dst >> 24) * (255 - alpha) + 127) / 255) << 24);
}

__attribute__((optimize("no-tree-vectorize"))) static void reference_blend(DWORD *dst, const DWORD *src, int count)
{
    int i;

    for (i = 0; i < count; i++) dst[i] = reference_pixel(dst[i], src[i]);
}

static HDC make_dc(int width, int height, DWORD **bits, HBITMAP *bitmap)
{
    BITMAPINFO info;
    HDC dc = CreateCompatibleDC(NULL);

    memset(&info, 0, sizeof(info));
    info.bmiHeader.biSize = sizeof(info.bmiHeader);
    info.bmiHeader.biWidth = width;
    info.bmiHeader.biHeight = -height;
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    *bitmap = CreateDIBSection(dc, &info, DIB_RGB_COLORS, (void **)bits, NULL, 0);
    SelectObject(dc, *bitmap);
    return dc;
}

static BOOL blend(HDC dst, HDC src, int width, int height)
{
    BLENDFUNCTION function = {AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};

    return AlphaBlend(dst, 0, 0, width, height, src, 0, 0, width, height, function);
}

static DWORD random_pixel(int premultiplied)
{
    DWORD alpha = rand() & 0xff, r = rand() & 0xff, g = rand() & 0xff, b = rand() & 0xff;

    if (rand() % 3 == 0) alpha = 0;
    else if (rand() % 3 == 0) alpha = 255;
    if (premultiplied) { r = r * alpha / 255; g = g * alpha / 255; b = b * alpha / 255; }
    return alpha << 24 | r << 16 | g << 8 | b;
}

static void compare_one_blit(const char *name, int premultiplied)
{
    enum { WIDTH = 40, HEIGHT = 200 };
    DWORD *src_bits, *dst_bits;
    static DWORD expected[WIDTH * HEIGHT];
    HBITMAP src_bitmap, dst_bitmap;
    HDC src = make_dc(WIDTH, HEIGHT, &src_bits, &src_bitmap);
    HDC dst = make_dc(WIDTH, HEIGHT, &dst_bits, &dst_bitmap);
    int width, y, i, wrong = 0;

    for (width = 1; width <= WIDTH; width++)
    {
        for (i = 0; i < WIDTH * HEIGHT; i++)
        {
            src_bits[i] = random_pixel(premultiplied);
            dst_bits[i] = (DWORD)rand() << 17 ^ (DWORD)rand() << 2 ^ rand();
        }
        memcpy(expected, dst_bits, sizeof(expected));
        for (y = 0; y < HEIGHT; y++) reference_blend(expected + y * WIDTH, src_bits + y * WIDTH, width);
        if (!blend(dst, src, width, HEIGHT)) wrong++;
        GdiFlush();
        if (memcmp(expected, dst_bits, sizeof(expected))) wrong++;
    }
    printf("%s: %s\n", name, wrong ? "FAIL" : "ok");
    if (wrong) failures++;
    DeleteDC(src);
    DeleteDC(dst);
    DeleteObject(src_bitmap);
    DeleteObject(dst_bitmap);
}

static void compare_every_alpha(void)
{
    enum { WIDTH = 256, HEIGHT = 256 };
    DWORD *src_bits, *dst_bits;
    static DWORD expected[WIDTH * HEIGHT];
    HBITMAP src_bitmap, dst_bitmap;
    HDC src = make_dc(WIDTH, HEIGHT, &src_bits, &src_bitmap);
    HDC dst = make_dc(WIDTH, HEIGHT, &dst_bits, &dst_bitmap);
    int backdrop, alpha, colour, wrong = 0;

    for (backdrop = 0; backdrop < 256; backdrop += 5)
    {
        for (alpha = 0; alpha < 256; alpha++)
            for (colour = 0; colour < 256; colour++)
            {
                src_bits[alpha * WIDTH + colour] = (DWORD)alpha << 24 | colour * 0x010101;
                dst_bits[alpha * WIDTH + colour] = (DWORD)backdrop * 0x01010101;
            }
        memcpy(expected, dst_bits, sizeof(expected));
        reference_blend(expected, src_bits, WIDTH * HEIGHT);
        if (!blend(dst, src, WIDTH, HEIGHT)) wrong++;
        GdiFlush();
        if (memcmp(expected, dst_bits, sizeof(expected))) wrong++;
    }
    printf("every alpha and colour over 52 backdrops: %s\n", wrong ? "FAIL" : "ok");
    if (wrong) failures++;
    DeleteDC(src);
    DeleteDC(dst);
    DeleteObject(src_bitmap);
    DeleteObject(dst_bitmap);
}

static double bench_reference(const DWORD *src, const DWORD *backdrop, DWORD *dst)
{
    double start, best = 1e9;
    int round;

    for (round = 0; round < BENCH_ROUNDS; round++)
    {
        memcpy(dst, backdrop, BENCH_SIZE * BENCH_SIZE * 4);
        start = seconds();
        reference_blend(dst, src, BENCH_SIZE * BENCH_SIZE);
        if (seconds() - start < best) best = seconds() - start;
    }
    return best;
}

static double bench_alpha_blend(HDC dc_dst, HDC dc_src, const DWORD *backdrop, DWORD *dst)
{
    double start, best = 1e9;
    int round;

    for (round = 0; round < BENCH_ROUNDS; round++)
    {
        memcpy(dst, backdrop, BENCH_SIZE * BENCH_SIZE * 4);
        start = seconds();
        blend(dc_dst, dc_src, BENCH_SIZE, BENCH_SIZE);
        GdiFlush();
        if (seconds() - start < best) best = seconds() - start;
    }
    return best;
}

static void compare_speed(void)
{
    DWORD *src_bits, *dst_bits, *backdrop = malloc(BENCH_SIZE * BENCH_SIZE * 4);
    DWORD *reference_dst = malloc(BENCH_SIZE * BENCH_SIZE * 4);
    HBITMAP src_bitmap, dst_bitmap;
    HDC src = make_dc(BENCH_SIZE, BENCH_SIZE, &src_bits, &src_bitmap);
    HDC dst = make_dc(BENCH_SIZE, BENCH_SIZE, &dst_bits, &dst_bitmap);
    double scalar, row;
    int i;

    for (i = 0; i < BENCH_SIZE * BENCH_SIZE; i++)
    {
        DWORD alpha = 1 + rand() % 254, colour = (rand() & 0xff) * alpha / 255;

        src_bits[i] = alpha << 24 | colour * 0x010101;
        backdrop[i] = 0xff000000 | ((DWORD)rand() << 12 ^ rand());
    }
    scalar = bench_reference(src_bits, backdrop, reference_dst);
    row = bench_alpha_blend(dst, src, backdrop, dst_bits);
    printf("AlphaBlend of %d pixels: %.3f ms, one pixel at a time in C %.3f ms, wanted under half: %s\n",
           BENCH_SIZE * BENCH_SIZE, row * 1000, scalar * 1000, row < scalar / 2 ? "ok" : "FAIL");
    if (row >= scalar / 2) failures++;
    DeleteDC(src);
    DeleteDC(dst);
    DeleteObject(src_bitmap);
    DeleteObject(dst_bitmap);
    free(backdrop);
    free(reference_dst);
}

int main(void)
{
    srand(20261030);
    compare_every_alpha();
    compare_one_blit("every width with premultiplied pixels", 1);
    compare_one_blit("every width with colour above alpha, which carries into the next channel", 0);
    compare_speed();
    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
