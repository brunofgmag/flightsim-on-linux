#define COBJMACROS
#define INITGUID
#include <windows.h>
#include <stdio.h>
#include <dxgi1_4.h>

static int fail(const char *step, HRESULT hr)
{
    printf("%s failed, hr=0x%08lx\n", step, (unsigned long)hr);
    fflush(stdout);
    return 1;
}

int main(void)
{
    IDXGIFactory1 *factory;
    IDXGIAdapter1 *adapter;
    IDXGIAdapter3 *adapter3;
    DXGI_ADAPTER_DESC1 desc;
    DXGI_QUERY_VIDEO_MEMORY_INFO local;
    HRESULT hr;

    if (FAILED(hr = CreateDXGIFactory1(&IID_IDXGIFactory1, (void **)&factory)))
        return fail("CreateDXGIFactory1", hr);
    if (FAILED(hr = IDXGIFactory1_EnumAdapters1(factory, 0, &adapter)))
        return fail("EnumAdapters1", hr);
    if (FAILED(hr = IDXGIAdapter1_GetDesc1(adapter, &desc)))
        return fail("GetDesc1", hr);
    if (FAILED(hr = IDXGIAdapter1_QueryInterface(adapter, &IID_IDXGIAdapter3, (void **)&adapter3)))
        return fail("QueryInterface(IDXGIAdapter3)", hr);
    if (FAILED(hr = IDXGIAdapter3_QueryVideoMemoryInfo(adapter3, 0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, &local)))
        return fail("QueryVideoMemoryInfo(LOCAL)", hr);

    printf("adapter %ls, %llu MiB dedicated\n", desc.Description, (unsigned long long)(desc.DedicatedVideoMemory >> 20));
    printf("budget %llu MiB\n", (unsigned long long)(local.Budget >> 20));
    fflush(stdout);
    return 0;
}
