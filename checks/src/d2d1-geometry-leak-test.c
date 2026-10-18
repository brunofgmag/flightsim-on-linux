#define COBJMACROS
#define WIDL_C_INLINE_WRAPPERS
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <d2d1.h>

#define WARMUP 20000
#define GEOMETRIES 300000
#define LEAK_BYTES_PER_GEOMETRY 8.0

static const D2D1_POINT_2F lines[] = { { 10.0f, 0.0f }, { 10.0f, 10.0f }, { 0.0f, 10.0f } };

static BOOL build_and_release(ID2D1Factory *factory)
{
    D2D1_POINT_2F start = { 0.0f, 0.0f };
    ID2D1PathGeometry *geometry;
    ID2D1GeometrySink *sink;
    HRESULT hr;

    if (FAILED(ID2D1Factory_CreatePathGeometry(factory, &geometry)))
        return FALSE;
    if (FAILED(ID2D1PathGeometry_Open(geometry, &sink)))
    {
        ID2D1PathGeometry_Release(geometry);
        return FALSE;
    }
    ID2D1GeometrySink_BeginFigure(sink, start, D2D1_FIGURE_BEGIN_FILLED);
    ID2D1GeometrySink_AddLines(sink, lines, ARRAYSIZE(lines));
    ID2D1GeometrySink_EndFigure(sink, D2D1_FIGURE_END_CLOSED);
    hr = ID2D1GeometrySink_Close(sink);
    ID2D1GeometrySink_Release(sink);
    ID2D1PathGeometry_Release(geometry);
    return SUCCEEDED(hr);
}

static SIZE_T committed_private_bytes(void)
{
    MEMORY_BASIC_INFORMATION info;
    const char *address = NULL;
    SIZE_T total = 0;

    while (VirtualQuery(address, &info, sizeof(info)))
    {
        if (info.State == MEM_COMMIT && info.Type == MEM_PRIVATE)
            total += info.RegionSize;
        address = (const char *)info.BaseAddress + info.RegionSize;
    }
    return total;
}

static BOOL build_many(ID2D1Factory *factory, unsigned int count)
{
    unsigned int i;

    for (i = 0; i < count; i++)
        if (!build_and_release(factory))
        {
            printf("geometry %u failed to build\n", i);
            return FALSE;
        }
    return TRUE;
}

int main(void)
{
    ID2D1Factory *factory;
    SIZE_T before, after;
    double per_geometry;

    if (FAILED(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, &IID_ID2D1Factory, NULL, (void **)&factory)))
    {
        printf("D2D1CreateFactory failed\n");
        return 2;
    }
    if (!build_many(factory, WARMUP))
        return 2;
    before = committed_private_bytes();
    if (!build_many(factory, GEOMETRIES))
        return 2;
    after = committed_private_bytes();
    ID2D1Factory_Release(factory);

    per_geometry = ((double)after - (double)before) / GEOMETRIES;
    printf("%u path geometries built and released, private memory %.1f MB -> %.1f MB, %.1f bytes left per geometry %s\n",
            GEOMETRIES, before / 1048576.0, after / 1048576.0, per_geometry,
            per_geometry > LEAK_BYTES_PER_GEOMETRY ? "FAIL" : "ok");
    return per_geometry > LEAK_BYTES_PER_GEOMETRY ? 1 : 0;
}
