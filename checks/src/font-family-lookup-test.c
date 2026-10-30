#include <windows.h>
#include <gdiplus.h>
#include <stdio.h>

#define CALLS 2000

static int failures;

static double seconds(void)
{
    LARGE_INTEGER now, frequency;

    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&frequency);
    return (double)now.QuadPart / frequency.QuadPart;
}

static void time_lookup(const char *what, const WCHAR *name, GpFontCollection *collection)
{
    GpFontFamily *family;
    double start, each;
    int i, missed = 0;

    start = seconds();
    for (i = 0; i < CALLS; i++)
    {
        if (GdipCreateFontFamilyFromName(name, collection, &family) != Ok) missed++;
        else GdipDeleteFontFamily(family);
    }
    each = (seconds() - start) / CALLS;

    printf("%s %ls: %d of %d lookups failed, %.1f us each, wanted under 2 us: %s\n", what, name, missed, CALLS,
           each * 1e6, !missed && each < 2e-6 ? "ok" : "FAIL");
    if (missed || each >= 2e-6) failures++;
}

int wmain(int argc, WCHAR **argv)
{
    GdiplusStartupInput input = { 1, NULL, FALSE, FALSE };
    GpFontCollection *private_fonts;
    GpFontFamily *family;
    WCHAR name[LF_FACESIZE];
    ULONG_PTR token;
    INT found = 0;

    if (argc < 2)
    {
        printf("usage: font-family-lookup-test <font.ttf>\n");
        return 2;
    }

    GdiplusStartup(&token, &input, NULL);

    GdipNewPrivateFontCollection(&private_fonts);
    if (GdipPrivateAddFontFile(private_fonts, argv[1]) != Ok
        || GdipGetFontCollectionFamilyList(private_fonts, 1, &family, &found) != Ok || !found)
    {
        printf("could not load %ls: FAIL\n", argv[1]);
        return 1;
    }
    GdipGetFamilyName(family, name, LANG_NEUTRAL);
    time_lookup("private collection, as the FSLabs displays use it,", name, private_fonts);

    if (GdipGetGenericFontFamilySansSerif(&family) != Ok)
    {
        printf("no sans serif family: FAIL\n");
        return 1;
    }
    GdipGetFamilyName(family, name, LANG_NEUTRAL);
    GdipDeleteFontFamily(family);
    time_lookup("installed fonts", name, NULL);

    if (GdipCreateFontFamilyFromName(L"No Such Family 9F3A", NULL, &family) != FontFamilyNotFound)
    {
        printf("a missing family was found: FAIL\n");
        failures++;
    }
    else
        printf("a missing family is not found: ok\n");

    GdipDeletePrivateFontCollection(&private_fonts);
    GdiplusShutdown(token);
    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
