#define COBJMACROS
#define WIDL_C_INLINE_WRAPPERS
#include <windows.h>
#include <math.h>
#include <stdio.h>
#include <d2d1.h>

#define CLOSE_TIMEOUT_MS 15000

struct close_job
{
    ID2D1GeometrySink *sink;
    HRESULT hr;
};

static DWORD WINAPI close_sink(void *arg)
{
    struct close_job *job = arg;

    job->hr = ID2D1GeometrySink_Close(job->sink);
    return 0;
}

int main(void)
{
    D2D1_POINT_2F start = { NAN, NAN };
    D2D1_BEZIER_SEGMENT beziers[2] = {
        { { NAN, NAN }, { NAN, NAN }, { NAN, NAN } },
        { { NAN, NAN }, { NAN, NAN }, { NAN, NAN } },
    };
    ID2D1Factory *factory;
    ID2D1PathGeometry *geometry;
    struct close_job job;
    HANDLE thread;
    DWORD waited;

    if (FAILED(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, &IID_ID2D1Factory, NULL, (void **)&factory))
            || FAILED(ID2D1Factory_CreatePathGeometry(factory, &geometry))
            || FAILED(ID2D1PathGeometry_Open(geometry, &job.sink)))
    {
        printf("could not create a path geometry\n");
        return 2;
    }
    ID2D1GeometrySink_SetFillMode(job.sink, D2D1_FILL_MODE_WINDING);
    ID2D1GeometrySink_BeginFigure(job.sink, start, D2D1_FIGURE_BEGIN_FILLED);
    ID2D1GeometrySink_AddBeziers(job.sink, beziers, ARRAYSIZE(beziers));
    ID2D1GeometrySink_EndFigure(job.sink, D2D1_FIGURE_END_CLOSED);

    thread = CreateThread(NULL, 0, close_sink, &job, 0, NULL);
    waited = WaitForSingleObject(thread, CLOSE_TIMEOUT_MS);
    if (waited != WAIT_OBJECT_0)
    {
        printf("Close of a geometry with NaN coordinates did not return in %u s FAIL\n", CLOSE_TIMEOUT_MS / 1000);
        fflush(stdout);
        ExitProcess(1);
    }
    printf("Close of a geometry with NaN coordinates returned %#lx ok\n", job.hr);
    ID2D1GeometrySink_Release(job.sink);
    ID2D1PathGeometry_Release(geometry);
    ID2D1Factory_Release(factory);
    return 0;
}
